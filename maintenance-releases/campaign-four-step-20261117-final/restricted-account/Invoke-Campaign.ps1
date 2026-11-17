[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('capabilities','discover-home','discover-detail','discover-detail-readback','observation','prepare','start','status','finalize','resume','acquire','takeover-status','inspect','click','reload','baseline','upload','collect','release','command-status')][string]$Operation,
    [string]$RequestId, [string]$JobId, [int]$Sequence, [string]$Label,
    [string]$Title, [string]$FactsFile, [string]$ObservationId, [int]$Offset=0,
    [string]$CommandId = ([Guid]::NewGuid().ToString('N'))
)
$ErrorActionPreference='Stop'
if ($CommandId -notmatch '^[0-9a-f]{32}$') { throw 'Invalid command id' }
$Body=@{operation=$Operation;command_id=$CommandId}
if ($RequestId) { $Body.request_id=$RequestId }
if ($JobId) { $Body.job_id=$JobId }
if ($PSBoundParameters.ContainsKey('Sequence')) { $Body.sequence=$Sequence }
if ($Label) { $Body.label=$Label }
if ($Title) { $Body.title=$Title }
if ($FactsFile) { $Body.facts=Get-Content -LiteralPath $FactsFile -Raw -Encoding UTF8 | ConvertFrom-Json }
if ($ObservationId) { $Body.observation_id=$ObservationId; $Body.offset=$Offset }
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class PansePipeClient {
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern SafePipeHandle CreateFile(string name, uint access, uint share, IntPtr security, uint mode, uint flags, IntPtr template);
}
'@
# FILE_READ_DATA|FILE_WRITE_DATA|SYNCHRONIZE, not GENERIC_WRITE which includes
# FILE_CREATE_PIPE_INSTANCE. Identification-only SQOS prevents server impersonation reuse.
$Handle=[PansePipeClient]::CreateFile('\\.\pipe\PanseCampaignExec-v1',0x100003,0,[IntPtr]::Zero,3,0x00110000,[IntPtr]::Zero)
if ($Handle.IsInvalid) { throw 'Restricted campaign broker is not available; do not use the general API' }
$Pipe=[IO.Pipes.NamedPipeClientStream]::new([IO.Pipes.PipeDirection]::InOut,$false,$true,$Handle)
try {
    $Bytes=[Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Compress -Depth 30))
    if ($Bytes.Length -gt 16384) { throw 'Request too large' }
    $Write=$Pipe.WriteAsync($Bytes,0,$Bytes.Length)
    if (-not $Write.Wait(10000)) { throw 'Request outcome unknown; use command-status with the printed command id' }
    $Buffer=New-Object byte[] 131072
    $Read=$Pipe.ReadAsync($Buffer,0,$Buffer.Length)
    if (-not $Read.Wait(10000)) { throw 'Request outcome unknown; use command-status with the printed command id' }
    $Reply=[Text.Encoding]::UTF8.GetString($Buffer,0,$Read.Result)
    $Pipe.WriteByte(1)
    $Reply
} catch {
    @{state='unknown';command_id=$CommandId;automatic_retry=$false;next_action='command-status'} | ConvertTo-Json -Compress
    throw
} finally { $Pipe.Dispose() }
