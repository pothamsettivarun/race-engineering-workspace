param([ValidateRange(1,65535)][int]$ListenerPort = 8766, [switch]$Disable)
$ErrorActionPreference = 'Stop'
$taskTailscaleCommand = Get-Command tailscale -ErrorAction SilentlyContinue
$taskTailscalePath = if ($taskTailscaleCommand) { $taskTailscaleCommand.Source } else { Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe' }
if (-not (Test-Path -LiteralPath $taskTailscalePath)) {
    Write-Host 'Install Tailscale from https://tailscale.com/download/windows on both PCs, then run this helper again.'
    exit 1
}
$taskStatusText = & $taskTailscalePath status --json
if ($LASTEXITCODE -ne 0) { throw 'Open Tailscale and sign in, then run this helper again.' }
$taskStatus = $taskStatusText | ConvertFrom-Json
if ($taskStatus.BackendState -ne 'Running') { throw 'Open Tailscale and sign in, then run this helper again.' }
$taskDNSName = if ($taskStatus.Self.DNSName) { $taskStatus.Self.DNSName.TrimEnd('.') } else { '' }
if (-not $taskDNSName.EndsWith('.ts.net')) { throw 'Enable MagicDNS and HTTPS in Tailscale, then run this helper again. Nothing was changed.' }
$taskServeText = & $taskTailscalePath serve status --json
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect existing Tailscale Serve settings. Nothing was changed.' }
$taskServe = $taskServeText | ConvertFrom-Json
$taskTarget = "http://127.0.0.1:$ListenerPort"
$taskOwnPort = if ($taskServe.Web) { @($taskServe.Web.PSObject.Properties | Where-Object { $_.Name.EndsWith(':8443') }) } else { @() }
$taskExistingOwnService = $false
foreach ($taskEntry in $taskOwnPort) {
    $taskHandlers = @($taskEntry.Value.Handlers.PSObject.Properties)
    if ($taskHandlers.Count -ne 1 -or $taskHandlers[0].Name -ne '/' -or $taskHandlers[0].Value.Proxy -ne $taskTarget) {
        throw 'HTTPS port 8443 already serves something else. Nothing was changed; choose a dedicated pitwall port configuration manually.'
    }
    $taskExistingOwnService = $true
}
$taskTCPEntry = if ($taskServe.TCP) { @($taskServe.TCP.PSObject.Properties | Where-Object { $_.Name -eq '8443' }) } else { @() }
if ($taskTCPEntry.Count -gt 0 -and (-not $taskExistingOwnService -or $taskTCPEntry[0].Value.HTTPS -ne $true)) {
    throw 'Port 8443 already has an unrelated forwarding configuration. Nothing was changed.'
}
foreach ($taskFunnelEntry in $(if ($taskServe.AllowFunnel) { @($taskServe.AllowFunnel.PSObject.Properties) } else { @() })) {
    if ($taskFunnelEntry.Name.EndsWith(':8443') -and $taskFunnelEntry.Value) {
        throw 'Public Funnel is enabled on port 8443. Disable that configuration before using private pitwall access. Nothing was changed.'
    }
}
if ($Disable) {
    if ($taskExistingOwnService) {
        & $taskTailscalePath serve --https=8443 off
        if ($LASTEXITCODE -ne 0) { throw 'Could not disable the pitwall Serve configuration.' }
    }
    Write-Host 'Private pitwall Serve is disabled. Other Serve configurations were preserved.'
    exit 0
}
& $taskTailscalePath serve --bg --https=8443 $taskTarget
if ($LASTEXITCODE -ne 0) { throw 'Tailscale Serve needs setup. Follow the HTTPS consent link shown by Tailscale, then run this helper again.' }
Write-Host "Private pitwall address: https://${taskDNSName}:8443"
Write-Host 'In Stint, open Live > Remote pitwall. Refresh status, enable private remote access, and save.'
Write-Host 'Share this PC with the engineer in Tailscale and restrict shared users to TCP 8443 as described in README.md.'
Write-Host 'Pit control stays disarmed until you enable it in Stint for the current driver.'
