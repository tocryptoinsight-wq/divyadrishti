@echo off
title START DDTOOLS LIVE SYSTEM
color 0A
setlocal

set PM2=pm2

echo ========================================
echo STARTING DDTOOLS LIVE SYSTEM
echo ========================================
echo.

call "%~dp0server_start_live.bat"
call "%~dp0tunnel_start_live.bat"

%PM2% save

echo.
echo Starting Electron desktop app...
start /B "" cmd /c "echo Starting Electron... && start /MIN \"DivyaDrishti Electron\" \"%~dp0..\electron\Divyadrishti_launch.cmd\""

echo.
echo Live system running on port 8080, Electron on port 8082.
echo.
%PM2% list

pause
endlocal
