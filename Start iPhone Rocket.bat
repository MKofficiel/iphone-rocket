@echo off
rem Starts iPhone Rocket without a console window.
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw rocket.py & exit /b)
where pyw >nul 2>nul && (start "" pyw -3 rocket.py & exit /b)
echo.
echo   Python 3 is required / Python 3 est requis
echo   https://www.python.org/downloads/windows/
echo.
pause
