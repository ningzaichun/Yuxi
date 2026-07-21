[CmdletBinding()]
param(
    [switch]$Force,
    [ValidateSet("All", "Infra", "App", "Host")]
    [string]$Scope = "All"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$configPairs = @(
    @{
        Scope = "Infra"
        Template = Join-Path $repoRoot "deploy/split/.env.infra.template"
        Target = Join-Path $repoRoot "deploy/split/.env.infra"
    },
    @{
        Scope = "App"
        Template = Join-Path $repoRoot "deploy/split/.env.app.template"
        Target = Join-Path $repoRoot "deploy/split/.env.app"
    },
    @{
        Scope = "Host"
        Template = Join-Path $repoRoot "deploy/split/.env.host.dev.template"
        Target = Join-Path $repoRoot ".env"
    },
    @{
        Scope = "Host"
        Template = Join-Path $repoRoot "web/.env.local.example"
        Target = Join-Path $repoRoot "web/.env.local"
    }
)

foreach ($pair in $configPairs) {
    if ($Scope -ne "All" -and $pair.Scope -ne $Scope) {
        continue
    }
    if ((Test-Path -LiteralPath $pair.Target) -and -not $Force) {
        Write-Host "Skip existing config: $($pair.Target)" -ForegroundColor Yellow
        continue
    }

    Copy-Item -LiteralPath $pair.Template -Destination $pair.Target -Force:$Force
    Write-Host "Created config: $($pair.Target)" -ForegroundColor Green
}

Write-Host "Fill all empty remote connection strings and secrets before starting services." -ForegroundColor Cyan
if ($Scope -in @("All", "Host")) {
    Write-Host "The root .env is used by host-native API/Worker and the local Docker Sandbox Provisioner." -ForegroundColor Cyan
}
