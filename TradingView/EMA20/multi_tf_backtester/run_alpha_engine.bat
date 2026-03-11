@echo off
echo ===================================================
echo Starting Institutional Alpha Engine - Live Production
echo Date: %date% Time: %time%
echo ===================================================

:: Navigate to the project directory (Change this path if you move the folder!)
cd /d "E:\Machine Learning\TradingView\EMA20\multi_tf_backtester"

:: Activate the conda environment
call C:\ProgramData\miniconda3\Scripts\activate.bat ema20_backtester

:: Step 1: Run the Mathematical Orchestrator (Generates live_target_portfolio.json)
echo [1/2] Running Live Execution Pipeline...
python live_execution.py
if %errorlevel% neq 0 (
    echo [ERROR] live_execution.py failed! Halting execution.
    pause
    exit /b %errorlevel%
)

:: Step 2: Run the Broker Bridge Array (Routes MOC orders to Alpaca)
echo.
echo [2/2] Running Broker Execution Bridge...
python execution_engine.py
if %errorlevel% neq 0 (
    echo [ERROR] execution_engine.py failed!
    pause
    exit /b %errorlevel%
)

echo.
echo [3/4] Running World State NLP Logger...
conda run -n ema20_backtester python "e:\Machine Learning\TradingView\EMA20\multi_tf_backtester\world_state_logger.py"

echo.
echo [4/4] Running Fundamental Logger (Corporate Health)...
python fundamentals_logger.py

echo.
echo ===================================================
echo Alpha Engine Execution Complete.
echo ===================================================
:: Timeout closes the window after 10 seconds automatically
timeout /t 10
