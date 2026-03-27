@echo off
TITLE Omega Deep Reinforcement Learning Agent (CST)
COLOR 0A

echo ===================================================
echo Starting Institutional Omega Engine - Live Production
echo Date: %date% Time: %time%
echo ===================================================

:: Navigate to the project directory
cd /d "E:\Machine Learning\TradingView\EMA20\omega_options_engine"

:: Activate the conda environment (Crucial for python paths!)
call C:\ProgramData\miniconda3\Scripts\activate.bat ema20_backtester

:: Step 1: Run the Ephemeral Sentinel (Executes math, generates DB telemetry)
echo [1/1] Launching the Omega Options Sentinel...
call python omega_scheduler.py

if %errorlevel% neq 0 (
    echo [ERROR] omega_scheduler.py failed! Halting execution.
    pause
    exit /b %errorlevel%
)

echo.
echo ===================================================
echo Omega Engine Execution Complete.
echo ===================================================
:: Timeout closes the window after 10 seconds automatically
timeout /t 10
