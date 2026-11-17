from pathlib import Path
import win32security
from restricted_acl import deny,protect_tree,FULL

def test_real_acl_keeps_existing_aces_and_adds_explicit_deny(tmp_path):
    sid=win32security.ConvertStringSidToSid('S-1-5-21-1-2-3-1009')
    p=tmp_path/'fixture.txt';p.write_text('no private data')
    before=win32security.GetNamedSecurityInfo(str(p),1,4).GetSecurityDescriptorDacl()
    before=[before.GetAce(i) for i in range(before.GetAceCount())]
    assert deny(p,sid,FULL)
    assert not deny(p,sid,FULL)
    after=win32security.GetNamedSecurityInfo(str(p),1,4).GetSecurityDescriptorDacl()
    assert after.GetAce(0)[0][0]==1 and after.GetAce(0)[2]==sid
    assert [after.GetAce(i) for i in range(1,after.GetAceCount())]==before

def test_tree_protects_explicit_allow_and_disabled_inheritance(tmp_path):
    sid=win32security.ConvertStringSidToSid('S-1-5-21-1-2-3-1009')
    root=tmp_path/'tree';root.mkdir();p=root/'fixture.txt';p.write_text('fixture')
    sd=win32security.GetNamedSecurityInfo(str(p),1,4);acl=sd.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(4,0,FULL,sid)
    win32security.SetNamedSecurityInfo(str(p),1,4|win32security.PROTECTED_DACL_SECURITY_INFORMATION,None,None,acl,None)
    with (tmp_path/'journal.jsonl').open('w') as journal:result=protect_tree(root,sid,journal)
    assert result['objects']==2
    acl=win32security.GetNamedSecurityInfo(str(p),1,4).GetSecurityDescriptorDacl()
    assert acl.GetAce(0)[0][0]==1 and acl.GetAce(0)[2]==sid
