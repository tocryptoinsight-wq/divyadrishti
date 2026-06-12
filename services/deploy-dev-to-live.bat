@echo off
title DEPLOY DEV -> LIVE
color 0E
setlocal

set PM2=pm2
set DEV_DIR=E:\DivyaDrishti\DDTools-dev
set LIVE_DIR=D:\DivyaDrishti\DDTools-live

echo ========================================
echo          DEPLOY DEV -^> LIVE
echo ========================================
echo.
echo  Source: %DEV_DIR%
echo  Target: %LIVE_DIR%
echo.
echo  Login database (*.db) will NOT be overwritten.
echo.
echo ========================================
echo.

set /p confirm=Type DEPLOY to confirm:
if not "%confirm%"=="DEPLOY" (
    echo [CANCELLED] Deployment aborted.
    pause
    exit /b
)

echo.
echo ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
echo  [1/5] Stopping live services...
echo ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
echo.
%PM2% stop divyadrishti-tunnel 2>nul
if errorlevel 1 echo  [WARN] Tunnel stop had issues (may already be stopped)
%PM2% stop divyadrishti-live
if errorlevel 1 echo  [WARN] Live server stop had issues

echo.
echo ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
echo  [2/5] Copying files (dev -^> live)...
echo ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
echo.
robocopy "%DEV_DIR%" "%LIVE_DIR%" ^
/MIR ^
/XD .venv .git __pycache__ node_modules build_temp dist-electron build dist ^
/XD "restart\current_port.json" services ^
/XF *.pyc .env *.db *.db-shm *.db-wal server_log.txt session_id.txt current_port.json
set ROBOTMP=%errorlevel%
if %ROBOTMP% geq 8 (
    echo  [ERROR] Robocopy failed with code %ROBOTMP%
    pause
    exit /b
)
echo  [OK] Files copied successfully

echo.
echo ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
echo  [3/5] Installing dependencies...
echo ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
echo.
python -m pip install -r "%LIVE_DIR%\requirements.txt" --quiet
if errorlevel 1 (
    echo  [WARN] pip install had issues, continuing...
) else (
    echo  [OK] Dependencies installed
)

echo.
echo ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
echo  [4/5] Starting live server...
echo ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
echo.
%PM2% start "%LIVE_DIR%\run_server.py" ^
--name divyadrishti-live ^
 --interpreter python ^
--cwd "%LIVE_DIR%" ^
-- 8080
if errorlevel 1 (
    echo  [ERROR] Failed to start live server
    pause
    exit /b
)
echo  [OK] Live server started on port 8080

echo.
echo ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
echo  [5/5] Starting live tunnel...
echo ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
echo.
%PM2% start "%LIVE_DIR%\services\cloudflared\cloudflared.exe" ^
--name divyadrishti-tunnel ^
-- tunnel --config "%LIVE_DIR%\services\cloudflared\config.yml" ^
--origincert "%LIVE_DIR%\services\cloudflared\cert.pem" run divyadrishti
if errorlevel 1 (
    echo  [WARN] Tunnel may have failed, check manually
) else (
    echo  [OK] Tunnel started
)

%PM2% save

echo.
echo ========================================
echo      DEPLOYMENT COMPLETE
echo ========================================
echo.
echo  All done! Hard refresh browser (Ctrl+Shift+R)
echo  to load the latest files.
echo.
echo  Press any key to view process list...
echo.
pause >nul
cls
%PM2% list
echo.
echo  Press any key to exit...
pause >nul
endlocal