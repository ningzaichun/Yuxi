[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$bridgeRoot = Split-Path -Parent $PSScriptRoot
$contracts = @(Get-ChildItem -LiteralPath (Join-Path $bridgeRoot 'schemas') -Filter '*.schema.json' -File |
    Sort-Object Name |
    ForEach-Object { $_.Name })
$commands = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' -File |
    Sort-Object Name |
    ForEach-Object { $_.Name })
$comRegistered = $false
if ($IsWindows) {
    try {
        $comRegistered = $null -ne [type]::GetTypeFromProgID('MSProject.Application', $false)
    }
    catch {
        $comRegistered = $false
    }
}
$payload = [ordered]@{
    schema_version = 'mpp_bridge_version_v1'
    bridge_version = '0.5.0-y0'
    powershell_version = $PSVersionTable.PSVersion.ToString()
    operating_system = [System.Runtime.InteropServices.RuntimeInformation]::OSDescription
    is_windows = [bool]$IsWindows
    microsoft_project_com_registered = $comRegistered
    commands = $commands
    contracts = $contracts
}
$json = $payload | ConvertTo-Json -Depth 10
$schema = Join-Path $bridgeRoot 'schemas/mpp_bridge_version_v1.schema.json'
if (-not ($json | Test-Json -SchemaFile $schema)) {
    throw 'MPP_BRIDGE_VERSION_SCHEMA_INVALID'
}
$json
