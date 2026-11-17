@echo off
rem Launch Antigravity IDE with proxy env injected, plus agy auto-update disabled.
rem Double-click this file. Extra args are forwarded to tools\launch_ide.py
rem   e.g.  launch-ide.cmd --proxy 127.0.0.1:7892
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

set "PY=python"
%PY% --version >nul 2>nul || set "PY=py -3"
%PY% --version >nul 2>nul || goto :nopython

%PY% tools\launch_ide.py %*
if errorlevel 1 goto :failed
goto :eof

:nopython
echo.
echo [ERROR] Python 3 not found.
echo         Install Python 3.8+ and make sure "python" is in PATH,
echo         or run:  py -3 tools\launch_ide.py
echo.
pause
exit /b 1

:failed
echo.
echo [ERROR] Launch failed, see message above.
echo         Try:  %PY% tools\launch_ide.py --check
echo.
pause
exit /b 1
