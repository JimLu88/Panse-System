"""Recognize a proven local pre-output availability failure, never a write retry."""
from pathlib import Path
import sqlite3

from campaign_entry_authority import load
from campaign_continuous_policy import fingerprint


def can_rebuild(folder, *, allow_owned=False):
    folder=Path(folder).resolve()
    if (folder/'files').exists():return False
    if folder.parent.name!='actions' or folder.parent.parent.parent.name!='segments':return False
    saved=load(folder/'request.json')
    if saved.get('step')!='generate':return False
    root=folder.parents[3]
    with sqlite3.connect((root/'controller.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT a.step,a.status,a.payload_sha,r.owner FROM continuous_campaign_actions a '
            'JOIN continuous_campaign_runs r ON r.id=a.run_id WHERE a.id=?',(folder.name,)).fetchone()
        diagnostic=db.execute('SELECT code FROM continuous_campaign_diagnostics WHERE action_id=? ORDER BY event_id DESC LIMIT 1',
                              (folder.name,)).fetchone()
    db.close()
    if (row is None or row[:3]!=('generate','interrupted_read',fingerprint(saved['payload']))
            or (row[3] is not None and not allow_owned)):
        return False
    if diagnostic==('offer_availability_readback_stale',):
        return (folder/'files-input-context.json').is_file()
    if diagnostic==('backup_alias_provenance_missing',):
        # The old signed alias receipts pinned complete official exports at
        # document level. This particular failure preceded even local file
        # generation; verify those exact export rows before admitting a retry.
        if ((folder/'files-input-context.json').exists()
                or not (folder/'official-mapping-overlay.json').is_file()):
            return False
        from campaign_failure_remediation import verified_code_aliases
        verified_code_aliases(load(folder.parents[1]/'resolved-snapshot.json'))
        return True
    if diagnostic==('TypeError',):
        # The fixed-template projection was written, but the legacy context
        # builder treated a signed exclusion {path,sha256} as a Path. This is
        # still before files/, a bundle, or any platform claim. Verify the
        # exact local inputs and official exclusion before rebuilding.
        if ((folder/'files-input-context.json').exists() or (folder/'claim.json').exists()
                or not (folder/'fixed-projection.json').is_file()
                or not (folder/'fixed-master-current-skus.xlsx').is_file()
                or not (folder/'snapshot-with-id-overlay.json').is_file()
                or not (folder/'official-mapping-overlay.json').is_file()):
            return False
        from campaign_entry_authority import file_sha
        projection=load(folder/'fixed-projection.json')
        if (projection.get('platform_write') is not False
                or projection.get('projection_path')!=str(folder/'fixed-master-current-skus.xlsx')
                or file_sha(projection['projection_path'])!=projection.get('projection_sha256')
                or file_sha(projection['master_path'])!=projection.get('master_sha256')):
            return False
        snapshot=load(folder/'snapshot-with-id-overlay.json')
        overlay=snapshot.get('official_mapping_overlay') or {}
        if (overlay.get('path')!=str(folder/'official-mapping-overlay.json')
                or file_sha(overlay['path'])!=overlay.get('sha256')):
            return False
        refs={tuple(sorted(d['repair']['scope_evidence'].items()))
              for decisions in saved['payload'].get('corrections',{}).values()
              for d in decisions if d.get('repair',{}).get('kind')=='exclude_ineligible_sku'}
        if not refs:return False
        from campaign_failure_remediation import excluded_pairs, mapped_erp_rows
        if not excluded_pairs([dict(ref) for ref in refs]):return False
        mapped_erp_rows(snapshot)
        return True
    return False
