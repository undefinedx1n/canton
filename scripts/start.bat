@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0..

if not exist .venv\Scripts\python.exe (
  echo [ERROR] venv not found. Run scripts\setup.bat first.
  exit /b 1
)

if not exist frontend\out\index.html (
  echo Building frontend (first run only)...
  pushd frontend
  call npm install --no-audit --no-fund
  call npm run build || (popd & exit /b 1)
  popd
)

echo ============================================================
echo   Park Scheduling System
echo   URL : http://127.0.0.1:8000/login/
echo   API : http://127.0.0.1:8000/docs
echo   Account: admin / admin123456
echo   Keep this window OPEN. Close it (or Ctrl+C) to stop.
echo ============================================================
start "" http://127.0.0.1:8000/login/
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
