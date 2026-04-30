$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$launcher = $null
if (Test-Path (Join-Path $PWD "launcher.py")) {
    $launcher = Join-Path $PWD "launcher.py"
} elseif (Test-Path (Join-Path $PWD "distribution\\launcher.py")) {
    $launcher = Join-Path $PWD "distribution\\launcher.py"
}

if (-not $launcher) {
    Write-Host "[CognArch] launcher.py not found."
    Read-Host "Press Enter to close"
    exit 1
}

$runtimeCandidates = @(
    (Join-Path $PWD "runtime\\python\\python.exe"),
    (Join-Path $PWD "runtime\\python.exe"),
    (Join-Path $PWD "..\\runtime\\python\\python.exe"),
    (Join-Path $PWD "..\\runtime\\python.exe")
)

$runtimePython = $runtimeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($runtimePython) {
    & $runtimePython $launcher @args
} else {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & py -3.12 $launcher @args
    } else {
        & python $launcher @args
    }
}

if ($LASTEXITCODE -ne 0) {
    Read-Host "CognArch exited with an error. Press Enter to close"
}

exit $LASTEXITCODE
