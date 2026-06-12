@echo off
title DDTOOLS LIVE BACKUP
color 0B
setlocal

set PM2=pm2

echo ========================================
echo CREATING LIVE BACKUP
echo ========================================
echo.

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set DATETIME=%%i

set BACKUP_DIR=%~dp0..\backups\daily
set PROJECT_DIR=%~dp0..\

echo Stopping live server for consistent backup...
%PM2% stop divyadrishti-live 2>nul

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
if exist "%BACKUP_DIR%\backup-temp" rmdir /s /q "%BACKUP_DIR%\backup-temp"
robocopy "%PROJECT_DIR%" "%BACKUP_DIR%\backup-temp" /MIR /XF nul /NFL /NDL /NJH /NJS /NP

echo Zipping temp directory...
powershell -NoProfile -Command "Compress-Archive -Path '%BACKUP_DIR%\backup-temp\*' -DestinationPath '%BACKUP_DIR%\DDTools-live-%DATETIME%.zip' -Force"
set ZIP_OK=%ERRORLEVEL%

rmdir /s /q "%BACKUP_DIR%\backup-temp"

echo Restarting live server...
%PM2% start "%PROJECT_DIR%run_server.py" --name divyadrishti-live --interpreter python --cwd "%PROJECT_DIR%" -- 8080 2>nul
%PM2% save

if %ZIP_OK%==0 (
    echo ========================================
    echo BACKUP CREATED
    echo ========================================
    echo File: %BACKUP_DIR%\DDTools-live-%DATETIME%.zip
) else (
    echo Backup creation failed!
)

echo.
echo Cleaning old backups (^> 30 days)...
forfiles /P "%BACKUP_DIR%" /S /M "*.zip" /D -30 /C "cmd /c del @path" 2>nul

echo.
echo ========================================
echo BACKUP COMPLETE
echo ========================================
pause
endlocal
