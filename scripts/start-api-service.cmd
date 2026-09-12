@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "D:\Mprojet\AI-Football-Prediction-v0.1.0-Phase1-Source\AI-Football-Prediction-v0.1.0-Phase1"

if not exist "logs" mkdir "logs"

set "LOG=logs\api_service.log"
set "MAX_BYTES=5242880"

if exist "%LOG%" (
    for %%A in ("%LOG%") do set "LOGSIZE=%%~zA"

    if !LOGSIZE! GEQ !MAX_BYTES! (
        for /f %%I in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "STAMP=%%I"

        set "ARCHIVE=logs\api_service_!STAMP!.log"

        move "%LOG%" "!ARCHIVE!" >nul
    )
)

for /f "skip=10 delims=" %%F in ('dir /b /a-d /o-d "logs\api_service_*.log" 2^>nul') do (
    del /q "logs\%%F"
)

echo.>> "%LOG%"
echo ============================================================>> "%LOG%"
echo MDRN SportsQ API Service start: %DATE% %TIME%>> "%LOG%"
echo ============================================================>> "%LOG%"

".venv\Scripts\python.exe" -u -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >> "%LOG%" 2>&1
