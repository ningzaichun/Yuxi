[CmdletBinding()]
param(
    [ValidateSet("All", "Infra", "App")]
    [string]$Scope = "All",
    [switch]$IncludeLogs
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path

function New-ComposeArgs([string]$ProjectName, [string]$ComposeFile, [string]$EnvFile) {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        throw "Missing environment file: $EnvFile"
    }
    return @(
        "compose", "--project-name", $ProjectName,
        "--project-directory", $repoRoot,
        "--env-file", $EnvFile,
        "-f", $ComposeFile
    )
}

function Invoke-ComposeCheck([string[]]$ComposeArgs, [string]$Label) {
    & docker @ComposeArgs config --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "$Label Compose validation failed."
    }
    Write-Host "$Label Compose configuration is valid." -ForegroundColor Green
    & docker @ComposeArgs ps
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read $Label service status."
    }
}

function Get-EnvPort([string]$EnvFile, [string]$Name, [int]$Default) {
    $line = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -match "^$Name=(\d+)$" } | Select-Object -Last 1
    if (-not $line) {
        return $Default
    }
    return [int]($line.Split("=", 2)[1])
}

function Protect-SensitiveText([string]$Text) {
    $masked = $Text -replace '(?i)([a-z][a-z0-9+.-]*://[^:\s/@]+:)[^@\s/]+@', '$1***@'
    return $masked -replace '(?i)((?:[a-z0-9_-]*(?:api[_-]?key|secret|password|token)[a-z0-9_-]*)["'']?\s*[=:]\s*["'']?)[^"''\s,]+', '$1***'
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not available in PATH."
}

if ($Scope -in @("All", "Infra")) {
    $infraArgs = New-ComposeArgs `
        "yuxi-infra" `
        (Join-Path $repoRoot "deploy/split/docker-compose.infra.yml") `
        (Join-Path $repoRoot "deploy/split/.env.infra")
    Invoke-ComposeCheck -ComposeArgs $infraArgs -Label "Infrastructure"
}

if ($Scope -in @("All", "App")) {
    $appEnvFile = Join-Path $repoRoot "deploy/split/.env.app.dev"
    $appArgs = New-ComposeArgs `
        "yuxi-app-dev" `
        (Join-Path $repoRoot "deploy/split/docker-compose.app.dev.yml") `
        $appEnvFile
    Invoke-ComposeCheck -ComposeArgs $appArgs -Label "Application"

    $apiPort = Get-EnvPort $appEnvFile "API_PORT" 5050
    $healthUri = "http://127.0.0.1:$apiPort/api/system/health"
    try {
        Invoke-RestMethod -Method Get -Uri $healthUri -TimeoutSec 15 | Out-Null
        Write-Host "API health check passed: $healthUri" -ForegroundColor Green
    } catch {
        throw "API health check failed at $healthUri. $($_.Exception.Message)"
    }

    if ($IncludeLogs) {
        Write-Host "Recent API/Worker logs (credentials masked):" -ForegroundColor Cyan
        & docker @appArgs logs --tail 40 api worker 2>&1 | ForEach-Object {
            Protect-SensitiveText ($_.ToString())
        }
    }
}
