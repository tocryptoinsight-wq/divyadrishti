@echo off
title RESTART CLOUDFLARE TUNNEL (LIVE)
color 0E
setlocal

set PM2=pm2

echo ========================================
echo RESTARTING CLOUDFLARE TUNNEL (LIVE)
echo ========================================
echo.

%PM2% restart divyadrishti-tunnel
%PM2% save

echo.
echo Tunnel restarted.
echo.
%PM2% list

pause
endlocal
