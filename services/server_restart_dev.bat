@echo off
title RESTART DDTOOLS DEV
color 0E
setlocal

set PM2=pm2

echo ========================================
echo RESTARTING DDTOOLS DEV
echo ========================================
echo.

%PM2% restart divyadrishti-dev
%PM2% save

echo.
echo Dev server restarted.
echo.
%PM2% list

pause
endlocal