@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0..

if not exist .venv\Scripts\python.exe (
  echo [ERROR] venv not found. Run scripts\setup.bat first.
  exit /b 1
)

start "schedule-api-8000" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend --reload"

pushd frontend
if not exist node_modules (
  echo Installing frontend dependencies...
  call npm install --no-audit --no-fund
)
start "schedule-web-3000" cmd /k "npm run dev"
popd

echo.
echo API  : http://127.0.0.1:8000/docs
echo WEB  : http://127.0.0.1:3000
echo Login: admin / admin123456
timeout /t 4 >nul
start http://127.0.0.1:3000/login/
