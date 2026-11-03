#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('install', 'uninstall')][string]$Action = 'install',
    [string]$CopilotHome = $(if ($env:COPILOT_HOME) { $env:COPILOT_HOME } else { Join-Path $HOME '.copilot' }),
    [switch]$AdoptPrototype
)
$ErrorActionPreference = 'Stop'
$homePath = [IO.Path]::GetFullPath($CopilotHome)
$runtime = Join-Path $homePath 'herdr-app-state'
$hook = Join-Path $homePath 'hooks\hc-status.json'
$manifestPath = Join-Path $runtime 'installation.json'
$names = @('status.cjs', 'ConsoleReport.cs', 'ConsoleReport.exe')
$managed = @($names | ForEach-Object { Join-Path $runtime $_ }) + @($hook)

function Read-Bytes([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if ($item.LinkType -or $item.PSIsContainer) { throw "Refusing a linked path or non-file: $Path" }
        return ,([IO.File]::ReadAllBytes($Path))
    }
    return $null
}
function Write-Atomic([string]$Path, [byte[]]$Bytes) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path)) | Out-Null
    $temporary = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllBytes($temporary, $Bytes)
        [IO.File]::Move($temporary, $Path, $true)
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
}
function Encode([string]$Text) { return ,([Text.UTF8Encoding]::new($false).GetBytes($Text)) }
function Hash([byte[]]$Bytes) { [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Bytes)) }

foreach ($directory in @($homePath, $runtime, (Split-Path $hook))) {
    if ((Test-Path -LiteralPath $directory) -and (Get-Item -LiteralPath $directory -Force).LinkType) {
        throw "Refusing a linked installation directory: $directory"
    }
}
$previousBytes = Read-Bytes $manifestPath
$previous = if ($null -ne $previousBytes) { [Text.Encoding]::UTF8.GetString($previousBytes) | ConvertFrom-Json -AsHashtable } else { $null }
if ($previous -and $previous.package -ne 'hc-copilot-hook-windows') { throw 'Unrecognized installation manifest.' }
$originals = [ordered]@{}
foreach ($file in $managed + @($manifestPath)) { $originals[$file] = Read-Bytes $file }
if ($previous) {
    foreach ($file in $managed) {
        $bytes = $originals[$file]
        if ($null -ne $bytes -and (Hash $bytes) -ne $previous.files[[IO.Path]::GetRelativePath($homePath, $file)]) {
            throw "Local edits found; refusing to overwrite/remove: $file"
        }
    }
} elseif ($Action -eq 'uninstall') { throw 'No managed installation; refusing to remove files.' }
elseif (-not $AdoptPrototype) {
    foreach ($file in $managed) {
        if ($null -ne $originals[$file]) { throw "Unmanaged file exists; inspect it first: $file" }
    }
}

$writes = [ordered]@{}
$stage = $null
try {
    if ($Action -eq 'install') {
        $node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        $compiler = @('Microsoft.NET\Framework64\v4.0.30319\csc.exe', 'Microsoft.NET\Framework\v4.0.30319\csc.exe') |
            ForEach-Object { Join-Path $env:WINDIR $_ } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $compiler) { throw 'The existing Windows .NET Framework C# compiler is required; nothing was installed.' }
        $stage = Join-Path ([IO.Path]::GetTempPath()) ('hc-hook-build-' + [guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($stage) | Out-Null
        $exe = Join-Path $stage 'ConsoleReport.exe'
        & $compiler /nologo /target:exe "/out:$exe" (Join-Path $PSScriptRoot 'ConsoleReport.cs')
        if ($LASTEXITCODE -ne 0) { throw 'Console reporter compilation failed; installation was not changed.' }
        foreach ($name in @('status.cjs', 'ConsoleReport.cs')) {
            $writes[(Join-Path $runtime $name)] = [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot $name))
        }
        $writes[(Join-Path $runtime 'ConsoleReport.exe')] = [IO.File]::ReadAllBytes($exe)
        $hooks = [ordered]@{}
        foreach ($event in @('sessionStart', 'userPromptSubmitted', 'postToolUse', 'postToolUseFailure', 'agentStop', 'sessionEnd', 'notification')) {
            $entry = [ordered]@{ type = 'command'; exec = $node; args = @((Join-Path $runtime 'status.cjs'), $event); timeoutSec = 1 }
            if ($event -eq 'notification') { $entry.matcher = 'permission_prompt|elicitation_dialog' }
            $hooks[$event] = @($entry)
        }
        $writes[$hook] = Encode (([ordered]@{ version = 1; hooks = $hooks } | ConvertTo-Json -Depth 8) + "`n")
        $hashes = [ordered]@{}
        foreach ($file in $writes.Keys) { $hashes[[IO.Path]::GetRelativePath($homePath, $file)] = Hash $writes[$file] }
        $manifest = [ordered]@{ package = 'hc-copilot-hook-windows'; version = 1; files = $hashes }
        $writes[$manifestPath] = Encode (($manifest | ConvertTo-Json -Depth 8) + "`n")
    }
    $backup = Join-Path $homePath ('backups\hc-copilot-hook-windows-' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($backup) | Out-Null
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls $backup /inheritance:r /grant:r "*${sid}:(OI)(CI)(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot protect backup permissions; installation was not changed.' }
    $index = [ordered]@{}
    $i = 0
    foreach ($file in $originals.Keys) {
        if ($null -ne $originals[$file]) {
            $name = "$i-" + [IO.Path]::GetFileName($file)
            [IO.File]::WriteAllBytes((Join-Path $backup $name), $originals[$file])
            $index[$name] = $file
            $i++
        }
    }
    Write-Atomic (Join-Path $backup 'index.json') (Encode ($index | ConvertTo-Json))
    foreach ($file in $writes.Keys) { Write-Atomic $file $writes[$file] }
    if ($Action -eq 'uninstall') {
        foreach ($file in $managed + @($manifestPath)) {
            if ($null -ne $originals[$file]) { Remove-Item -LiteralPath $file }
        }
    }
    Write-Host "$Action completed. Hook: $hook"
    Write-Host "Backup: $backup"
    Write-Host 'No profiles, aliases, command wrappers or approval settings were changed.'
    Write-Host 'Exit/resume existing Copilot processes to reload hooks. Herdr App/core need no restart.'
    if ($Action -eq 'install') { Write-Host 'Initial idle before the first message is not supported by hooks-only in CLI 1.0.105.' }
    else { Write-Host 'Runtime metadata, backups and unrelated settings are retained.' }
} finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        if (Test-Path -LiteralPath (Join-Path $stage 'ConsoleReport.exe')) { Remove-Item -LiteralPath (Join-Path $stage 'ConsoleReport.exe') }
        [IO.Directory]::Delete($stage)
    }
}
