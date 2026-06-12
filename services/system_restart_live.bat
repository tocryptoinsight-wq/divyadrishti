@echo off
title RESTART DDTOOLS LIVE SYSTEM
color 0E
setlocal

set PM2=pm2

echo ========================================
echo RESTARTING DDTOOLS LIVE SYSTEM
echo ========================================
echo.

%PM2% restart divyadrishti-tunnel
%PM2% restart divyadrishti-live
%PM2% save

echo.
echo Live system restarted.
echo.
%PM2% list

pause
endlocal
