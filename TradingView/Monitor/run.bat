@echo off
setlocal

REM Create venv if missing
if not exist venv (
  echo [1/3] Creating virtual environment...
  python -m venv venv
)

echo [2/3] Activating venv...
call venv\Scripts\activate

echo [3/3] Installing requirements (if needed)...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Starting ORB Monitor...
python run_live.py

pause
endlocal
