"""Local artifact adoption, never a submission retry or an Authority unlock.

The context contains business inputs, not software versions. Existing effect
claims stay authoritative even when a local receipt or upload file is missing.
"""
from decimal import Decimal
from pathlib import Path

from campaign_entry_authority import file_sha, load
from campaign_continuous_policy import fingerprint


def input_context(args):
    def ref(path):
        if not path:
            return None
        path = Path(path)
        return {'path': str(path.resolve()), 'sha256': file_sha(path)}

    def items(value):
        return sorted(set(value.split(','))) if value is not None else None

    return dict(schema='campaign_artifact_context_v1',
        campaign=args.campaign_key, start=args.start, end=args.end,
        official_rate=str(Decimal(str(args.official_rate).rstrip('%')) /
                          (100 if str(args.official_rate).endswith('%') else 1)),
        target=args.target, signup_items=items(args.signup_items),
        discount_items=items(args.discount_items), snapshot=ref(args.snapshot),
        template=ref(args.activity_template),
        custom_corrections=ref(getattr(args, 'custom_corrections', None)),
        fixed_basis=[ref(p) for p in args.custom_basis_receipt],
        exclusions=[ref(p) for p in (getattr(args, 'sku_exclusion_receipts', None) or [])],
        time_request=ref(getattr(args, 'time_request', None)),
        time_segment=getattr(args, 'time_segment', None),
        rule_sha=getattr(args, 'continuous_rule_sha', None))


def _legacy_binding(args, body):
    """Adopt pre-context complete bundles only with exact reconstructible inputs."""
    from campaign_segmented_time import bind_request
    from campaign_official_template import discount_rate
    expected = dict(campaign=args.campaign_key, start=args.start, end=args.end,
        target=args.target, snapshot_path=str(Path(args.snapshot).resolve()),
        template_path=str(Path(args.activity_template).resolve()),
        continuous_rule_sha=getattr(args, 'continuous_rule_sha', None))
    if any(body.get(k) != v for k, v in expected.items()):
        raise ValueError('artifact_context_changed')
    if discount_rate(body['official_rate']) != discount_rate(args.official_rate):
        raise ValueError('artifact_context_changed')
    for key in ('signup_items', 'discount_items'):
        value = getattr(args, key)
        if value is None or sorted(set(value.split(','))) != body[key]:
            raise ValueError('legacy_artifact_scope_not_explicit')
    corrections = (load(args.custom_corrections) if getattr(args, 'custom_corrections', None)
                   else {'rows': []})
    if corrections != body.get('corrections'):
        raise ValueError('artifact_corrections_changed')
    if (getattr(args, 'sku_exclusion_receipts', None) or []) != body.get('sku_exclusion_receipts', []):
        raise ValueError('artifact_exclusions_changed')
    timing = (bind_request(args.time_request, args.time_segment)
              if getattr(args, 'time_request', None) else None)
    if timing != body.get('time_binding'):
        raise ValueError('artifact_time_binding_changed')


def ensure_generated(args, authority, *, generator=None, adopt_only=False, record_adoption=True):
    """Generate once, or validate/adopt exactly that original artifact.

    No files/claims are removed on damage. A missing or partial legacy artifact
    needs a separate proven local repair, never a fresh platform submission.
    """
    from campaign_continuous_transport import persist
    from campaign_generate_current_files import _generate
    from campaign_submission_gate import validated_body
    output = Path(args.output_dir)
    context_path = output.parent / (output.name + '-input-context.json')
    context = input_context(args)
    if context_path.exists() and load(context_path) != context:
        raise ValueError('artifact_context_changed')
    if not output.exists():
        if adopt_only:
            raise ValueError('artifact_missing_preserve_effect_claims')
        persist(context_path, context)
        return (generator or _generate)(args, authority)
    receipt = output / 'receipt.json'
    if not receipt.is_file():
        raise ValueError('artifact_incomplete_preserve_effect_claims')
    result = load(receipt)
    if result.get('issues'):
        if not context_path.exists():
            raise ValueError('legacy_input_issues_need_bound_context')
        return result
    bundle = result.get('entry_bundle_id')
    if not bundle:
        raise ValueError('artifact_bundle_missing')
    body = authority.get_bundle(bundle)
    if not context_path.exists():
        _legacy_binding(args, body)
    if result.get('files') != body['files'] or result.get('price_version') != body['price_version']:
        raise ValueError('artifact_receipt_bundle_mismatch')
    if (result.get('activity_rows') != body['signup_rows'] or
            result.get('discount_rows') != body['discount_rows']):
        raise ValueError('artifact_receipt_rows_mismatch')
    for f in body['files']:
        if Path(f['path']).resolve().parent != output.resolve() or file_sha(f['path']) != f['sha256']:
            raise ValueError('artifact_file_missing_or_changed')
    # Reproduce authoritative bytes and recompute price/mapping/offer evidence.
    # This is local computation, not a browser preflight; it never claims.
    for phase in ('signup', 'discount'):
        if body[phase + '_rows']:
            validated_body(authority, bundle, phase)
    if record_adoption:
        persist(context_path, context)
        persist(output.parent / (output.name + '-adoption.json'), dict(
            schema='campaign_artifact_adoption_v1', bundle_id=bundle,
            context_sha256=fingerprint(context), receipt_sha256=file_sha(receipt),
            platform_write=False, authority_claims_changed=False))
    return result
