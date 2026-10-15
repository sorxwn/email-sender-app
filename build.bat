@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==============================================
echo   Build executabil: Trimitere_Emailuri.exe
echo ==============================================

set PY=python
where python >nul 2>nul
if errorlevel 1 set PY=py

echo.
echo [1/3] Instalez PyInstaller (o singura data)...
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo EROARE: nu am putut instala PyInstaller.
    echo Verifica daca Python e instalat: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo.
echo [2/3] Construiesc executabilul (dureaza 1-2 minute)...
%PY% -m PyInstaller --onefile --noconsole --name "Trimitere_Emailuri" app.py
if errorlevel 1 (
    echo EROARE la construirea executabilului.
    pause
    exit /b 1
)

echo.
echo [3/3] GATA!
echo.
echo Executabilul tau este aici:
echo     dist\Trimitere_Emailuri.exe
echo.
echo De predat clientului:
echo     - dist\Trimitere_Emailuri.exe
echo     - cv_emails_Sheet1.csv
echo     - INSTRUCTIUNI_CLIENT.txt
echo.
pause
