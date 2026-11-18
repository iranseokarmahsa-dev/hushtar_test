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

    & $python.Source -m PyInstaller --noconfirm --clean installer/hooshyar.spec
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

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()

    $env:HOOSHYAR_PORT = "$port"
    $env:HOOSHYAR_NO_BROWSER = "1"
    $env:HOOSHYAR_DATA_DIR = Join-Path $env:TEMP "Hooshyar-build-smoke-$PID"
    $executable = Join-Path $repositoryRoot "dist/Hooshyar/Hooshyar.exe"
    $process = Start-Process -FilePath $executable -PassThru
    $healthy = $false
    $lastError = "No response received."
    try {
        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline) {
            $process.Refresh()
            if ($process.HasExited) {
                throw "Packaged Hooshyar exited during startup (exit code $($process.ExitCode))."
            }
            try {
                $response = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
                if ($response.status -eq "ok") {
                    $healthy = $true
                    break
                }
                $lastError = "Unexpected health response: $($response | ConvertTo-Json -Compress)"
            }
            catch {
                $lastError = $_.Exception.Message
            }
            Start-Sleep -Seconds 1
        }
        if (-not $healthy) {
            throw "Packaged Hooshyar failed its /health smoke test: $lastError"
        }
        Write-Output "Packaged Hooshyar started and passed /health."
    }
    finally {
        $process.Refresh()
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
            Wait-Process -Id $process.Id -Timeout 10 -ErrorAction SilentlyContinue
        }
    }

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
