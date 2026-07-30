$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Get-Command conda -ErrorAction SilentlyContinue) {
    conda run --no-capture-output -n proteinfoldingpractical python "$PSScriptRoot\run_app.py"
    exit $LASTEXITCODE
}

$candidates = @(
    "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
    "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
    "$env:USERPROFILE\miniforge3\Scripts\conda.exe",
    "$env:LOCALAPPDATA\miniconda3\Scripts\conda.exe"
)

$condaPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $condaPath) {
    Write-Error "Conda was not found. Install Miniconda, Anaconda, or Miniforge first."
}

& $condaPath run --no-capture-output -n proteinfoldingpractical python "$PSScriptRoot\run_app.py"
exit $LASTEXITCODE
