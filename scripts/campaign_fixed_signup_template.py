"""Pinned official super-reduce master. Never silently redownload or overwrite."""
from pathlib import Path
from campaign_entry_authority import file_sha, load
from campaign_official_template import template_rows


def resolve_master(manifest, *, identity, roots):
    path=Path(manifest).resolve(strict=True)
    doc=load(path)
    source=Path(doc['path']).resolve(strict=True)
    if not any(source.is_relative_to(Path(r).resolve()) for r in roots):
        raise ValueError('fixed_signup_template_outside_artifact_roots')
    if doc.get('source')!='official_current_template_download' or file_sha(source)!=doc['sha256']:
        raise ValueError('fixed_signup_template_provenance_or_hash_changed')
    keys=('campaign_id','phase_id','sign_record_id','shop_name')
    if any(doc['identity'].get(k)!=identity.get(k) for k in keys):
        raise ValueError('fixed_signup_template_campaign_or_shop_mismatch')
    # Read the physical package, including its real row range. No synthetic
    # replacement and no change to the separate single-discount master.
    if not template_rows(source.read_bytes()):raise ValueError('fixed_signup_template_has_no_sku_rows')
    return dict(doc,path=str(source),fixed_master=True)
