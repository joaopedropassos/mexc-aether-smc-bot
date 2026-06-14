# Helper script to install / repair the Aether bot environment on Windows
# Run this with:  .\install.ps1   (from the mexc_aether_bot folder, PowerShell)

Write-Host "=== Aether SMC Bot - Environment Setup ===" -ForegroundColor Cyan

$venvPython = ".\.venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

Write-Host "Upgrading pip and fixing certifi (common Windows issue)..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install --upgrade --force-reinstall certifi

Write-Host "Installing requirements..."
& $venvPython -m pip install -r requirements.txt

Write-Host ""
Write-Host "✅ Installation / repair completed!" -ForegroundColor Green
Write-Host "To run the bot:  .\.venv\Scripts\activate ; python main.py" -ForegroundColor Yellow
Write-Host "It will start in DRY-RUN (paper) mode by default. Read the README."
