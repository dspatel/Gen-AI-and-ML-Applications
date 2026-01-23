@echo off
setlocal


cd /d E:\Machine Learning\TradingView
REM Create tv_venv if missing
if not exist tv_env (
  echo [1/3] Creating virtual environment...
  python -m venv tv_env
)

echo [2/3] Activating tv_venv...
call E:\Machine Learning\TradingView\tv_env\Scripts\activate

echo [3/3] Installing requirements (if needed)...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Starting ORB Monitor...
python run_live.py

pause
endlocal
