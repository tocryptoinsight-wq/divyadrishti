@echo off
title STOP DDTOOLS DEV
color 0C
setlocal

set PM2=pm2

echo ========================================
echo STOPPING DDTOOLS DEV
echo ========================================
echo.

%PM2% stop divyadrishti-dev
%PM2% save

echo.
echo Dev server stopped.
echo.
%PM2% list

pause
endlocal