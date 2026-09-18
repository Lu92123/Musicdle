@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
title Musicdle - Setup

set "CHECK_MODE=0"
set "ONLY_CREDS=0"
if /i "%~1"=="--check" set "CHECK_MODE=1"
if /i "%~1"=="--creds-only" set "ONLY_CREDS=1"

echo.
echo  ==== Musicdle - Setup automatico ====
echo.

rem ---------- Python ----------
where python >nul 2>&1
if errorlevel 1 (
  echo  [FALTA] Python no esta en PATH.
  echo          Instalalo desde python.org marcando Add Python to PATH.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('python --version') do set "PYTVER=%%v"
echo  [OK] Python: %PYTVER%

if "%CHECK_MODE%"=="1" goto :check
if "%ONLY_CREDS%"=="1" (
  call :creds
  echo.
  echo  Listo. Ya podes abrir Musicdle.
  pause
  exit /b 0
)

rem ---------- Credenciales de Spotify ----------
call :creds

rem ---------- Entorno virtual ----------
set "PY=.venv\Scripts\python.exe"
if not exist "!PY!" (
  echo.
  echo  Creando entorno virtual en .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo  [ERROR] No se pudo crear el entorno virtual.
    pause
    exit /b 1
  )
)
echo  [OK] Entorno virtual listo.

rem ---------- pip ----------
"!PY!" -m pip install --upgrade pip >nul 2>&1

rem ---------- PyTorch: CUDA o CPU ----------
echo.
where nvidia-smi >nul 2>&1
if errorlevel 1 (
  echo  No se detecto GPU NVIDIA. Instalando PyTorch para CPU ...
  "!PY!" -m pip install torch torchaudio
) else (
  echo  GPU NVIDIA detectada. Instalando PyTorch con CUDA cu126 ...
  "!PY!" -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
  if errorlevel 1 (
    echo  [AVISO] Fallo el indice cu126; probando cu124 ...
    "!PY!" -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
  )
)

rem ---------- Demas dependencias ----------
echo.
echo  Instalando el resto: Demucs, FastAPI, Spotipy, yt-dlp ...
"!PY!" -m pip install -r requirements.txt
if errorlevel 1 (
  echo  [ERROR] Fallo la instalacion de dependencias.
  pause
  exit /b 1
)

rem ---------- FFmpeg ----------
echo.
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo  No se encontro ffmpeg. Instalando con winget:
  winget install --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  where ffmpeg >nul 2>&1
  if errorlevel 1 (
    echo  [AVISO] ffmpeg instalado pero quizas no este en el PATH de esta terminal.
    echo          El lanzador Iniciar Musicdle.bat lo agrega igualmente a su PATH local.
  )
) else (
  echo  [OK] ffmpeg encontrado.
)

echo.
echo  ====== INSTALACION LISTA ======
echo.
echo  Doble clic al acceso Musicdle o Iniciar Musicdle.bat para jugar.
echo  La primera vez pulsá Conectar con Spotify para autorizar.
echo.
pause
exit /b 0

rem =====================================================================
rem  Subrutina: credenciales de Spotify (CLIENT_ID y CLIENT_SECRET)
rem =====================================================================
:creds
set "CID="
set "CSEC="
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if /i "%%a"=="SPOTIFY_CLIENT_ID" set "CID=%%b"
    if /i "%%a"=="SPOTIFY_CLIENT_SECRET" set "CSEC=%%b"
  )
)
set "NEED=1"
if not "%CID%"=="" if not "%CSEC%"=="" (
  if /i not "%CID%"=="tu_client_id_aqui" if /i not "%CID%"=="tu_client_id" (
    if /i not "%CSEC%"=="tu_client_secret_aqui" if /i not "%CSEC%"=="opcional" set "NEED=0"
  )
)
if "%NEED%"=="0" (
  echo  [OK] Credenciales ya configuradas para: %CID%
  set /p CAMBIA="     Queres cambiarlas?  s/N : "
  if /i not "!CAMBIA!"=="s" goto :creds_done
)
rem Atajo por variables de entorno SETUP_CID / SETUP_CSEC (util para automatizar):
if "%NEED%"=="1" if not "%SETUP_CID%"=="" if not "%SETUP_CSEC%"=="" (
  set "CID=%SETUP_CID%"
  set "CSEC=%SETUP_CSEC%"
  goto :creds_write
)

echo.
echo  ==== Credenciales de Spotify ====
echo.
echo  COMO CONSEGUIRLAS:
echo  1) Abri  https://developer.spotify.com/dashboard  con tu cuenta.
echo     Pulsa "Create app", pone nombre y descripcion.
echo     En Redirect URI tipea EXACTAMENTE:
echo        http://127.0.0.1:8000/callback
echo     Marca "Web API" y dale a Create.
echo  2) En la app creada esta el CLIENT ID.
echo     El CLIENT SECRET: en "Settings" de tu app, activa los toggle de
echo     "User authentication" y "REDIRECT URIs" si estan apagados, guarda,
echo     y abajo aparece el Client secret.
echo  3) Pega tus valores. Dejar vacio repite la pregunta;
echo     escribe SALIR para cancelar sin guardar.
echo.
set /a TRIES=0
:ask_cid
set /a TRIES+=1
if %TRIES% gtr 10 (
  echo  [ERROR] Demasiados intentos vacios. Cancelando.
  goto :creds_done
)
set /p CID="  Client ID: "
if /i "%CID%"=="salir" goto :creds_done
if "%CID%"=="" goto :ask_cid
set /a TRIES=0
:ask_csec
set /a TRIES+=1
if %TRIES% gtr 10 (
  echo  [ERROR] Demasiados intentos vacios. Cancelando.
  goto :creds_done
)
set /p CSEC="  Client Secret: "
if /i "%CSEC%"=="salir" goto :creds_done
if "%CSEC%"=="" goto :ask_csec

:creds_write
echo.
echo  Escribiendo .env ...
> ".env" echo SPOTIFY_CLIENT_ID=%CID%
>> ".env" echo SPOTIFY_CLIENT_SECRET=%CSEC%
>> ".env" echo SPOTIFY_REDIRECT_URI=http://127.0.0.1:8000/callback
echo  [OK] Credenciales guardadas en .env
echo.
:creds_done
exit /b 0

rem =====================================================================
rem  Diagnostico (sin cambios)
rem =====================================================================
:check
echo   Diagnostico - no cambia nada:
where ffmpeg >nul 2>&1
if errorlevel 1 ( echo   ffmpeg    : FALTA  - el setup lo instala via winget ) else ( echo   ffmpeg    : OK )
where nvidia-smi >nul 2>&1
if errorlevel 1 ( echo   GPU CUDA  : no     - se instalara PyTorch CPU ) else ( echo   GPU CUDA  : si     - se instalara PyTorch con CUDA )
if exist ".venv\Scripts\python.exe" ( echo   .venv     : existe ) else ( echo   .venv     : falta - lo crea el setup )
if exist ".env" ( echo   .env      : existe ) else ( echo   .env      : falta - lo crea el setup )
echo.
if exist ".venv\Scripts\python.exe" ( "!PY!" --version ) else ( echo   Ejecuta  setup.bat  para instalar todo lo que falte. )
echo   Para cambiar solo las credenciales:  setup.bat --creds-only
echo.
exit /b 0