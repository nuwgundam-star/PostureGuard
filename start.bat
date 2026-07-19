@echo off
REM ==========================================================
REM  PostureGuard demo launcher
REM  NOTE: This file is intentionally ASCII-only.
REM  Korean text inside a UTF-8 .bat breaks cmd.exe parsing
REM  ("not recognized as an internal or external command").
REM  Do NOT add Korean characters to this file.
REM ==========================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title PostureGuard demo

echo ============================================
echo    PostureGuard - realtime VDT risk (demo)
echo ============================================
echo.

REM --- find Python 3.11 -------------------------------------
REM  The bundled venv cannot be moved between PCs (its config
REM  stores an absolute path). So we use the system Python 3.11
REM  and inject the bundled site-packages via PYTHONPATH.
set "PY="
py -3.11 -c "import sys" >nul 2>&1
if !errorlevel! equ 0 (
    set "PY=py -3.11"
    echo [Python] using system Python 3.11 via py launcher
) else (
    python -c "import sys;assert sys.version_info[:2]==(3,11)" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY=python"
        echo [Python] using python 3.11 on PATH
    )
)

if not defined PY (
    echo.
    echo  [X] Python 3.11 NOT FOUND.
    echo.
    echo      Install Python 3.11 from python.org
    echo      and CHECK "Add python.exe to PATH" during setup.
    echo      3.12 / 3.13 will NOT work - it must be 3.11
    echo.
    echo      Then close this window and run start.bat again.
    echo.
    echo      [KR] Python 3.11 mi-seolchi. 3.11 beonjeon-eul
    echo           seolchi hago dasi silhaeng hae juseyo.
    echo.
    pause
    exit /b 1
)

REM --- inject bundled packages (no pip install needed) ------
set "PYTHONPATH=%~dp0backend\venv\Lib\site-packages"
if not exist "%PYTHONPATH%" (
    echo.
    echo  [X] backend\venv\Lib\site-packages is MISSING.
    echo.
    echo      The zip was not fully extracted, or the venv
    echo      folder was overwritten. Do NOT create a new venv.
    echo      Please extract the zip again ^(all files^).
    echo.
    pause
    exit /b 1
)

REM --- self check so we never fail silently -----------------
echo [check] verifying libraries...
%PY% -c "import fastapi,uvicorn,cv2,mediapipe,numpy" 2>nul
if !errorlevel! neq 0 (
    echo.
    echo  [X] Could not import required libraries.
    echo      Detected Python version:
    %PY% -V
    echo.
    echo      Full error detail:
    %PY% -c "import fastapi,uvicorn,cv2,mediapipe,numpy"
    echo.
    echo      Run diagnose.bat and send diag_result.txt
    echo.
    pause
    exit /b 1
)
echo        OK
echo.

echo [1/3] starting backend (port 8000)...
start "PostureGuard-Backend" /d "%~dp0backend" cmd /k "set PYTHONPATH=%PYTHONPATH%&& %PY% -m uvicorn app.main:app --port 8000"

echo [2/3] starting dashboard (port 3000)...
start "PostureGuard-Frontend" /d "%~dp0frontend\dist" cmd /k "%PY% -m http.server 3000"

echo [3/3] opening browser in 10 seconds...
ping -n 11 127.0.0.1 >nul
start "" "http://localhost:3000"

echo.
echo ============================================
echo  Done.
echo.
echo  - Keep the two black windows open during the demo
echo  - If the browser does not open:  http://localhost:3000
echo    Use "localhost", NOT 127.0.0.1 (buttons get blocked)
echo  - If it still fails, just use the video file:
echo    [ siyeon-yeongsang.mp4 / demo video in this folder ]
echo ============================================
echo.
pause
