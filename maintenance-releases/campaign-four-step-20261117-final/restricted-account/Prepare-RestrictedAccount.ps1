# Creates a DISABLED standard identity and an isolated staging directory only.
# Does not switch Codex, enable login, copy tokens, change services or live ERP ACLs.
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$AccountName = 'PanseCampaignExec'
$StageRoot = 'C:\ProgramData\PanseCampaignExec'
$Description = 'Panse campaign staging; disabled pending audit'
$CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$AdminSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$SystemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$UsersSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')

function Assert-PlainPath([string]$Path) {
    $Item = Get-Item -LiteralPath $Path -Force
    if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point refused' }
}
function Set-StageAcl([string]$Path, [Security.Principal.SecurityIdentifier]$ExecSid, [bool]$Writable) {
    Assert-PlainPath $Path
    $Acl = [Security.AccessControl.DirectorySecurity]::new()
    $Acl.SetAccessRuleProtection($true, $false)
    $Acl.SetOwner($AdminSid)
    foreach ($Identity in @($AdminSid, $SystemSid, $CurrentSid)) {
        $Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $Identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    }
    $Rights = if ($Writable) { 'Modify' } else { 'ReadAndExecute' }
    $Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $ExecSid, $Rights, 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    if ($Writable) {
        # May manage contents, but cannot remove/replace the service-owned folder.
        $Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($ExecSid,'Delete','None','None','Deny'))
    }
    Set-Acl -LiteralPath $Path -AclObject $Acl
}

$Plan = [ordered]@{
    schema='panse-restricted-account-stage-v1'; account=$AccountName; disabled=$true
    directory=$StageRoot; read_only=@('program','receipts'); writable=@('inbox','work')
    group='Users only'; copy_credentials=$false; live_erp_acl_changed=$false
    existing_chat_migrated=$false; hard_permission_isolation=$false
    activation_gate=@('restricted broker deployed','live source/secret denial probes passed','user sets password locally','Codex/business host runs as new identity')
}
if (-not $Apply) { $Plan | ConvertTo-Json -Depth 5; exit 0 }
$Principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator UAC confirmation required' }
Assert-PlainPath 'C:\ProgramData'
$Existing = Get-LocalUser -Name $AccountName -ErrorAction SilentlyContinue
if ($Existing) { throw 'Account already exists; inspect existing receipt, do not reset or overwrite it' }
if (Test-Path -LiteralPath $StageRoot) { throw 'Staging directory already exists; inspect before reuse' }
$Created = $false
try {
    # Never save or display the random bootstrap password. Activation must set a new password locally.
    $Bytes = New-Object byte[] 48
    $Rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $Rng.GetBytes($Bytes); $Rng.Dispose()
    $Secret = ConvertTo-SecureString ('Aa9!' + [Convert]::ToBase64String($Bytes)) -AsPlainText -Force
    [Array]::Clear($Bytes,0,$Bytes.Length)
    $User = New-LocalUser -Name $AccountName -Password $Secret -Disabled -Description $Description
    $Created = $true; $Secret.Dispose(); $Secret = $null
    if (-not @(Get-LocalGroupMember -SID $UsersSid | Where-Object { $_.SID.Value -eq $User.SID.Value }).Count) {
        Add-LocalGroupMember -SID $UsersSid -Member $User
    }
    New-Item -ItemType Directory -Path $StageRoot | Out-Null
    Set-StageAcl $StageRoot $User.SID $false
    foreach ($Leaf in @('program','inbox','work','receipts')) {
        $Path = Join-Path $StageRoot $Leaf
        New-Item -ItemType Directory -Path $Path | Out-Null
        Set-StageAcl $Path $User.SID ($Leaf -in @('inbox','work'))
    }
    Set-Content -LiteralPath (Join-Path $StageRoot 'program\read-only-probe.txt') -Value 'permission probe fixture; contains no business data' -Encoding UTF8
    $Groups = @(Get-LocalGroup | Where-Object {
        $Members = @(Get-LocalGroupMember -Group $_ -ErrorAction Stop)
        @($Members | Where-Object { $_.SID.Value -eq $User.SID.Value }).Count -gt 0
    } | ForEach-Object { $_.SID.Value })
    if ($Groups.Count -ne 1 -or $Groups[0] -ne $UsersSid.Value) { throw 'Unexpected local group membership' }
    $Readback = Get-LocalUser -Name $AccountName
    if ($Readback.Enabled) { throw 'Prepared account must remain disabled' }
    $AclReadback = @()
    foreach ($Leaf in @('','program','inbox','work','receipts')) {
        $Path = if ($Leaf) { Join-Path $StageRoot $Leaf } else { $StageRoot }
        $Acl = Get-Acl -LiteralPath $Path
        if (-not $Acl.AreAccessRulesProtected) { throw 'Staging ACL still inherits broad access' }
        $Rules = @($Acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | Where-Object { $_.IdentityReference.Value -eq $User.SID.Value })
        $Allow = @($Rules | Where-Object AccessControlType -eq 'Allow')
        if ($Allow.Count -ne 1) { throw 'Unexpected executor access rules' }
        $Expected = [Security.AccessControl.FileSystemRights]::ReadAndExecute
        if ($Leaf -in @('inbox','work')) { $Expected = [Security.AccessControl.FileSystemRights]::Modify }
        if (($Allow[0].FileSystemRights -band (-bnot [Security.AccessControl.FileSystemRights]::Synchronize)) -ne $Expected) { throw 'Staging rights readback mismatch' }
        $AclReadback += [ordered]@{path=$Path; protected=$Acl.AreAccessRulesProtected; rights=$Rules | ForEach-Object { [ordered]@{type=[string]$_.AccessControlType; rights=[string]$_.FileSystemRights} }}
    }
    $Receipt = [ordered]@{state='prepared_disabled'; observed_at=[DateTimeOffset]::Now.ToString('o'); account=$AccountName; sid=$User.SID.Value; enabled=$Readback.Enabled; groups=$Groups; acl=$AclReadback; plan=$Plan; real_identity_probe_passed=$false; activation_allowed=$false; hard_permission_isolation=$false}
    $Receipt | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath (Join-Path $StageRoot 'preparation-receipt.json') -Encoding UTF8
} catch {
    if ($Created) { Disable-LocalUser -Name $AccountName -ErrorAction Continue }
    # Keep partial state for inspection; never delete an account or restore broad ACLs automatically.
    throw
}
