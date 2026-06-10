# AI News Aggregator Runner
# This script activates the environment and runs the aggregator

param(
    [switch]$NonInteractive = $false,
    [string]$DaysBack,
    [string]$MaxTweets
)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPath = "$projectRoot\..\\.venv\\ai_news\\Scripts\\Activate.ps1"

# Check if venv exists
if (-not (Test-Path $venvPath)) {
    Write-Host "❌ Virtual environment not found at: $venvPath" -ForegroundColor Red
    Write-Host "Please run setup first:" -ForegroundColor Yellow
    Write-Host "  cd $projectRoot && ..\\manage_env.ps1 create_env -version 3.11.0 -env ai_news" -ForegroundColor Yellow
    exit 1
}

# Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Cyan
& $venvPath

# Check if .env exists
if (-not (Test-Path "$projectRoot\.env")) {
    Write-Host "❌ .env file not found!" -ForegroundColor Red
    Write-Host "Please copy .env.example to .env and configure your API keys:" -ForegroundColor Yellow
    Write-Host "  cp .env.example .env" -ForegroundColor Yellow
    exit 1
}

# Set environment variables if provided
if ($DaysBack) {
    $env:DAYS_BACK = $DaysBack
    Write-Host "📅 Days back: $DaysBack" -ForegroundColor Green
}

if ($MaxTweets) {
    $env:MAX_TWEETS_PER_USER = $MaxTweets
    Write-Host "📊 Max tweets per user: $MaxTweets" -ForegroundColor Green
}

# Install dependencies if needed
Write-Host "Installing dependencies..." -ForegroundColor Cyan
poetry install

# Run the aggregator
Write-Host ""
Write-Host "🚀 Starting AI News Aggregator..." -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Gray

poetry run python "$projectRoot\src\main.py"

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Gray
    Write-Host "✅ Report generation completed successfully!" -ForegroundColor Green
    Write-Host "📁 Check the 'reports' folder for your PDFs" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Gray
    Write-Host "❌ Error during report generation" -ForegroundColor Red
    Write-Host "Exit code: $LASTEXITCODE" -ForegroundColor Red
}
