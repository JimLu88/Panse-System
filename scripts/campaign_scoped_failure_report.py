"""Keep distinct failed subsets of one official batch immutable and addressable."""
from pathlib import Path
from campaign_entry_authority import load, file_sha


def save(root, folder, report):
    from campaign_continuous_transport import persist
    root, folder = Path(root), Path(folder)
    path = Path(persist(folder / 'failure-report.json', report))
    # Legacy imports still use this first report. Never overwrite it when a
    # partial-discount subset and a wholly failed subset share an offer ID.
    legacy = root / 'reports' / (str(report['batch']) + '.json')
    if not legacy.exists():
        persist(legacy, report)
    return {'path': str(path), 'sha256': file_sha(path)}


def read(root, payload):
    root = Path(root).resolve()
    ref = payload.get('report_ref')
    if ref is None:
        path = root / 'reports' / (str(payload['failed_batch']) + '.json')
        return path, load(path)
    path = Path(ref['path']).resolve()
    if (path.name != 'failure-report.json' or path.parent.parent != root / 'actions'
            or file_sha(path) != ref['sha256']):
        raise ValueError('scoped_failure_report_binding_invalid')
    saved = load(path.parent / 'request.json')
    source = saved.get('payload', {})
    report = load(path)
    if (saved.get('step') != 'report'
            or source.get('identity') != payload.get('identity')
            or source.get('time_binding') != payload.get('time_binding')
            or source.get('batch') != payload['failed_batch']
            or source.get('failed_phase') != payload['failed_phase']
            or report.get('batch') != payload['failed_batch']
            or not set(payload['items']).issubset(set(source.get('items', [])))
            or {e['item'] for e in report['errors']} != set(source.get('items', []))):
        raise ValueError('scoped_failure_report_identity_or_scope_mismatch')
    return path, report
