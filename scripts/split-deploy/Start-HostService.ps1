[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Api", "Worker", "Web", "Sandbox")]
    [string]$Service
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$envFile = Join-Path $repoRoot ".env"
$backendPath = Join-Path $repoRoot "backend"

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing $Path. Run Initialize-SplitConfig.ps1 first."
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }

        $parts = $line.Split("=", 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (-not $name) {
            continue
        }
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Get-EnvValue([string]$Name, [string]$Default) {
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

Import-DotEnv $envFile
Remove-Item Env:RUNNING_IN_DOCKER -ErrorAction SilentlyContinue

$pythonPaths = @(
    (Join-Path $repoRoot "backend"),
    (Join-Path $repoRoot "backend/package")
)
if ($env:PYTHONPATH) {
    $pythonPaths += $env:PYTHONPATH
}
$env:PYTHONPATH = $pythonPaths -join [IO.Path]::PathSeparator

Set-Location $repoRoot

switch ($Service) {
    "Api" {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw "uv is not available in PATH."
        }
        $hostName = Get-EnvValue "HOST_DEV_API_HOST" "127.0.0.1"
        $port = Get-EnvValue "HOST_DEV_API_PORT" "5050"
        & uv run --project $backendPath uvicorn server.main:app `
            --host $hostName `
            --port $port `
            --reload `
            --reload-dir backend/server `
            --reload-dir backend/package
    }
    "Worker" {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw "uv is not available in PATH."
        }
        & uv run --project $backendPath watchfiles --filter python `
            "arq server.worker_main.WorkerSettings" `
            backend/server backend/package
    }
    "Web" {
        if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
            throw "pnpm is not available in PATH."
        }
        $hostName = Get-EnvValue "HOST_DEV_WEB_HOST" "127.0.0.1"
        $port = Get-EnvValue "HOST_DEV_WEB_PORT" "5173"
        Set-Location (Join-Path $repoRoot "web")
        & pnpm run server -- --host $hostName --port $port
    }
    "Sandbox" {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw "uv is not available in PATH."
        }
        $backend = (Get-EnvValue "PROVISIONER_BACKEND" "docker").ToLowerInvariant()
        if ($backend -eq "docker" -and -not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "Docker is required by PROVISIONER_BACKEND=docker."
        }
        if ([string]::IsNullOrWhiteSpace($env:DOCKER_THREADS_HOST_PATH)) {
            $env:DOCKER_THREADS_HOST_PATH = Join-Path $repoRoot "saves/threads"
        }
        New-Item -ItemType Directory -Force -Path $env:DOCKER_THREADS_HOST_PATH | Out-Null
        $env:DOCKER_SANDBOX_HOST = Get-EnvValue "DOCKER_SANDBOX_HOST" "127.0.0.1"
        $hostName = Get-EnvValue "HOST_DEV_SANDBOX_HOST" "127.0.0.1"
        $port = Get-EnvValue "HOST_DEV_SANDBOX_PORT" "8002"
        & uv run --project $backendPath `
            --with-requirements (Join-Path $repoRoot "docker/sandbox_provisioner/requirements.txt") `
            uvicorn app:app `
            --app-dir (Join-Path $repoRoot "docker/sandbox_provisioner") `
            --host $hostName `
            --port $port
    }
}

if ($LASTEXITCODE -ne 0) {
    throw "$Service process exited with code $LASTEXITCODE."
}
