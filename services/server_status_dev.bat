@echo off
title DDTOOLS DEV STATUS
color 09
setlocal

set PM2=pm2

echo ========================================
echo DDTOOLS DEV STATUS
echo ========================================
echo.

%PM2% list

echo.
pause
endlocal