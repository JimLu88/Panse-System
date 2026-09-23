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
    return False
