@echo off
echo ==========================================
echo   STOCK SCREENER BOT - DEPLOY UPDATE
echo ==========================================

set KEY="D:\Python Projects\Stock Screener\data\oracle_cloud\ssh-key-2026-02-25.key"
set HOST=ubuntu@158.180.19.113
set REMOTE=~/stock-screener

echo.
echo [1/2] Transferring files...
ssh -i %KEY% %HOST% "rm -rf %REMOTE%/src"
scp -r -i %KEY% "D:\Python Projects\Stock Screener\src" %HOST%:%REMOTE%/src
scp -i %KEY% "D:\Python Projects\Stock Screener\requirements.txt" %HOST%:%REMOTE%/requirements.txt
scp -i %KEY% "D:\Python Projects\Stock Screener\Dockerfile" %HOST%:%REMOTE%/Dockerfile
scp -i %KEY% "D:\Python Projects\Stock Screener\.dockerignore" %HOST%:%REMOTE%/.dockerignore

if %errorlevel% neq 0 (
    echo ERROR: File transfer failed.
    exit /b 1
)

echo.
echo [2/2] Rebuilding and restarting bot...
ssh -i %KEY% %HOST% "cd %REMOTE% && docker build -t stock-screener-bot . && docker stop stock-screener && docker rm stock-screener && docker run -d --name stock-screener --restart unless-stopped --shm-size=256m --env-file .env -v $(pwd)/data:/app/data stock-screener-bot && echo Bot restarted successfully && docker logs --tail 10 stock-screener"

if %errorlevel% neq 0 (
    echo ERROR: Deployment failed.
    exit /b 1
)

echo.
echo ==========================================
echo   DEPLOYMENT COMPLETE
echo ==========================================
