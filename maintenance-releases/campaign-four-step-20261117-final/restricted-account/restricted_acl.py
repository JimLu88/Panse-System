"""New SID only. Preserve every existing ACE; never traverse junction targets."""
from pathlib import Path
import os,json
import win32security,ntsecuritycon

FULL=ntsecuritycon.FILE_ALL_ACCESS
REPARSE=0x400
INHERITED=0x10

def plain(path):
    p=Path(path)
    if p.lstat().st_file_attributes & REPARSE:raise ValueError('reparse_target_refused')
    return p

def entries(root):
    root=plain(root);stack=[root]
    while stack:
        folder=stack.pop();yield folder
        with os.scandir(folder) as scan:
            for item in scan:
                stat=item.stat(follow_symlinks=False)
                if stat.st_file_attributes & REPARSE:continue
                if item.is_dir(follow_symlinks=False):stack.append(Path(item.path))
                else:yield Path(item.path)

def deny(path,sid,mask,*,propagate=False,journal=None):
    path=plain(path)
    info=win32security.GetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION)
    old=info.GetSecurityDescriptorDacl()
    if old is None:raise ValueError('null_dacl_requires_manual_review')
    values=[old.GetAce(i) for i in range(old.GetAceCount())]
    for ace in values:
        if ace[0][0]==win32security.ACCESS_DENIED_ACE_TYPE and not ace[0][1]&INHERITED and ace[2]==sid and ace[1]&mask==mask:return False
    if journal:
        # Audit original descriptor before this one-SID change. Restoration must
        # remove only this SID, not overwrite other people's later ACL changes.
        journal.write(json.dumps(dict(path=str(path),before_sddl=win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(info,1,win32security.DACL_SECURITY_INFORMATION)),ensure_ascii=False)+'\n');journal.flush()
    # Round-trip SDDL preserves object/callback ACEs too; pywin32 PyACL has no
    # generic AddAce and rebuilding only Allow/Deny would silently drop others.
    sddl=win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(info,1,win32security.DACL_SECURITY_INFORMATION)
    insert=sddl.find('(')
    if insert<0:insert=len(sddl)
    flags='OICI' if propagate and path.is_dir() else ''
    ace='(D;'+flags+';'+hex(mask)+';;;'+win32security.ConvertSidToStringSid(sid)+')'
    descriptor=win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl[:insert]+ace+sddl[insert:],1)
    # SetNamedSecurityInfo recursively propagates even unchanged inheritable
    # ACEs on a parent such as D:/AI. SetFileSecurity updates this object only;
    # protect_tree explicitly walks the authorized non-junction scope.
    win32security.SetFileSecurity(str(path),win32security.DACL_SECURITY_INFORMATION,descriptor)
    return True

def protect_tree(root,sid,journal):
    count=changed=0
    # Add explicit denies to each object, including protected inheritance and
    # explicit allow exceptions. Enumeration never follows reparse points.
    for path in entries(root):
        changed+=deny(path,sid,FULL,propagate=path.is_dir(),journal=journal);count+=1
        if count%5000==0:print('ACL objects verified: '+str(count),flush=True)
    return dict(root=str(root),objects=count,changed=changed)

def no_access(path,mode):
    try:
        with open(path,mode):pass
    except PermissionError:return True
    raise AssertionError('restricted_identity_unexpected_access')
