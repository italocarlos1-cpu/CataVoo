@echo off
REM SPDX-License-Identifier: AGPL-3.0-or-later
REM ===================================================================
REM  DroneHighlighter - instala a IA de paisagens (CLIP)
REM
REM  Duplo clique neste arquivo, na pasta do DroneHighlighter.exe.
REM  Baixa o PyTorch + transformers (~2,5 GB com placa NVIDIA, ~400 MB sem)
REM  para a subpasta packs\clip e o modelo CLIP (~600 MB). Nao precisa ter
REM  Python instalado: o instalador usa um Python temporario e apaga depois.
REM  O programa encontra o pacote sozinho na proxima vez que abrir.
REM
REM    Instalar_IA_Paisagens.bat              detecta a placa automaticamente
REM    Instalar_IA_Paisagens.bat /cpu         forca a versao sem placa de video
REM    Instalar_IA_Paisagens.bat /silencioso  sem pausas (para scripts)
REM
REM  Para desinstalar: apague a pasta packs\clip.
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title DroneHighlighter - instalando a IA de paisagens (CLIP)

set "SILENT="
set "FORCE_CPU="
for %%A in (%*) do (
  if /i "%%~A"=="/silencioso" set "SILENT=1"
  if /i "%%~A"=="/cpu" set "FORCE_CPU=1"
)

echo.
echo === DroneHighlighter: instalando a IA de paisagens (CLIP) ===
echo.

if not exist "DroneHighlighter.exe" (
  echo [ERRO] Coloque este arquivo na mesma pasta do DroneHighlighter.exe.
  goto :falha
)

set "PYXY=3.12"
if exist "_internal\dh_python.txt" set /p PYXY=<"_internal\dh_python.txt"
if exist "dh_python.txt" set /p PYXY=<dh_python.txt
set "PYTAG=%PYXY:.=%"
set "EMBED_VER=3.12.10"
echo Versao do Python do programa: %PYXY%

set "PACK=%CD%\packs\clip"
set "WORK=%CD%\packs\.instalador"
set "TARGET=%PACK%\site-packages"

REM --- placa de video ------------------------------------------------
set "INDEX=https://download.pytorch.org/whl/cpu"
set "KIND=CPU (sem placa NVIDIA)"
if not defined FORCE_CPU (
  where nvidia-smi >nul 2>nul
  if not errorlevel 1 (
    nvidia-smi -L >nul 2>nul
    if not errorlevel 1 (
      set "INDEX=https://download.pytorch.org/whl/cu126"
      set "KIND=placa NVIDIA (CUDA)"
    )
  )
)
echo Versao do PyTorch: %KIND%
echo.

REM --- espaco ---------------------------------------------------------
echo Isto baixa varios GB. Feche o DroneHighlighter antes de continuar.
if not defined SILENT pause

if exist "%TARGET%" (
  echo Removendo a instalacao anterior ...
  rmdir /s /q "%TARGET%" || goto :falha_uso
)
if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%\tmp" || goto :falha
mkdir "%TARGET%" || goto :falha
set "TMP=%WORK%\tmp"
set "TEMP=%WORK%\tmp"

REM --- Python temporario ---------------------------------------------
echo [1/4] Baixando um Python temporario ...
call :baixar "https://www.python.org/ftp/python/%EMBED_VER%/python-%EMBED_VER%-embed-amd64.zip" "%WORK%\python.zip" || goto :falha_rede
mkdir "%WORK%\python"
tar -xf "%WORK%\python.zip" -C "%WORK%\python" || goto :falha
call :baixar "https://bootstrap.pypa.io/pip/pip.pyz" "%WORK%\pip.pyz" || goto :falha_rede
set "PY=%WORK%\python\python.exe"

REM --- PyTorch + transformers ----------------------------------------
echo.
echo [2/4] Baixando e instalando PyTorch e transformers (demora) ...
"%PY%" "%WORK%\pip.pyz" install --disable-pip-version-check --no-cache-dir ^
  --target "%TARGET%" --only-binary=:all: --platform win_amd64 ^
  --python-version %PYXY% --implementation cp --abi cp%PYTAG% ^
  --index-url %INDEX% --extra-index-url https://pypi.org/simple ^
  torch "transformers>=4.40" pillow || goto :falha_pip

REM --- registro do pacote --------------------------------------------
> "%PACK%\pack.json" echo {"name": "clip", "dep": "torch", "priority": 100}

REM --- modelo CLIP ---------------------------------------------------
echo.
echo [3/4] Baixando o modelo CLIP (~600 MB) ...
if "%PYXY%"=="3.12" (
  for %%F in ("%WORK%\python\python*._pth") do >> "%%~fF" echo %TARGET%
  "%PY%" -c "from transformers import CLIPModel, CLIPProcessor as P; m='openai/clip-vit-base-patch32'; CLIPModel.from_pretrained(m); P.from_pretrained(m); print('modelo ok')"
  if errorlevel 1 echo [aviso] O modelo sera baixado na primeira vez que o programa usar o CLIP.
) else (
  echo O modelo sera baixado na primeira vez que o programa usar o CLIP.
)

REM --- limpeza -------------------------------------------------------
echo.
echo [4/4] Limpando arquivos temporarios ...
rmdir /s /q "%WORK%" >nul 2>nul

echo.
echo === Pronto! IA de paisagens instalada em packs\clip ===
echo Abra o DroneHighlighter: no registro deve aparecer "CLIP carregado".
echo Para desinstalar, apague a pasta packs\clip.
if not defined SILENT pause
exit /b 0

:baixar
curl.exe -L --fail --retry 3 -o "%~2" "%~1" 2>nul && exit /b 0
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%~1' -OutFile '%~2'" && exit /b 0
exit /b 1

:falha_rede
echo [ERRO] Nao consegui baixar da internet. Confira a conexao e tente de novo.
goto :falha
:falha_pip
echo [ERRO] A instalacao do PyTorch falhou (internet ou falta de espaco em disco?).
if exist "%TARGET%" rmdir /s /q "%TARGET%" >nul 2>nul
goto :falha
:falha_uso
echo [ERRO] A pasta packs\clip esta em uso. Feche o DroneHighlighter e tente de novo.
goto :falha
:falha
if exist "%WORK%" rmdir /s /q "%WORK%" >nul 2>nul
echo.
echo O programa continua funcionando sem a IA (paisagens pela cor).
if not defined SILENT pause
exit /b 1
