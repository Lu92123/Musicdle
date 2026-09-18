@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
title Musicdle - Setup

set "CHECK_MODE=0"
if /i "%~1"=="--check" set "CHECK_MODE=1"

echo.
echo  ==== Musicdle - Setup automatico ====
echo.

rem ---------- Python ----------
where python >nul 2>&1
if errorlevel 1 (
  echo  [FALTA] Python no esta en PATH.
  echo          Instalalo desde python.org marcando Add Python to PATH.
  if "%CHECK_MODE%"=="0" pause
  exit /b 1
)
for /f "delims=" %%v in ('python --version') do set "PYTVER=%%v"
echo  [OK] Python: %PYTVER%

if "%CHECK_MODE%"=="1" goto :check

rem ---------- .env ----------
if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo  [AVISO] No existia .env. Se creo una copia de .env.example.
  echo          Completala con tu CLIENT_ID y CLIENT_SECRET antes de jugar.
) else (
  echo  [OK] .env encontrado.
)

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
echo  1 Completá tu CLIENT_ID y CLIENT_SECRET en el archivo .env
echo     Dashboard: https://developer.spotify.com/dashboard
echo     Redirect URI de la app: http://127.0.0.1:8000/callback
echo  2 Doble clic al acceso Musicdle o Iniciar Musicdle.bat para jugar.
echo  3 La primera vez pulsá Conectar con Spotify para autorizar.
echo.
pause
exit /b 0

:check
echo   Diagnostico - no cambia nada:
where ffmpeg >nul 2>&1
if errorlevel 1 ( echo   ffmpeg    : FALTA  - el setup lo instala via winget ) else ( echo   ffmpeg    : OK )
where nvidia-smi >nul 2>&1
if errorlevel 1 ( echo   GPU CUDA  : no     - se instalara PyTorch CPU ) else ( echo   GPU CUDA  : si     - se instalara PyTorch con CUDA )
if exist ".venv\Scripts\python.exe" ( echo   .venv     : existe ) else ( echo   .venv     : falta - lo crea el setup )
if exist ".env" ( echo   .env      : existe ) else ( echo   .env      : falta - el setup copia .env.example )
echo.
if exist ".venv\Scripts\python.exe" ( "!PY!" --version ) else ( echo   Ejecuta  setup.bat  para instalar todo lo que falte. )
echo.
exit /b 0