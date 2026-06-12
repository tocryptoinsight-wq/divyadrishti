@echo off
title STOP DDTOOLS LIVE SYSTEM
color 0C
setlocal

set PM2=pm2

echo ========================================
echo STOPPING DDTOOLS LIVE SYSTEM
echo ========================================
echo.

%PM2% stop divyadrishti-tunnel
%PM2% stop divyadrishti-live
%PM2% save

echo.
echo Live system stopped.
echo.
%PM2% list

pause
endlocal
