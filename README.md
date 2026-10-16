# Trimitere Emailuri Automatizate

Aplicație desktop (Python + Tkinter) care trimite emailuri personalizate dintr-un
fișier CSV, cu interfață grafică simplă pentru client. **Rulează 100% local** –
fără server, fără VPS, fără hosting.

## Ce e în folder

| Fișier | Rol |
|---|---|
| `app.py` | Aplicația **desktop** (sursa, Tkinter) |
| `web_app.py` | Aplicația **web** (Streamlit, 100% română, Google Sheet live, atașament CV) |
| `email_core.py` | Logica comună de email (SMTP, template, CSV, atașamente) – folosită de AMBELE variante |
| `sheet_io.py` | Conectare la Google Sheet + ștergerea sigură a rândurilor |
| `sender_worker.py` | Thread-ul de trimitere pentru web (progres, log, stop, ștergere, atașamente) |
| `porneste_web.sh` / `porneste_web.bat` | Dublu-click → pornește aplicația web |
| `build.bat` | Construiește `Trimitere_Emailuri.exe` (rulezi pe Windows) |
| `.github/workflows/build-exe.yml` | Build .exe automat pe GitHub (de pe macOS) |
| `cv_emails_Sheet1.csv` | Lista de contacte (628 rânduri, coloanele din Google Sheet) |
| `INSTRUCTIUNI_CLIENT.txt` | Ghidul pe care i-l predai clientului |
| `test_app.py` | Teste desktop (45) – SMTP falsat, nu trimit emailuri reale |
| `test_web.py` | Teste web (77) – Sheet falsat, SMTP falsat, atașamente, Streamlit AppTest |
| `requirements.txt` | Dependente: `streamlit`, `gspread`, `google-auth` (web) + `pyinstaller` (build) |
| `.streamlit/secrets.toml.example` | Model pentru cheia Service Account (NU conține chei reale) |

## Aplicația WEB (Streamlit + Google Sheet)

```bash
./porneste_web.sh          # macOS/Linux  (prima dată instalează tot singur)
porneste_web.bat           # Windows
# manual: streamlit run web_app.py
```

Ce face:

- **Se conectează la Google Sheet-ul tău** (link-ul e deja completat) și arată
  „*În prezent sunt X firme necontactate în listă*”.
- **Două moduri de conectare:**
  - **🔌 Service account** (recomandat) – poți **șterge automat rândurile**;
    pașii exacți (Google Cloud → Sheets API → JSON → Share cu rol Editor) sunt
    chiar în bara laterală a aplicației.
  - **👁 Mod citire (public)** – merge instant, dar rândurile nu se pot șterge
    (aplicația avertizează explicit).
- **Trimitere:** email, parolă de aplicație, SMTP (cu ⚡Auto SMTP), subiect +
  corp cu `{{Prenume}}` / `{{Persoana_contact}}` / `{{Companie}}` / `{{Companie_Titlu}}`,
  „la primele N companii” (implicit **30**) și pauză (implicit **10s**).
- **📎 Fișier atașat (CV):** încarci PDF-ul o dată, pleacă automat cu FIECARE
  email trimis; Dry-Run-ul arată dacă e atașat sau lipsește.
- **🚀 Pornește / ⏹️ Oprește** – bară de progres + jurnal live; stop-ul e sigur
  (verificat la fiecare secundă, inclusiv în pauze).
- **Ștergerea din tabel se face IMEDIAT după fiecare email trimis cu succes**,
  cu ajustarea numerelor de rând (testat: ștergerile consecutive nimeresc
  exact rândurile corecte). Dacă un destinatar e respins, rândul **rămâne**.
- **Protecție:** dacă închizi fila browserului, trimiterea se oprește singură
  după ~60s (nu mai trimite „orbeste”).

Teste:

```bash
.venv-web/bin/python test_web.py     # 77 teste (include conexiune publică REALA la tabelul tău)
TK_SILENCE_DEPRECATION=1 /usr/bin/python3 test_app.py   # 45 teste desktop (macOS)
python3 test_app.py                  # 45 teste desktop (Windows/Linux)
```

