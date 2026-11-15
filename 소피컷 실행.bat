@echo off
chcp 65001 >nul
title SophiCut
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PIP_DISABLE_PIP_VERSION_CHECK=1
set "HOMEFILE=%LOCALAPPDATA%\SophiCut\home.txt"
set "APPDIR=%~dp0"

rem This launcher may be moved or copied anywhere (e.g. Desktop).
rem If the program folder is next to it, remember it; otherwise use the remembered one.
if not exist "%APPDIR%sophicut\__main__.py" goto findapp
if not exist "%LOCALAPPDATA%\SophiCut" mkdir "%LOCALAPPDATA%\SophiCut"
>"%HOMEFILE%" echo %APPDIR%
goto start

:findapp
if not exist "%HOMEFILE%" goto noapp
set /p APPDIR=<"%HOMEFILE%"
if not exist "%APPDIR%sophicut\__main__.py" goto noapp

:start
cd /d "%APPDIR%"
set "VPY=%APPDIR%.venv\Scripts\python.exe"
if exist "%VPY%" goto checkpkgs

rem ---------- 1) find Python 3.10 - 3.13 ----------
call :findpython
if defined PY goto mkvenv

echo.
echo   [SophiCut] Python is not installed. Installing Python 3.12 automatically...
echo   (If a window asks for permission, click "Yes".)
echo.
where winget >nul 2>nul
if errorlevel 1 goto nopython
winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
call :findpython
if not defined PY goto nopython

:mkvenv
echo.
echo   [SophiCut] Preparing program environment (first run only)...
echo.
%PY% -m venv "%APPDIR%.venv"
if not exist "%VPY%" goto fail

:checkpkgs
"%VPY%" -c "import faster_whisper, flask, PIL" >nul 2>nul
if not errorlevel 1 goto checkffmpeg

rem ---------- 2) install packages ----------
echo.
echo   [SophiCut] First run: installing packages. This takes a few minutes...
echo.
"%VPY%" -m pip install --upgrade pip >nul 2>nul
"%VPY%" -m pip install -r "%APPDIR%requirements.txt"
if errorlevel 1 goto fail
where nvidia-smi >nul 2>nul
if errorlevel 1 goto checkffmpeg
echo.
echo   [SophiCut] NVIDIA GPU found: installing GPU acceleration (about 1GB)...
echo.
"%VPY%" -m pip install -r "%APPDIR%requirements-gpu.txt"
if errorlevel 1 echo   [SophiCut] GPU packages failed. SophiCut will use the CPU instead.

rem ---------- 3) ffmpeg ----------
:checkffmpeg
"%VPY%" -c "import sys; from sophicut import media; sys.exit(0 if media.ensure_ffmpeg() else 1)" >nul 2>nul
if not errorlevel 1 goto run
echo.
echo   [SophiCut] ffmpeg is not installed. Installing ffmpeg automatically...
echo.
where winget >nul 2>nul
if errorlevel 1 goto noffmpeg
winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
"%VPY%" -c "import sys; from sophicut import media; sys.exit(0 if media.ensure_ffmpeg() else 1)" >nul 2>nul
if errorlevel 1 goto noffmpeg

:run
"%VPY%" -m sophicut
echo.
echo   [SophiCut] Server stopped. Press any key to close.
pause >nul
exit /b 0

rem ---------- helpers ----------
:findpython
set "PY="
for %%V in (3.12 3.11 3.13 3.10) do if not defined PY py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
if defined PY exit /b 0
python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>nul && set "PY=python"
if defined PY exit /b 0
for %%V in (312 311 313 310) do if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe""
exit /b 0

:noapp
echo.
echo   [SophiCut] Program folder not found.
echo   Run this file once from the SophiCut folder (the one containing "sophicut").
echo.
pause
exit /b 1

:nopython
echo.
echo   [SophiCut] Could not install Python automatically.
echo   Install Python 3.12 from https://www.python.org/downloads/
echo   (check "Add python.exe to PATH"), then run this file again.
echo.
pause
exit /b 1

:noffmpeg
echo.
echo   [SophiCut] Could not install ffmpeg automatically.
echo   Open Command Prompt and run:  winget install Gyan.FFmpeg
echo   then run this file again.
echo.
pause
exit /b 1

:fail
echo.
echo   [SophiCut] Setup failed. See the messages above.
echo   Tip: check your internet connection and run this file again.
echo   If it keeps failing, delete the ".venv" folder and try again.
echo.
pause
exit /b 1
