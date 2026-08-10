@echo off
echo ===================================================
echo 🚀 1. Building frontend production assets...
echo ===================================================
cd frontend
call npm run build
if %ERRORLEVEL% neq 0 (
    echo ❌ Frontend build failed! Deployment aborted.
    cd ..
    exit /b %ERRORLEVEL%
)
cd ..

echo ===================================================
echo 🚀 2. Deploying application to Fly.io...
echo ===================================================
call fly deploy
if %ERRORLEVEL% neq 0 (
    echo ❌ Deployment to Fly.io failed!
    exit /b %ERRORLEVEL%
)

echo ===================================================
echo 🎉 Deployment successful!
echo ===================================================
pause
