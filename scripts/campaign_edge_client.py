"""ERP program client for fixed retained-Edge jobs, never an AI browser loop.

The caller supplies its configured token in memory. No token/path is logged;
submission is POSTed once, then only the exact returned job is read. Business
acceptance still requires official per-item reconciliation by the controller.
"""
import time
import requests


class EdgeJobError(RuntimeError):
    def __init__(self, reason, job_id=None):
        self.reason, self.job_id = reason, job_id
        super().__init__(reason)


class EdgeClient:
    def __init__(self, token, *, session=None):
        if not token:
            raise ValueError('configured_web_agent_token_required')
        self.session = session or requests.Session()
        self.headers = {'Authorization': 'Bearer '+token}
        self.url = 'http://127.0.0.1:8502/session/action'

    def _action(self, operation, options):
        try:
            response = self.session.post(self.url, headers=self.headers,
                json={'operation': operation, 'options': options}, timeout=(3, 20), allow_redirects=False)
            if response.status_code != 200:
                raise EdgeJobError('dedicated_edge_http_'+str(response.status_code))
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise EdgeJobError('dedicated_edge_transport_uncertain_do_not_resubmit') from exc
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise EdgeJobError(str(result.get('error', 'invalid_edge_result')) if isinstance(result, dict) else 'invalid_edge_result')
        return result

    def submit(self, step, payload):
        return self._action('program_job_submit', {'step': step, 'payload': payload})

    def status(self, job_id):
        return self._action('program_job_status', {'job_id': job_id})

    def wait(self, job_id, *, timeout=300, progress=None):
        deadline = time.monotonic() + min(timeout, 300)
        while True:
            result = self.status(job_id)
            if result.get('job_id') != job_id:
                raise EdgeJobError('edge_job_identity_changed', job_id)
            if progress:
                progress(result)
            if result.get('state') in ('finished', 'unknown', 'interrupted_read'):
                return result
            if result.get('state') != 'running':
                raise EdgeJobError('edge_job_state_invalid', job_id)
            if time.monotonic() >= deadline:
                raise EdgeJobError('edge_job_wait_timeout_job_may_still_be_running', job_id)
            time.sleep(min(2.5, max(0, deadline-time.monotonic())))

    def run(self, step, payload, *, progress=None):
        admitted = self.submit(step, payload)
        if admitted.get('state') != 'running':
            return admitted
        return self.wait(admitted['job_id'], progress=progress)
