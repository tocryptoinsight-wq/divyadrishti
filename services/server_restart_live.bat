@echo off
title RESTART DDTOOLS LIVE
color 0E
setlocal

set PM2=pm2

echo ========================================
echo RESTARTING DDTOOLS LIVE
echo ========================================
echo.

%PM2% restart divyadrishti-live
%PM2% save

echo.
echo Live server restarted.
echo.
%PM2% list

pause
endlocal
