#requires -Version 7.0
$ErrorActionPreference = 'Stop'
function Assert([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$root = Join-Path ([IO.Path]::GetTempPath()) ("hc-windows-test space's-" + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
try {
    $homePath = Join-Path $root 'copilot home'
    [IO.Directory]::CreateDirectory((Join-Path $homePath 'hooks')) | Out-Null
    [IO.File]::WriteAllText((Join-Path $homePath 'settings.json'), '{"defaultPermissionMode":"manual"}')
    [IO.File]::WriteAllText((Join-Path $homePath 'hooks\other.json'), '{"version":1,"hooks":{}}')
    $installer = Join-Path $PSScriptRoot 'install.ps1'
    & $installer -CopilotHome $homePath
    $hook = Join-Path $homePath 'hooks\hc-status.json'
    $originalHook = [IO.File]::ReadAllBytes($hook)
    & $installer -CopilotHome $homePath
    $config = [IO.File]::ReadAllText($hook) | ConvertFrom-Json -AsHashtable
    Assert (-not $config.hooks.Contains('preToolUse') -and -not $config.hooks.Contains('permissionRequest')) 'Approval hook added'
    foreach ($event in $config.hooks.Values) {
        Assert ($event[0].args[0] -eq (Join-Path $homePath 'herdr-app-state\status.cjs')) 'Nonportable hook path'
    }
    $backupIndex = Get-ChildItem (Join-Path $homePath 'backups') -Filter index.json -Recurse |
        Where-Object { ([IO.File]::ReadAllText($_.FullName) | ConvertFrom-Json -AsHashtable).Values -contains $hook } |
        Select-Object -First 1
    Assert ($null -ne $backupIndex) 'Updated hook was not backed up'
    [IO.File]::AppendAllText($hook, "`n")
    $rejected = $false
    try { & $installer -Action uninstall -CopilotHome $homePath } catch { $rejected = $true }
    Assert $rejected 'Modified owned file was removed'
    [IO.File]::WriteAllBytes($hook, $originalHook)
    & $installer -Action uninstall -CopilotHome $homePath
    Assert (-not (Test-Path $hook)) 'Owned hook was not removed'
    Assert ([IO.File]::ReadAllText((Join-Path $homePath 'settings.json')) -eq '{"defaultPermissionMode":"manual"}') 'Settings changed'
    Assert (Test-Path (Join-Path $homePath 'hooks\other.json')) 'Unrelated hook removed'
    [IO.File]::WriteAllText($hook, '{"unmanaged":"keep"}')
    $rejected = $false
    try { & $installer -CopilotHome $homePath } catch { $rejected = $true }
    Assert $rejected 'Unmanaged collision was overwritten'
    Assert ([IO.File]::ReadAllText($hook) -eq '{"unmanaged":"keep"}') 'Collision changed'
    Assert (-not ([IO.File]::ReadAllText($installer) -match '\$PROFILE|function global:copilot')) 'Installer changes the command entry point'
    Write-Host 'Windows install, backup, ownership, collision and uninstall tests passed.'
} finally {
    # Only this test's explicitly allocated temporary directory is removed.
    [IO.Directory]::Delete($root, $true)
}
