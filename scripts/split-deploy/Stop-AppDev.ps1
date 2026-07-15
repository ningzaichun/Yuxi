[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$composeFile = Join-Path $repoRoot "deploy/split/docker-compose.app.dev.yml"
$envFile = Join-Path $repoRoot "deploy/split/.env.app.dev"

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing $envFile."
}

$composeArgs = @(
    "compose", "--project-name", "yuxi-app-dev",
    "--project-directory", $repoRoot,
    "--env-file", $envFile,
    "-f", $composeFile
)

# Intentionally omit --volumes so local development state is preserved.
& docker @composeArgs down --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw "Failed to stop local application services."
}
