# This script activates the virtual environment for AI News Aggregator
# Run: .\activate.ps1

$venvPath = "..\.venv\ai_news\Scripts\Activate.ps1"

if (Test-Path $venvPath) {
    & $venvPath
    Write-Host "✓ Virtual environment activated!" -ForegroundColor Green
} else {
    Write-Host "✗ Virtual environment not found at: $venvPath" -ForegroundColor Red
    Write-Host "Please run: ..\..\manage_env.ps1 create_env -version 3.11.0 -env ai_news" -ForegroundColor Yellow
}
