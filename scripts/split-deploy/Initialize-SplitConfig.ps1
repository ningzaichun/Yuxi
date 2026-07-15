[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$configPairs = @(
    @{
        Template = Join-Path $repoRoot "deploy/split/.env.infra.template"
        Target = Join-Path $repoRoot "deploy/split/.env.infra"
    },
    @{
        Template = Join-Path $repoRoot "deploy/split/.env.app.dev.template"
        Target = Join-Path $repoRoot "deploy/split/.env.app.dev"
    }
)

foreach ($pair in $configPairs) {
    if ((Test-Path -LiteralPath $pair.Target) -and -not $Force) {
        Write-Host "Skip existing config: $($pair.Target)" -ForegroundColor Yellow
        continue
    }

    Copy-Item -LiteralPath $pair.Template -Destination $pair.Target -Force:$Force
    Write-Host "Created config: $($pair.Target)" -ForegroundColor Green
}

Write-Host "Fill all empty connection strings and secrets before starting services." -ForegroundColor Cyan
