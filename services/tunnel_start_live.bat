@echo off
title START CLOUDFLARE TUNNEL (LIVE)
color 0A
setlocal

set PM2=pm2
set TUNNEL_DIR=%~dp0..\services\cloudflared

echo ========================================
echo STARTING CLOUDFLARE TUNNEL (LIVE)
echo ========================================
echo.

%PM2% start "%TUNNEL_DIR%\cloudflared.exe" ^
--name divyadrishti-tunnel ^
-- tunnel --config "%TUNNEL_DIR%\config.yml" ^
--origincert "%TUNNEL_DIR%\cert.pem" run divyadrishti

%PM2% save

echo.
echo Tunnel started.
echo.
%PM2% list

pause
endlocal
