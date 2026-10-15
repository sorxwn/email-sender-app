#!/bin/sh
# Porneste aplicatia WEB (Streamlit) pe macOS / Linux.
# Prima rulare: creeaza mediul virtual si instaleaza pachetele (1-2 minute).
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv-web/bin/streamlit" ]; then
    echo "Creez mediul virtual pentru aplicatia web (prima data dureaza 1-2 minute)..."
    python3 -m venv .venv-web || { echo "EROARE: ai python3 instalat?"; exit 1; }
    .venv-web/bin/pip install -q --upgrade pip
    .venv-web/bin/pip install -q -r requirements.txt || { echo "EROARE la instalare."; exit 1; }
fi

echo "Pornesc aplicatia web... browserul se deschide singur (sau vezi adresa din terminal)."
.venv-web/bin/streamlit run web_app.py
