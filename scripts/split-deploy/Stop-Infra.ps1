[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$composeFile = Join-Path $repoRoot "deploy/split/docker-compose.infra.yml"
$envFile = Join-Path $repoRoot "deploy/split/.env.infra"

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing $envFile."
}

$composeArgs = @(
    "compose", "--project-name", "yuxi-infra",
    "--project-directory", $repoRoot,
    "--env-file", $envFile,
    "-f", $composeFile
)

# Intentionally omit --volumes so database data is preserved.
& docker @composeArgs down --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw "Failed to stop infrastructure services."
}
