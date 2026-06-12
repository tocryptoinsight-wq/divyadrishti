@echo off
title STOP DDTOOLS LIVE
color 0C
setlocal

set PM2=pm2

echo ========================================
echo STOPPING DDTOOLS LIVE
echo ========================================
echo.

%PM2% stop divyadrishti-live
%PM2% save

echo.
echo Live server stopped.
echo.
%PM2% list

pause
endlocal
