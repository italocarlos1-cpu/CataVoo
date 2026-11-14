@echo off
REM SPDX-License-Identifier: AGPL-3.0-or-later
REM ===================================================================
REM  DroneHighlighter - gera o DroneHighlighter.exe no Windows
REM
REM  Duplo clique neste arquivo (ou rode no Prompt de Comando).
REM  Requisito: Python 3.12 (python.org, marcar "Add python.exe to PATH").
REM
REM    build_exe.bat          edicao "lite": sem PyTorch/CLIP (~350 MB)
REM    build_exe.bat clip     com o CLIP DENTRO do .exe (varios GB; o normal e
REM                           usar o Instalar_IA_Paisagens.bat ao lado do .exe)
REM
REM  Resultado: dist\DroneHighlighter\DroneHighlighter.exe
REM             dist\DroneHighlighter-VERSAO-win64.zip  (a pasta inteira, para copiar/compartilhar)
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0\..\.."
title DroneHighlighter - gerando o .exe
echo.
echo === DroneHighlighter: gerando o .exe (pasta: %CD%) ===
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo [ERRO] Python nao encontrado.
  echo        Instale o Python 3.12 em https://www.python.org/downloads/ marcando
  echo        "Add python.exe to PATH" e rode este arquivo de novo.
  pause & exit /b 1
)
set "PYV=-3.12"
py %PYV% -c "import sys" >nul 2>nul || set "PYV=-3"
py %PYV% -c "import sys; print('Python', sys.version.split()[0])"

if not exist ".venv-build\Scripts\python.exe" (
  echo Criando ambiente isolado .venv-build ...
  py %PYV% -m venv .venv-build || goto :erro
)
set "PY=.venv-build\Scripts\python.exe"
"%PY%" -m pip install --upgrade pip >nul
echo Instalando dependencias (alguns minutos na primeira vez) ...
"%PY%" -m pip install numpy opencv-python PySide6 pyyaml pillow imageio-ffmpeg av pyinstaller || goto :erro

set "DH_WITH_CLIP="
if /i "%~1"=="clip" (
  echo Instalando PyTorch com CUDA e transformers para o CLIP ...
  "%PY%" -m pip install torch --index-url https://download.pytorch.org/whl/cu128 || goto :erro
  "%PY%" -m pip install transformers || goto :erro
  set "DH_WITH_CLIP=1"
)

echo.
echo Empacotando com PyInstaller ...
"%PY%" -m PyInstaller packaging\drone\DroneHighlighter.spec --noconfirm --distpath dist --workpath build\drone || goto :erro

echo.
echo Autoteste: abrindo e fechando a janela ...
set "DRONEHIGHLIGHTER_SELFTEST=%TEMP%\dronehighlighter_selftest.txt"
del "%DRONEHIGHLIGHTER_SELFTEST%" >nul 2>nul
start "" /wait "dist\DroneHighlighter\DroneHighlighter.exe"
set "DRONEHIGHLIGHTER_SELFTEST="
if exist "%TEMP%\dronehighlighter_selftest.txt" (
  type "%TEMP%\dronehighlighter_selftest.txt" & echo.
) else (
  echo [AVISO] O autoteste da janela nao confirmou. Tente abrir o .exe manualmente.
)
"dist\DroneHighlighter\DroneHighlighter-cli.exe" --help >nul || goto :erro

echo Compactando a pasta para compartilhar ...
for /f "tokens=2 delims==" %%V in ('findstr /b "__version__" drone\__init__.py') do set "DHV=%%~V"
set "DHV=%DHV: =%"
set "DHV=%DHV:"=%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\DroneHighlighter\*' -DestinationPath 'dist\DroneHighlighter-%DHV%-win64.zip' -Force"

echo.
echo ===================================================================
echo  PRONTO!
echo    Programa:  %CD%\dist\DroneHighlighter\DroneHighlighter.exe
echo    Pacote:    %CD%\dist\DroneHighlighter-%DHV%-win64.zip
echo  Para usar em outro PC: copie a pasta inteira (ou o .zip). Nao precisa
echo  instalar Python nem ffmpeg.
echo ===================================================================
explorer "dist\DroneHighlighter"
pause
exit /b 0

:erro
echo.
echo [ERRO] A geracao falhou. Veja as mensagens acima.
pause
exit /b 1
