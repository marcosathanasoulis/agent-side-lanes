param(
    [Parameter(Position=0, Mandatory=$true)]
    [ValidateSet("install", "check", "uninstall")]
    [string]$Mode,
    [Parameter(Position=1)]
    [ValidateSet("codex", "claude", "both")]
    [string]$HostName = "both"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$InstallRoot = Join-Path $env:LOCALAPPDATA "agent-side-lanes"
$ManifestPath = Join-Path $InstallRoot "install-manifest.json"
$RunnerDestination = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\side-lane.cmd"
$CodexDestination = Join-Path $HOME ".agents\skills\claude-side-lane"
$ClaudeDestination = Join-Path $HOME ".claude\skills\codex-side-lane"

function Managed-Items {
    $items = @(@{ Source = (Join-Path $RepoRoot "bin\side-lane"); Destination = $RunnerDestination; Kind = "runner" })
    if ($HostName -eq "codex" -or $HostName -eq "both") {
        $items += @{ Source = (Join-Path $RepoRoot "skills\codex\claude-side-lane"); Destination = $CodexDestination; Kind = "codex" }
    }
    if ($HostName -eq "claude" -or $HostName -eq "both") {
        $items += @{ Source = (Join-Path $RepoRoot "skills\claude\codex-side-lane"); Destination = $ClaudeDestination; Kind = "claude" }
    }
    return $items
}

function Read-Manifest {
    if (-not (Test-Path -LiteralPath $ManifestPath)) { return $null }
    return Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
}

$manifest = Read-Manifest
$items = Managed-Items
if ($Mode -eq "install") {
    foreach ($item in $items) {
        if ((Test-Path -LiteralPath $item.Destination) -and
            -not ($manifest -and ($manifest.destinations -contains $item.Destination))) {
            throw "Refusing unrelated destination: $($item.Destination)"
        }
    }
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    $ownedDestinations = @()
    if ($manifest) { $ownedDestinations += @($manifest.destinations) }
    foreach ($item in $items) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $item.Destination) | Out-Null
        if ($item.Kind -eq "runner") {
            $lines = @("@echo off", ('python "' + $RepoRoot + '\bin\side-lane" %*'))
            Set-Content -LiteralPath $item.Destination -Value $lines -Encoding Ascii
        } else {
            if (Test-Path -LiteralPath $item.Destination) {
                Remove-Item -LiteralPath $item.Destination -Recurse -Force
            }
            Copy-Item -LiteralPath $item.Source -Destination $item.Destination -Recurse -Force
        }
        if (-not ($ownedDestinations -contains $item.Destination)) {
            $ownedDestinations += $item.Destination
        }
    }
    @{ schema_version = 1; source = $RepoRoot; destinations = $ownedDestinations } |
        ConvertTo-Json | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
    Write-Output "install ($HostName) complete"
    exit 0
}
if (-not $manifest) {
    if ($Mode -eq "check") { throw "No managed installation manifest found" }
    Write-Output "Nothing managed to uninstall"
    exit 0
}
if ($Mode -eq "check") {
    foreach ($item in $items) {
        if (-not ($manifest.destinations -contains $item.Destination) -or
            -not (Test-Path -LiteralPath $item.Destination)) {
            throw "Missing or unmanaged destination: $($item.Destination)"
        }
    }
    Write-Output "check ($HostName) complete"
    exit 0
}
foreach ($item in $items) {
    if ($manifest.destinations -contains $item.Destination) {
        Remove-Item -LiteralPath $item.Destination -Recurse -Force
    }
}
$remaining = @($manifest.destinations | Where-Object { Test-Path -LiteralPath $_ })
if ($remaining.Count -eq 0) {
    Remove-Item -LiteralPath $ManifestPath -Force
} else {
    @{ schema_version = 1; source = $manifest.source; destinations = $remaining } |
        ConvertTo-Json | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
}
Write-Output "uninstall ($HostName) complete"
