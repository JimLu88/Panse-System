"""Pinned reusable single-discount master. No download or previous batch reuse."""
import hashlib
from pathlib import Path

FIXED_TEMPLATE = Path("D:/AI/畔色ERP系统/活动准备/固定模板/单品立减-SKU级-固定官方模板.xlsx")
FIXED_SHA256 = "dae4da7f875398c7cc99226e129d1211cb849a012d4670a66df88459860f117f"


def load_fixed_discount_template(path=None):
    # Explicit copies are supported only when byte-identical to the user's
    # master. Filled previous batches and the activity template are not masters.
    path = Path(path) if path is not None else FIXED_TEMPLATE
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("fixed_discount_template_unavailable_restore_local_master_no_redownload") from exc
    if hashlib.sha256(raw).hexdigest() != FIXED_SHA256:
        raise ValueError("fixed_discount_template_changed_do_not_redownload_or_use_filled_batch")
    return raw
