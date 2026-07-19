@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "OUT=%~dp0diag_result.txt"

echo PostureGuard Diagnostic
echo ------------------------------------------
echo Writing report to diag_result.txt ...
echo.

> "%OUT%" echo === PostureGuard Diagnostic Report ===
>>"%OUT%" echo Path: %~dp0
>>"%OUT%" echo Time: %DATE% %TIME%
>>"%OUT%" echo.

>>"%OUT%" echo --- [1] python launcher list ---
py -0p >>"%OUT%" 2>&1
>>"%OUT%" echo.

>>"%OUT%" echo --- [2] py -3.11 version / arch ---
py -3.11 -c "import sys,platform;print(sys.version);print('arch:',platform.architecture()[0]);print('exe:',sys.executable)" >>"%OUT%" 2>&1
>>"%OUT%" echo.

>>"%OUT%" echo --- [3] python on PATH ---
where python >>"%OUT%" 2>&1
python -V >>"%OUT%" 2>&1
>>"%OUT%" echo.

>>"%OUT%" echo --- [4] package folder check ---
if exist "%~dp0backend\venv\Lib\site-packages" (
    >>"%OUT%" echo site-packages: FOUND
    dir /b "%~dp0backend\venv\Lib\site-packages" 2>nul | find /c /v "" >>"%OUT%"
    >>"%OUT%" echo ^(number above = item count, normal is 100+^)
) else (
    >>"%OUT%" echo site-packages: MISSING - extract failed, or venv was overwritten
)
if exist "%~dp0backend\app\main.py" (>>"%OUT%" echo backend-main: FOUND) else (>>"%OUT%" echo backend-main: MISSING)
if exist "%~dp0frontend\dist\index.html" (>>"%OUT%" echo frontend-dist: FOUND) else (>>"%OUT%" echo frontend-dist: MISSING)
>>"%OUT%" echo.

>>"%OUT%" echo --- [5] import test ---
set "PYTHONPATH=%~dp0backend\venv\Lib\site-packages"
>>"%OUT%" echo PYTHONPATH=!PYTHONPATH!
for %%M in (numpy cv2 mediapipe fastapi uvicorn sqlalchemy) do (
    py -3.11 -c "import %%M" >nul 2>&1
    if !errorlevel! equ 0 (
        >>"%OUT%" echo   %%M : OK
    ) else (
        >>"%OUT%" echo   %%M : FAILED - detail below
        py -3.11 -c "import %%M" >>"%OUT%" 2>&1
    )
)
>>"%OUT%" echo.

>>"%OUT%" echo --- [6] port 8000 / 3000 in use ---
netstat -ano | findstr ":8000 :3000" >>"%OUT%" 2>&1
>>"%OUT%" echo ^(empty above = ports are free = good^)
>>"%OUT%" echo.

>>"%OUT%" echo --- [7] backend app import (no server spawned) ---
pushd "%~dp0backend"
py -3.11 -c "from app.main import app; print('app import: OK')" >>"%OUT%" 2>&1
popd

echo.
echo Done. Send [diag_result.txt] in this folder to Seungbeom.
echo (You can also open it in Notepad and copy all the text.)
echo.
start "" notepad "%OUT%"
