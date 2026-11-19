"""Execution code identity is separate from immutable business/idempotency keys."""
import hashlib
import json
from pathlib import Path
from campaign_continuous_policy import fingerprint


def sources(root=None):
    explicit=root is not None
    root=Path(root) if explicit else Path(__file__).resolve().parents[1]
    result={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest()
            for pattern in ('scripts/campaign_*.py','docs/campaign-*-contract.json',
                            'docs/campaign-continuous-flow-20260911.json',
                            'docs/campaign-segmented-time-20260911.json')
            for p in root.glob(pattern)}
    if not explicit:
        agent=Path('D:/AI/畔色ERP系统/Web-Agent程序')
        for pattern in ('app/engine/campaign_*.py','app/browser/campaign_edge*.py','app/recorder/campaign_*.py'):
            for p in agent.glob(pattern):
                result[str(p.resolve())]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result


class ReleaseGuard:
    def __init__(self,root=None,checkpoint=None):
        self.root=root;self.original=sources(root);self.release_id=fingerprint(self.original)
        if checkpoint is not None:
            path=Path(checkpoint)
            path.parent.mkdir(parents=True,exist_ok=True)
            body=self.evidence()
            # Content-addressed recovery sources, never an automatic overwrite
            # of a newer runtime. Hash verification detects a damaged archive.
            archive=path.parent/'execution-releases'/self.release_id
            archive.mkdir(parents=True,exist_ok=True)
            for filename,sha in self.original.items():
                target=archive/sha
                if not target.exists():
                    raw=Path(filename).read_bytes()
                    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('execution_source_changed_during_pin')
                    try:
                        with target.open('xb') as stream:stream.write(raw)
                    except FileExistsError:pass
                if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:
                    raise ValueError('pinned_execution_source_archive_corrupted')
            # The request/action identity remains unchanged across program releases.
            # A restart cannot silently adopt a newly deployed source version.
            try:
                with path.open('x',encoding='utf-8') as stream:
                    json.dump(body,stream,ensure_ascii=False,sort_keys=True)
            except FileExistsError:
                if json.loads(path.read_text(encoding='utf-8'))!=body:
                    from campaign_scope_maintenance import validate
                    try:validate(path,body)
                    except (OSError,ValueError,KeyError,TypeError) as exc:
                        raise ValueError('pinned_execution_release_changed_restore_original_version') from exc

    def verify(self):
        if sources(self.root)!=self.original:
            raise ValueError('execution_release_changed_preserve_claims')

    def evidence(self):
        return dict(schema='campaign_execution_release_v1',release_id=self.release_id,
            source_hashes=self.original,business_identity_changed=False)


def recovery_inventory(checkpoint):
    """Reviewable restore manifest; does not alter runtime, claims or requests."""
    path=Path(checkpoint);saved=json.loads(path.read_text(encoding='utf-8'))
    if fingerprint(saved['source_hashes'])!=saved['release_id']:raise ValueError('pinned_release_manifest_changed')
    archive=path.parent/'execution-releases'/saved['release_id'];rows=[]
    for filename,sha in saved['source_hashes'].items():
        source=archive/sha
        if hashlib.sha256(source.read_bytes()).hexdigest()!=sha:raise ValueError('pinned_execution_source_archive_corrupted')
        target=Path(filename)
        current=hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        rows.append(dict(path=filename,pinned_sha256=sha,current_sha256=current,archive=str(source),restore_needed=current!=sha))
    return dict(release_id=saved['release_id'],sources=rows,automatic_restore=False,
                instruction='maintenance_restore_only_when_all_owners_idle')
