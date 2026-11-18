[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repositoryRoot

try {
    foreach ($directory in @("build", "dist")) {
        $path = Join-Path $repositoryRoot $directory
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }

    $python = Get-Command python -ErrorAction Stop
    & $python.Source -m pip install -r requirements.txt pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "Installing Python build dependencies failed (exit code $LASTEXITCODE)."
    }

    & $python.Source -m PyInstaller --noconfirm --clean --noconsole installer/hooshyar.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed (exit code $LASTEXITCODE)."
    }

    $graphvizRoot = Join-Path $env:ProgramFiles "Graphviz"
    $dot = Get-ChildItem -Path $graphvizRoot -Filter dot.exe -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $dot) {
        throw "Graphviz was not found under '$graphvizRoot'. Install Graphviz before building."
    }

    $graphvizInstallRoot = Split-Path (Split-Path $dot.FullName -Parent) -Parent
    $graphvizDestination = Join-Path $repositoryRoot "dist/Hooshyar/graphviz"
    New-Item -ItemType Directory -Path $graphvizDestination -Force | Out-Null
    Copy-Item -Path (Join-Path $graphvizInstallRoot "*") -Destination $graphvizDestination -Recurse -Force

    $innoCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6/ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6/ISCC.exe")
    )
    $iscc = $innoCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        throw "Inno Setup 6 was not found. Install Inno Setup before building."
    }

    & $iscc "/DAppVersion=$Version" "installer/hooshyar.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed (exit code $LASTEXITCODE)."
    }

    $installer = Join-Path $repositoryRoot "installer-output/HOOSHYAR-Setup.exe"
    if (-not (Test-Path $installer)) {
        throw "The installer was not created at '$installer'."
    }
    Write-Output "Installer created: $installer (version $Version)"
}
finally {
    Pop-Location
}
