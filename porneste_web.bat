@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv-web\Scripts\streamlit.exe" (
    echo Creez mediul virtual pentru aplicatia web - prima data dureaza 1-2 minute...
    python -m venv .venv-web
    if errorlevel 1 (
        echo EROARE: ai Python instalat? Descarca de pe python.org si bifeaza "Add to PATH".
        pause
        exit /b 1
    )
    .venv-web\Scripts\pip install -q --upgrade pip
    .venv-web\Scripts\pip install -q -r requirements.txt
)

echo Pornesc aplicatia web... browserul se deschide singur.
.venv-web\Scripts\streamlit run web_app.py
pause
