@echo off
title START DDTOOLS LIVE
color 0A
setlocal

set PM2=pm2
set PROJECT_DIR=%~dp0..\

echo ========================================
echo STARTING DDTOOLS LIVE (port 8080)
echo ========================================
echo.

%PM2% start "%PROJECT_DIR%run_server.py" ^
--name divyadrishti-live ^
 --interpreter python ^
--cwd "%PROJECT_DIR%" ^
-- 8080

%PM2% save

echo.
echo Live server started on port 8080
echo.
%PM2% list

pause
endlocal
