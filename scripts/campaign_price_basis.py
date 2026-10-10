"""Resolve only explicitly supplied price receipts; no store scan or network.

The manifest pins source bytes. Historical daily prices are observations, never
silently promoted to a fixed original. Missing data is a row-level data gap,
not a decision to rotate and not a new user-approval gate.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from campaign_reserve_policy import fixed_basis, inherit_basis


def receipt_records(document, path):
    """Existing fixed-receipt schemas, including item_id/sku_id/floor."""
    if not isinstance(document.get("rows"), list):
        raise ValueError("unsupported_receipt_schema:rows_required")
    records = []
    for index, row in enumerate(document["rows"]):
        if "fixed_original_record" not in row:
            continue  # ordinary rows do not assert an original
        item = str(row.get("item") or row.get("item_id") or "")
        sku = str(row.get("sku") or row.get("sku_id") or "")
        code = str(row.get("erp_code") or "")
        if not item or not sku:
            raise ValueError("fixed_record_identity_missing")
        if "fixed_floor" in row and "floor" in row and Decimal(str(row["fixed_floor"])) != Decimal(str(row["floor"])):
            raise ValueError("conflicting_floor_aliases")
        original, floor = fixed_basis(row["fixed_original_record"], row.get("fixed_floor", row.get("floor")))
        basis = dict(original=str(original), floor=str(floor), source=str(path),
                     receipt_row=index, erp_code=code,
                     provenance=row.get("floor_provenance") or row.get("rotation_source") or "explicit_fixed_original_record",
                     first_ever_historical_record_claimed=row.get("first_ever_historical_record_claimed"))
        records.append(dict(item=item, sku=sku, erp_code=code, basis=deepcopy(basis)))
        for lineage in row.get("verified_replacement_lineage", []):
            if (lineage.get("item"), lineage.get("source_sku")) != (item, sku):
                raise ValueError("lineage_source_not_receipt_identity")
            if not code or lineage.get("erp_code") != code:
                raise ValueError("lineage_erp_code_not_receipt_identity")
            basis = inherit_basis(basis, lineage)
            sku = lineage["replacement_sku"]
            records.append(dict(item=item, sku=sku, erp_code=code, basis=deepcopy(basis)))
    return records


class BasisCatalog:
    def __init__(self, sources=(), *, root=Path(".")):
        self.records, self.history, self.sources, self.errors, self.row_errors = [], [], [], [], []
        for source in sources:
            path = Path(source["path"])
            if not path.is_absolute():
                path = root / path
            meta = dict(path=str(path), kind=source.get("kind"), expected_sha256=source.get("sha256"), scope=source.get("scope"))
            self.sources.append(meta)
            try:
                raw = path.read_bytes()
                actual = hashlib.sha256(raw).hexdigest()
                meta["actual_sha256"] = actual
                if not source.get("sha256") or actual != source["sha256"]:
                    raise ValueError("source_version_mismatch")
                document = json.loads(raw.decode("utf-8-sig"))
                if source.get("kind") == "fixed":
                    if not isinstance(document.get("rows"), list):
                        raise ValueError("unsupported_receipt_schema:rows_required")
                    records = []
                    for index, row in enumerate(document["rows"]):
                        try:
                            parsed = receipt_records({"rows": [row]}, path)
                            for record in parsed:
                                record["basis"]["receipt_row"] = index
                            records.extend(parsed)
                        except (ValueError, KeyError, TypeError) as exc:
                            self.row_errors.append(dict(item=str(row.get("item") or row.get("item_id") or ""),
                                                        sku=str(row.get("sku") or row.get("sku_id") or ""),
                                                        source=str(path), error=str(exc)))
                    for record in records:
                        record["basis"]["source_sha256"] = actual
                    self.records.extend(records)
                    meta["loaded_records"] = len(records)
                elif source.get("kind") == "historical":
                    if not isinstance(document.get("rows"), list):
                        raise ValueError("unsupported_history_schema")
                    for row in document["rows"]:
                        self.history.append(dict(row, observation_source=str(path), source_sha256=actual))
                    meta["loaded_records"] = len(document["rows"])
                else:
                    raise ValueError("unsupported_source_kind")
                meta["status"] = "loaded"
            except (OSError, ValueError, TypeError, KeyError) as exc:
                meta.update(status="not_loaded", error=str(exc))
                self.errors.append(meta)

    @classmethod
    def from_manifest(cls, path):
        path = Path(path)
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        return cls(doc["sources"], root=path.parent)

    def resolve(self, row):
        key = str(row["item"]), str(row["sku"])
        code = row["erp_code"]
        matches = [r for r in self.records if (r["item"], r["sku"]) == key]
        history = [r for r in self.history if
                   (str(r.get("item") or r.get("item_id")), str(r.get("sku") or r.get("sku_id"))) == key
                   and (r.get("erp_code") or r.get("sku_code")) == code]
        result = dict(status="not_loaded", basis=None, observations=history,
                      lookup_scope="explicit_manifest_sources_only")
        if not self.sources:
            return result
        errors = [e for e in self.errors if not e.get("scope") or list(key) in e["scope"]]
        row_errors = [e for e in self.row_errors if not e["item"] or not e["sku"] or (e["item"], e["sku"]) == key]
        if row_errors:
            result.update(status="source_conflict", source_errors=row_errors)
            return result
        if errors:
            # A missing listed source could contain a conflicting record.
            result.update(status="source_version_mismatch" if any(e["error"] == "source_version_mismatch" for e in errors) else "not_loaded",
                          source_errors=errors)
            return result
        if matches and any((r["erp_code"] and r["erp_code"] != code) or
                           (not r["erp_code"] and not row.get("mapping_price_version")) for r in matches):
            result["status"] = "source_identity_conflict"
            return result
        values = {(Decimal(r["basis"]["original"]), Decimal(r["basis"]["floor"])) for r in matches}
        if len(values) > 1:
            result.update(status="source_conflict", conflicting_records=matches)
        elif matches:
            basis = deepcopy(matches[0]["basis"])
            if not basis["erp_code"]:
                basis.update(erp_code=code, identity_binding_snapshot_sha256=row["mapping_price_version"])
            result.update(status="confirmed", basis=basis,
                          corroborating_sources=[r["basis"]["source"] for r in matches])
        else:
            result["status"] = "historical_only" if history else "no_record_in_loaded_sources"
        return result
