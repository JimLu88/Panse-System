"""Execution code identity is separate from immutable business/idempotency keys."""
import hashlib
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
    def __init__(self,root=None):
        self.root=root;self.original=sources(root);self.release_id=fingerprint(self.original)

    def verify(self):
        if sources(self.root)!=self.original:
            raise ValueError('execution_release_changed_preserve_claims')

    def evidence(self):
        return dict(schema='campaign_execution_release_v1',release_id=self.release_id,
            source_hashes=self.original,business_identity_changed=False)
