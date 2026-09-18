@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Musicdle - Heardle ^& Bandle Local

rem FFmpeg en PATH (necesario para el modo Bandle)
set "FFDIR=C:\Users\Lucas\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin"
if exist "%FFDIR%" set "PATH=%FFDIR%;%PATH%"

rem Si el server ya esta corriendo en el puerto 8000, solo abrir el navegador
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if ($c) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
  echo.
  echo El juego ya esta corriendo en http://127.0.0.1:8000
  start "" "http://127.0.0.1:8000"
  exit /b 0
)

rem Iniciar el server en segundo plano (usa el venv si existe, sino el python del sistema)
powershell -NoProfile -Command "$py = if (Test-Path '.\.venv\Scripts\python.exe') { '.\.venv\Scripts\python.exe' } else { 'python' }; Start-Process -FilePath $py -ArgumentList 'main.py' -WorkingDirectory (Get-Location) -WindowStyle Hidden"

echo.
echo Musicdle arrancando por primera vez... esperando al server.
powershell -NoProfile -Command "for ($i=0; $i -lt 60; $i++) { try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/status' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop; if ($r) { exit 0 } } catch {}; Start-Sleep -Seconds 1 }; exit 1"
if %errorlevel%==0 (
  start "" "http://127.0.0.1:8000"
) else (
  echo No respondio a tiempo. Abri http://127.0.0.1:8000 cuando quede listo.
)
exit /b 0