Mediul virtual `.venv-web/` se creează automat la prima rulare a
`porneste_web.sh` / `porneste_web.bat` (sau manual: `python3 -m venv .venv-web && .venv-web/bin/pip install -r requirements.txt`).

Pentru deploy public (Streamlit Cloud): push pe GitHub →
[share.streamlit.io](https://share.streamlit.io/) → `requirements.txt` e deja complet.
Cheia NU se urca pe GitHub: fisierul `.streamlit/secrets.toml` e ignorat prin
`.gitignore`. Completezi `.streamlit/secrets.toml.example` si lipesti continutul
in aplicatie la Settings → Secrets (sectiunea `[gspread_service_account]` +
optional `sheet_url`). Aplicatia citeste cheia din `st.secrets` si se
auto-conecteaza la deschidere. Ghidul pas-cu-pas e in bara laterala
(expanderul „Publicare pe Streamlit Cloud”).

## Cum rulezi varianta desktop local

```bash
python3 app.py          # sau: python app.py pe Windows
```


## Teste

```bash
python3 test_app.py
```

Testează: citirea CSV-ului real, coloana de email, personalizarea
`{{Companie}}`/`{{Persoana_contact}}`, codarea subiectelor cu diacritice,
trimiterea end-to-end cu un SMTP **falsat** (inclusiv butonul OPREȘTE,
reluarea fără retrimis, parola greșită, auto-SMTP, salvarea setărilor).

## Cum faci executabilul .exe pentru client

PyInstaller **nu poate face cross-build** (macOS → Windows), deci ai 2 variante:

**A. Pe orice calculator Windows** (cel mai simplu)
1. Instalează Python de pe python.org (bifează „Add to PATH")
2. Dă dublu-click pe `build.bat`
3. Executabilul apare în `dist\Trimitere_Emailuri.exe`

**B. Direct de pe macOS, prin GitHub Actions** (fără PC Windows)
1. Împinge folderul într-un repo GitHub (`.github/workflows/build-exe.yml` e deja pus)
2. Tab **Actions → build-exe → Run workflow**
3. Descarci artefactul `Trimitere_Emailuri.exe` din pagina de runs

## Ce predai clientului

Doar 3 fișiere, în același folder:
1. `Trimitere_Emailuri.exe`
2. `cv_emails_Sheet1.csv`
3. `INSTRUCTIUNI_CLIENT.txt`

## Ce face aplicația (pe scurt)

- **Alege CSV** – detectează singur coloana cu emailuri (orice variantă de
  nume), encoding (UTF-8/BOM/Latin-1) și delimitator (`,` `;`)
- **Auto SMTP** – completează serverul/portul după domeniu (Gmail, Outlook,
  Yahoo, iCloud); **Testează conexiunea** înainte de trimiteri
- **Personalizare** – `{{Companie}}`, `{{Persoana_contact}}`, `{{Oras}}`,
  `{{Website}}`, `{{Telefon}}` sau orice coloană din CSV
- **Start / Stop** – OPREȘTE e eficient inclusiv în pauze (verificat la
  fiecare secundă); interfața nu se blochează (worker pe thread separat,
  toate actualizările de UI trec printr-o coadă)
- **Rezumă** – emailurile trimise se salvează în `progres_trimis.json`;
  la repornire se sar automat peste ele (bifabil)
- **Setări salvate** – `setari.json` (email, mesaj, pauza, opțional parola)
- **Erori tratate**: destinatar refuzat → se trece mai departe; picarea
  conexiunii → reconectare automată; „serverul cere pauză" (4xx) → așteaptă
  30s și reîncearcă; parola greșită → oprire cu instrucțiuni clare
- **Jurnal** cu oră și culori pentru fiecare email trimis/eroare/sărit

## Limite cunoscute

- Parola se salvează **plaintext** în `setari.json` doar dacă bifa
  „Reține parola" e pornită (implicit pornită, pentru comoditatea
  clientului). Nu distribui fișierul.
- Nu e un tool de mass-mailing cu cozi/throttle adaptiv: ține-te de
  recomandările din `INSTRUCTIUNI_CLIENT.txt` (pauză 10–20s, max
  ~500/zi la Gmail) ca să nu fie blocat contul.
- Interfața e în română.
