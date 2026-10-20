$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar o ambiente. Instale Python 3.12 ou superior." }
}

& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependencias." }

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

Write-Host ""
Write-Host "Preparacao concluida. Configure o Client ID no arquivo .env."
Write-Host "Iniciar: .\.venv\Scripts\python.exe -m music_curator serve"
Write-Host "Demonstracao: .\.venv\Scripts\python.exe -m music_curator serve --demo"

