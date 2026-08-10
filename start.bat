@echo off
echo ===================================================
echo 🧹 Clearing ports 5190 (Frontend) and 8025 (Backend)...
echo ===================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0clear_ports.ps1"

echo.
echo ===================================================
echo 🚀 Starting Backend Server on port 8025...
echo ===================================================
start "Assistant UI Backend (Port 8025)" cmd /k "cd /d %~dp0backend && .venv\Scripts\python.exe main.py"

echo.
echo ===================================================
echo 🚀 Starting Frontend Dev Server on port 5190...
echo ===================================================
cd /d %~dp0frontend
npm run dev
