# Default regression gate for new features.
# Run from the repo root:  powershell -File scripts/test-regression.ps1
# Equivalent:  python -m pytest tests -q
#
# Does not start the LinkedIn worker and does not ollama pull models.
# Consider a feature done only after this script (or pytest tests -q) passes.

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $python = "python"
}

Write-Host "Regression gate: $python -m pytest tests -q"
& $python -m pytest tests -q
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Ruff: $python -m ruff check src tests"
& $python -m ruff check src tests
exit $LASTEXITCODE
