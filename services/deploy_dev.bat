@echo off
title DEPLOY DEV -> LIVE (DEPRECATED)
color 0E
setlocal

echo ========================================
echo   THIS SCRIPT IS DEPRECATED
echo ========================================
echo.
echo  Use instead:
echo    deploy-dev-to-live.bat   (Windows batch)
echo    deploy-to-live.sh        (Git Bash - recommended)
echo.
echo  This script has broken paths and /MIR flag
echo  that deletes cloudflared config.
echo.
pause
endlocal