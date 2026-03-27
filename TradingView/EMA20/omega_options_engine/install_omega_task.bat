@echo off
:: Check for Administrator Privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    color 0C
    echo =====================================================
    echo    ERROR: Administrator Privileges Required!
    echo =====================================================
    echo Windows Task Scheduler requires elevated permissions.
    echo Please close this window, right-click "install_omega_task.bat",
    echo and select "Run as administrator".
    echo.
    pause
    exit /b
)

color 0A
echo =====================================================
echo       INSTALLING OMEGA EPHEMERAL SENTINEL
echo =====================================================
echo Injecting silent background task into Windows Task Scheduler...
echo.

:: Execute the native schtasks command to register the task explicitly under the user's session
schtasks /create /tn "Omega Engine Daily Execution" /tr "\"E:\Machine Learning\TradingView\EMA20\omega_options_engine\run_omega_engine.bat\"" /sc daily /st 08:30 /f

echo.
echo =====================================================
echo    SUCCESS: THE MATRIX IS EPHEMERAL.
echo =====================================================
echo The Omega Options Engine is now silently scheduled in the background.
echo It will automatically launch invisibly EVERY DAY at 8:30 AM CST.
echo It uses Alpaca's Live Clock to instantly skip weekends/holidays,
echo and permanently terminates itself at exactly 3:00 PM CST.
echo.
pause
