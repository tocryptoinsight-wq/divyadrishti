@echo off
title DDTOOLS LIVE STATUS
color 09
setlocal

set PM2=pm2

echo ========================================
echo DDTOOLS LIVE STATUS
echo ========================================
echo.

%PM2% list

echo.
pause
endlocal
