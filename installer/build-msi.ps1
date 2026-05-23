# build-msi.ps1
# Builds the OctoAssist Agent and packages it into an MSI.
# Run on a Windows host with .NET 8 SDK and WiX 4 dotnet tool installed.
#
# Prereqs:
#   winget install Microsoft.DotNet.SDK.8
#   dotnet tool install --global wix
#   dotnet workload install --skip-sign-check
#
# Usage:
#   PowerShell -ExecutionPolicy Bypass -File .\build-msi.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$AgentDir = Join-Path $RepoRoot "agent"
$InstallerDir = $ScriptDir
$PublishDir = Join-Path $AgentDir "bin\Release\net8.0-windows\win-x64\publish"
$OutDir = Join-Path $RepoRoot "dist"

Write-Host "==> Cleaning"
Remove-Item -Recurse -Force (Join-Path $AgentDir "bin"), (Join-Path $AgentDir "obj") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $InstallerDir "bin"), (Join-Path $InstallerDir "obj") -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $OutDir | Out-Null

Write-Host "==> Publishing OctoAssistAgent (self-contained, single-file, win-x64)"
Push-Location $AgentDir
dotnet publish -c Release -r win-x64 --self-contained true `
    -p:PublishSingleFile=true `
    -p:IncludeNativeLibrariesForSelfExtract=true
Pop-Location

if (-not (Test-Path (Join-Path $PublishDir "OctoAssistAgent.exe"))) {
    throw "Publish failed: OctoAssistAgent.exe not found in $PublishDir"
}

Write-Host "==> Building MSI"
Push-Location $InstallerDir
dotnet build -c Release -p:AgentSourceDir="$PublishDir"
Pop-Location

$Msi = Join-Path $InstallerDir "bin\Release\OctoAssistAgent.msi"
if (-not (Test-Path $Msi)) { throw "MSI not produced at $Msi" }

Copy-Item $Msi (Join-Path $OutDir "OctoAssistAgent.msi") -Force
Write-Host "==> MSI built: $(Join-Path $OutDir 'OctoAssistAgent.msi')"
