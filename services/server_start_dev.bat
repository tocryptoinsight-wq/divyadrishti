@echo off
title START DDTOOLS DEV
color 0A
setlocal

set PM2=pm2
set PROJECT_DIR=%~dp0..\
set VENV_PYTHONW=%PROJECT_DIR%.venv\Scripts\pythonw.exe

echo ========================================
echo STARTING DDTOOLS DEV (port 8000)
echo ========================================
echo.

%PM2% start "%PROJECT_DIR%run_server.py" ^
--name divyadrishti-dev ^
--interpreter "%VENV_PYTHONW%" ^
--cwd "%PROJECT_DIR%" ^
-- 8000

%PM2% save

echo.
echo Dev server started on port 8000
echo.
%PM2% list

pause
endlocal