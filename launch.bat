@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem No dependency installation or system configuration changes are performed.
pushd "%~dp0"
if errorlevel 1 goto directory_error

py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 goto run_py

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 goto run_python

echo Python 3.9 or newer is required. Install Python, then run this script again.
set "result=1"
goto finish

:run_py
py -3 app.py %*
set "result=%errorlevel%"
goto finish

:run_python
python app.py %*
set "result=%errorlevel%"
goto finish

:finish
popd
if not "%result%"=="0" pause
endlocal & exit /b %result%

:directory_error
echo Cannot open the project directory.
pause
endlocal & exit /b 1
