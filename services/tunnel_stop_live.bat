@echo off
title STOP CLOUDFLARE TUNNEL (LIVE)
color 0C
setlocal

set PM2=pm2

echo ========================================
echo STOPPING CLOUDFLARE TUNNEL (LIVE)
echo ========================================
echo.

%PM2% stop divyadrishti-tunnel
%PM2% save

echo.
echo Tunnel stopped.
echo.
%PM2% list

pause
endlocal
