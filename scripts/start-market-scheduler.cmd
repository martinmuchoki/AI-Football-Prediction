@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "D:\Mprojet\AI-Football-Prediction-v0.1.0-Phase1-Source\AI-Football-Prediction-v0.1.0-Phase1"

if not exist "logs" mkdir "logs"

set "LOG=logs\market_scheduler.log"
set "MAX_BYTES=5242880"

if exist "%LOG%" (
    for %%A in ("%LOG%") do set "LOGSIZE=%%~zA"

    if !LOGSIZE! GEQ !MAX_BYTES! (
        for /f "tokens=1-4 delims=/ " %%a in ("%date%") do (
            set "STAMP_DATE=%%d-%%b-%%c"
        )

        for /f "tokens=1-4 delims=:., " %%a in ("%time%") do (
            set "STAMP_TIME=%%a%%b%%c"
        )

        set "ARCHIVE=logs\market_scheduler_!STAMP_DATE!_!STAMP_TIME!.log"

        move "%LOG%" "!ARCHIVE!" >nul
    )
)

for /f "skip=10 delims=" %%F in ('dir /b /a-d /o-d "logs\market_scheduler_*.log" 2^>nul') do (
    del /q "logs\%%F"
)

echo.>> "%LOG%"
echo ============================================================>> "%LOG%"
echo MDRN SportsQ Market Scheduler start: %DATE% %TIME%>> "%LOG%"
echo ============================================================>> "%LOG%"

".venv\Scripts\python.exe" -u -m app.market_scheduler >> "%LOG%" 2>&1
