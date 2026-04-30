$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot "distribution\start.ps1") @args
exit $LASTEXITCODE
