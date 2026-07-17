[CmdletBinding()]
param(
    [switch]$SkipWeb,
    [switch]$SkipSandbox
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$envFile = Join-Path $repoRoot ".env"

function Read-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing $Path. Run Initialize-SplitConfig.ps1 first."
    }

    $values = @{}
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }
        $parts = $line.Split("=", 2)
        $values[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
    }
    return $values
}

$config = Read-DotEnv $envFile
$required = @(
    "POSTGRES_URL",
    "REDIS_URL",
    "NEO4J_URI",
    "NEO4J_PASSWORD",
    "MILVUS_URI",
    "MINIO_URI",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "JWT_SECRET_KEY",
    "YUXI_INSTANCE_ID"
)
$missing = @($required | Where-Object { -not $config.ContainsKey($_) -or [string]::IsNullOrWhiteSpace($config[$_]) })
if ($missing.Count -gt 0) {
    throw "Required host development settings are empty: $($missing -join ', ')"
}
Write-Host "Host development configuration contains all required values." -ForegroundColor Green

$apiHost = if ($config["HOST_DEV_API_HOST"]) { $config["HOST_DEV_API_HOST"] } else { "127.0.0.1" }
$apiPort = if ($config["HOST_DEV_API_PORT"]) { $config["HOST_DEV_API_PORT"] } else { "5050" }
$apiHealth = "http://${apiHost}:${apiPort}/api/system/health"
Invoke-RestMethod -Method Get -Uri $apiHealth -TimeoutSec 15 | Out-Null
Write-Host "API health check passed: $apiHealth" -ForegroundColor Green

if (-not $SkipWeb) {
    $webHost = if ($config["HOST_DEV_WEB_HOST"]) { $config["HOST_DEV_WEB_HOST"] } else { "127.0.0.1" }
    $webPort = if ($config["HOST_DEV_WEB_PORT"]) { $config["HOST_DEV_WEB_PORT"] } else { "5173" }
    $webUri = "http://${webHost}:${webPort}"
    Invoke-WebRequest -UseBasicParsing -Uri $webUri -TimeoutSec 15 | Out-Null
    Write-Host "Web check passed: $webUri" -ForegroundColor Green
}

if (-not $SkipSandbox) {
    $sandboxHost = if ($config["HOST_DEV_SANDBOX_HOST"]) { $config["HOST_DEV_SANDBOX_HOST"] } else { "127.0.0.1" }
    $sandboxPort = if ($config["HOST_DEV_SANDBOX_PORT"]) { $config["HOST_DEV_SANDBOX_PORT"] } else { "8002" }
    $sandboxHealth = "http://${sandboxHost}:${sandboxPort}/health"
    Invoke-RestMethod -Method Get -Uri $sandboxHealth -TimeoutSec 15 | Out-Null
    Write-Host "Sandbox Provisioner health check passed: $sandboxHealth" -ForegroundColor Green
}

Write-Host "Worker has no health endpoint; verify its terminal shows ARQ startup and run one Agent task." -ForegroundColor Yellow
