[CmdletBinding()]
param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

if (-not $SkipDependencyInstall) {
    python -m pip install --requirement "$root\requirements-build.txt"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install build dependencies."
    }
}

python "$root\tools\generate_icon.py"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to generate the application icon."
}

python -m PyInstaller --noconfirm --clean "$root\AppActivity.spec"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

function Get-SignTool {
    $tools = Get-ChildItem `
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" `
        -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending
    return $tools | Select-Object -First 1
}

function Invoke-CodeSigning([string]$Path) {
    if (-not $env:SIGNING_CERTIFICATE_PATH) {
        Write-Warning "No signing certificate configured; $Path will be unsigned."
        return
    }
    $signTool = Get-SignTool
    if (-not $signTool) {
        throw "signtool.exe was not found."
    }
    $arguments = @(
        "sign",
        "/f", $env:SIGNING_CERTIFICATE_PATH,
        "/fd", "SHA256",
        "/tr", "http://timestamp.digicert.com",
        "/td", "SHA256"
    )
    if ($env:SIGNING_CERTIFICATE_PASSWORD) {
        $arguments += @("/p", $env:SIGNING_CERTIFICATE_PASSWORD)
    }
    $arguments += $Path
    & $signTool.FullName @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Code signing failed for $Path."
    }
}

Invoke-CodeSigning "$root\dist\AppActivity\AppActivity.exe"

$innoCandidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $innoCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 was not found. Install it with: winget install JRSoftware.InnoSetup"
}

$versionText = Get-Content "$root\app_activity\__init__.py" -Raw
$version = [regex]::Match($versionText, '__version__ = "([^"]+)"').Groups[1].Value
if (-not $version) {
    throw "Could not determine the App Activity version."
}

& $iscc "/DMyAppVersion=$version" "$root\installer\AppActivity.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed."
}

$installer = "$root\artifacts\AppActivity-Setup.exe"
Invoke-CodeSigning $installer
Write-Output "Built $installer"
