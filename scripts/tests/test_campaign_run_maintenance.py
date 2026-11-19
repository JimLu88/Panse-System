import json
from pathlib import Path
import pytest
import campaign_execution_release as release
import campaign_run_maintenance as maintenance
from campaign_continuous_policy import fingerprint
from campaign_entry_authority import file_sha
from test_campaign_scope_maintenance import write


def fixture(tmp_path,monkeypatch):
    request={'fixture':'immutable'};rid=fingerprint(request);root=tmp_path/rid;write(root/'request.json',request)
    program=tmp_path/'program';source=program/'scripts/campaign_fixture.py';source.parent.mkdir(parents=True);source.write_text('old shared')
    paths=[root/'shared/execution-release.json',root/'segments'/('a'*64)/'execution-release.json',
           root/'final-audit'/('b'*64)/('a'*64)/'execution-release.json']
    release.ReleaseGuard(program,paths[0]);source.write_text('old segment')
    for pin in paths[1:]:release.ReleaseGuard(program,pin)
    source.write_text('new reviewed');current=release.sources(program)
    monkeypatch.setattr(release,'sources',lambda root=None:dict(current))
    proof=dict(schema='campaign_reconciled_release_checkpoint_v1',request_id=rid,
               references=[{'path':str(root/'request.json'),'sha256':file_sha(root/'request.json')}])
    monkeypatch.setattr(maintenance,'verify_checkpoint',lambda root:proof)
    review={'request_id':rid,'pins':{str(p.relative_to(root).as_posix()):{
        'old_release_id':json.loads(p.read_text())['release_id'],
        'changes':maintenance.differences(json.loads(p.read_text())['source_hashes'],current)} for p in paths},
        'validation_evidence':[write(tmp_path/'tests.json',{'passed':True})]}
    review_path=tmp_path/'review.json';write(review_path,review)
    return root,paths,review_path,review,current


def test_all_shared_segment_and_final_audit_pins_approved_and_unchanged(tmp_path,monkeypatch):
    root,paths,review,_,_=fixture(tmp_path,monkeypatch);old={p:p.read_bytes() for p in paths}
    result=maintenance.approve_all(root,review)
    assert len(result['pins'])==3
    for pin in paths:release.ReleaseGuard(checkpoint=pin).verify();assert pin.read_bytes()==old[pin]
    assert maintenance.inspect_resume(root)[1]==result['release_id']
    assert maintenance.approve_all(root,review)==result


@pytest.mark.parametrize('case',['missing_segment','wrong_changes','bad_validation','archive'])
def test_incomplete_review_never_approves_any_pin(tmp_path,monkeypatch,case):
    root,paths,review,body,_=fixture(tmp_path,monkeypatch)
    if case=='missing_segment':del body['pins'][paths[1].relative_to(root).as_posix()]
    if case=='wrong_changes':body['pins'][paths[2].relative_to(root).as_posix()]['changes']={}
    if case=='bad_validation':Path(body['validation_evidence'][0]['path']).write_text('changed')
    if case=='archive':next((paths[2].parent/'execution-releases').glob('*/*')).write_text('changed')
    write(review,body)
    with pytest.raises(ValueError):maintenance.approve_all(root,review)
    assert not list(root.rglob('execution-maintenance'))


def test_approval_for_shared_pin_cannot_be_copied_to_segment(tmp_path,monkeypatch):
    from campaign_scope_maintenance import validate_document
    root,paths,review,_,current=fixture(tmp_path,monkeypatch);result=maintenance.approve_all(root,review)
    shared=json.loads(Path(result['pins'][0]['approval']).read_text())
    shared['pin_relative']='shared/execution-release.json'
    body=dict(schema='campaign_execution_release_v1',release_id=fingerprint(current),source_hashes=current,business_identity_changed=False)
    with pytest.raises(ValueError):validate_document(paths[1],body,shared)
