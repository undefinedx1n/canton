@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0..

echo [1/3] Preparing Python venv...
if not exist .venv\Scripts\python.exe (
  python -m venv .venv || goto :err
)
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt -q || goto :err

echo [2/3] Installing frontend dependencies...
pushd frontend
call npm install --no-audit --no-fund || (popd & goto :err)
popd

echo [3/3] Initializing demo database (31 employees, 12 positions)...
.venv\Scripts\python.exe scripts\init_demo.py || goto :err

echo.
echo Setup finished.
echo   Start DEV  : scripts\start-dev.bat   (frontend 3000 + api 8000)
echo   Start PROD : scripts\start.bat       (single port 8000)
exit /b 0

:err
echo.
echo [ERROR] Setup failed. Check the message above.
exit /b 1
