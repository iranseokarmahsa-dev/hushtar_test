[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDirectory
)

$ErrorActionPreference = "Stop"
$testData = Join-Path $env:TEMP "Hooshyar-desktop-window-smoke-$PID"
$env:HOOSHYAR_DATA_DIR = $testData
Remove-Item Env:HOOSHYAR_PORT, Env:HOOSHYAR_NO_BROWSER -ErrorAction SilentlyContinue

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class HooshyarWindowSmoke {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr extra);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr extra);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint message, IntPtr wParam, IntPtr lParam);
    public static IntPtr FindVisibleWindow(int targetPid) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((hWnd, extra) => {
            uint processId;
            GetWindowThreadProcessId(hWnd, out processId);
            if (processId == (uint)targetPid && IsWindowVisible(hWnd)) {
                var title = new System.Text.StringBuilder(256);
                GetWindowText(hWnd, title, title.Capacity);
                if (title.ToString() == "Hooshyar") {
                    found = hWnd;
                    return false;
                }
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@

$app = Start-Process -FilePath (Join-Path $InstallDirectory "Hooshyar.exe") -PassThru
try {
    $window = [IntPtr]::Zero
    for ($attempt = 0; $attempt -lt 30 -and $window -eq [IntPtr]::Zero; $attempt++) {
        Start-Sleep -Seconds 1
        $app.Refresh()
        if ($app.HasExited) {
            throw "Hooshyar exited before its desktop window opened (exit code $($app.ExitCode))."
        }
        $window = [HooshyarWindowSmoke]::FindVisibleWindow($app.Id)
    }
    if ($window -eq [IntPtr]::Zero) {
        throw "The native Hooshyar window did not appear. See $testData\hooshyar.log."
    }
    Write-Output "Desktop window opened (handle $window)."
    Start-Sleep -Seconds 5
    [HooshyarWindowSmoke]::PostMessage($window, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
    $app.WaitForExit(20000)
    if (-not $app.HasExited) {
        throw "Hooshyar did not exit after its window closed. See $testData\hooshyar.log."
    }
    Write-Output "Closing the desktop window also closed Hooshyar."
}
finally {
    $app.Refresh()
    if (-not $app.HasExited) {
        Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item Env:HOOSHYAR_DATA_DIR -ErrorAction SilentlyContinue
}
