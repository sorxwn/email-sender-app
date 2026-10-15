# -*- coding: utf-8 -*-
"""
Aplicatie WEB (Streamlit) - Trimitere Emailuri Automatizate din Google Sheets.

Rulare locala:
    .venv-web/bin/streamlit run web_app.py        (macOS / Linux)
    .venv-web\\Scripts\\streamlit run web_app.py   (Windows)

Optional (recomandat): service account Google pentru stergerea automata
a randurilor trimise - instructiunile sunt in bara laterala a aplicatiei.
"""

import time

import streamlit as st

import sheet_io
from email_core import SMTP_PRESETS, SmtpSession, build_lookup, render_template
from sender_worker import SendRun

st.set_page_config(page_title="Trimitere Emailuri Automatizate", page_icon="📧", layout="wide")

DEFAULT_SHEET = ("https://docs.google.com/spreadsheets/d/"
                 "1CrvHbwiL9O1K2N8Ks9AcGZyg3fnpMzp7D7ZKCSEEuo8/edit?usp=sharing")


# ============================================================ stare sesiune ==
def ss_default(key, value):
    if key not in st.session_state:
        st.session_state[key] = value


ss_default("sheet_model", None)      # sheet_io.SheetModel
ss_default("sheet_source", None)     # cum reconectam: {"mode": ..., ...}
ss_default("sheet_error", None)
ss_default("run", None)              # sender_worker.SendRun curent
ss_default("sheet_url", DEFAULT_SHEET)
ss_default("creds_text", "")
ss_default("smtp_host", "smtp.gmail.com")
ss_default("smtp_port", 587)
ss_default("sender_email", "")
ss_default("sender_pass", "")
ss_default("subject", "")
ss_default("body", "")
ss_default("limit", 30)
ss_default("delay", 10)
ss_default("auto_smtp_msg", "")
ss_default("dry_run", True)          # siguranta: intai previzualizare, apoi trimitere
ss_default("secrets_tried", False)   # auto-conectare din st.secrets o singura data
ss_default("secrets_used", False)    # daca ultima conexiune a venit din st.secrets


# ========================================================= functii conexiune =
def reload_model():
    """Reincarca datele din Google Sheet (dupa stergere sau editare externa)."""
    src = st.session_state.sheet_source
    if not src:
        return
    try:
        if src["mode"] == "service":
            ws = src["worksheet"]
            st.session_state.sheet_model = sheet_io.SheetModel(
                ws.get_all_values(), writable=True, worksheet=ws)
        else:
            values = sheet_io.fetch_public_csv(src["sheet_id"])
            st.session_state.sheet_model = sheet_io.SheetModel(values, writable=False)
        st.session_state.sheet_error = None
    except Exception as e:
        st.session_state.sheet_error = "Nu pot reîncărca tabelul: {}".format(e)


def load_secret_creds():
    """
    Citeste cheia Service Account din st.secrets (Streamlit Cloud / secrets.toml).
    JSON-ul NU trebuie urcat niciodata pe GitHub - sta doar in interfata
    Streamlit Cloud (App settings -> Secrets) sau in .streamlit/secrets.toml
    local (fisier ignorat prin .gitignore).

    Accepta, in ordinea preferintei:
      1. [gspread_service_account] ca TABEL TOML (recomandat pe Cloud:
         lipesti fiecare camp al JSON-ului ca si cheie TOML);
      2. gspread_service_account ca STRING JSON (tot JSON-ul intr-o cheie);
      3. gcp_service_account (numele alternativ folosit in documentatia Streamlit).
    Returneaza string JSON sau None (daca nu exista secrets).
    """
    try:
        secrets = st.secrets
    except Exception:
        return None  # fara secrets.toml (ex: rulare locala) -> conectare manuala
    for key in ("gspread_service_account", "gcp_service_account"):
        try:
            if key not in secrets:
                continue
            value = secrets[key]
        except Exception:
            continue
        if isinstance(value, (str, bytes)):
            text = str(value).strip()
            if text:
                return text
            continue
        # tabel TOML -> JSON pentru gspread
        try:
            import json as _json
            return _json.dumps(dict(value))
        except Exception:
            continue
    return None


def load_secret_sheet_url():
    """Linkul tabelului, optional, din st.secrets (cheia 'sheet_url')."""
    try:
        url = st.secrets.get("sheet_url", "")
    except Exception:
        return None
    url = str(url or "").strip()
    return url or None


def connect_service(creds_json_text=None):
    """Conectare prin service account (citire + stergere de randuri)."""
    try:
        sheet_id = sheet_io.extract_sheet_id(st.session_state.sheet_url)
        if not sheet_id:
            raise ValueError("Linkul nu pare un Google Sheet valid.")
        creds = (creds_json_text if creds_json_text is not None
                 else (st.session_state.creds_text or "")).strip()
        if not creds:
            raise ValueError("Lipește JSON-ul service account-ului în câmpul din stânga.")
        client, sa_email = sheet_io.connect_service_account(creds)
        worksheet = client.open_by_key(sheet_id).sheet1
        model = sheet_io.SheetModel(worksheet.get_all_values(), writable=True, worksheet=worksheet)
        st.session_state.sheet_model = model
        st.session_state.sheet_source = {"mode": "service", "worksheet": worksheet,
                                         "sheet_id": sheet_id}
        st.session_state.sheet_error = None
        st.session_state.sa_email = sa_email
    except Exception as e:
        st.session_state.sheet_model = None
        st.session_state.sheet_source = None
        st.session_state.sheet_error = "Conectare eșuată: {}".format(e)


def connect_public():
    """Conectare doar-citire, prin link public (fara stergere posibila)."""
    try:
        sheet_id = sheet_io.extract_sheet_id(st.session_state.sheet_url)
        if not sheet_id:
            raise ValueError("Linkul nu pare un Google Sheet valid.")
        values = sheet_io.fetch_public_csv(sheet_id)
        st.session_state.sheet_model = sheet_io.SheetModel(values, writable=False)
        st.session_state.sheet_source = {"mode": "public", "sheet_id": sheet_id}
        st.session_state.sheet_error = None
    except Exception as e:
        st.session_state.sheet_model = None
        st.session_state.sheet_source = None
        st.session_state.sheet_error = "Citire publică eșuată: {}".format(e)


def auto_smtp_fill():
    """Callback de buton (ruleaza inainte de randarea widget-urilor)."""
    email = (st.session_state.get("sender_email") or "").strip().lower()
    if "@" not in email:
        st.session_state.auto_smtp_msg = "Introdu mai întâi adresa de email."
        return
    domain = email.split("@", 1)[1]
    preset = SMTP_PRESETS.get(domain)
    if preset:
        st.session_state.smtp_host, st.session_state.smtp_port = preset
        st.session_state.auto_smtp_msg = "Server SMTP completat automat pentru {}.".format(domain)
    else:
        st.session_state.auto_smtp_msg = "Domeniu „{}” necunoscut – completează manual serverul.".format(domain)


# auto-conectare cu cheia din st.secrets (Streamlit Cloud) - o singura data,
# fara sa punem JSON-ul in campul de text vizibil din interfata.
# Daca in secrets exista si 'sheet_url', il folosim pe acela (Cloud-first).
if st.session_state.sheet_source is None and not st.session_state.secrets_tried:
    st.session_state.secrets_tried = True
    _secret_url = load_secret_sheet_url()
    if _secret_url:
        st.session_state.sheet_url = _secret_url
    _creds = load_secret_creds()
    if _creds:
        st.session_state.secrets_used = True
        connect_service(_creds)
    elif _secret_url:
        # avem linkul in secrets, dar fara cheie -> macar mod citire
        connect_public()

# =========================================================== bara laterala ==
with st.sidebar:
    st.header("🔌 Conectare la Google Sheet")
    st.text_input("Linkul tabelului tău Google Sheet", key="sheet_url")
    st.text_area("Service account – JSON (posibilitatea de a șterge rândurile)",
                 key="creds_text", height=170,
                 help="Lipești aici tot conținutul fișierului .json descărcat de la Google Cloud. "
                      "Pașii exacți sunt mai jos.")

    b1, b2 = st.columns(2)
    with b1:
        if st.button("🔌 Conectează", type="primary", width="stretch"):
            connect_service()
    with b2:
        if st.button("👁 Mod citire (public)", width="stretch"):
            connect_public()

    if st.session_state.secrets_used:
        st.caption("🔑 Credențialele au fost încărcate automat din st.secrets – "
                   "fișier ascuns: nu urcă pe GitHub și nu se vede în interfață.")

    st.markdown("**Cum obții service account-ul (o singură dată):**")
    st.markdown(
        "1. Mergi pe [console.cloud.google.com](https://console.cloud.google.com/) → creează un proiect.\n"
        "2. **APIs & Services → Library** → activează **Google Sheets API** și **Google Drive API**.\n"
        "3. **APIs & Services → Credentials → Create Credentials → Service account**.\n"
        "4. **Keys → Add key → Create new key → JSON** → descarcă fișierul.\n"
        "5. Lipește conținutul lui în câmpul de mai sus, apoi apasă **🔌 Conectează**.\n"
        "6. **Important:** deschide tabelul → **Share** → adaugă adresa "
        "de tip `...@...iam.gserviceaccount.com` (o vezi după conectare) cu rol **Editor**."
    )
    with st.expander("☁️ Publicare pe Streamlit Cloud – unde pui cheia (fără GitHub)"):
        st.markdown(
            "1. Urcă proiectul pe GitHub **fără** fișierul `.streamlit/secrets.toml` "
            "(e deja ignorat prin `.gitignore` – verifică înainte de `push`).\n"
            "2. Pe [share.streamlit.io](https://share.streamlit.io/) creezi aplicația din repo.\n"
            "3. În pagina aplicației: **⋮ (meniu) → Settings → Secrets** – lipești conținutul "
            "fișierului `.streamlit/secrets.toml.example` completat cu datele tale "
            "(secțiunea `[gspread_service_account]` + opțional `sheet_url`).\n"
            "4. Salvezi (**Save**) – aplicația se reconectează singură și citește cheia "
            "din `st.secrets`, fără ca JSON-ul să ajungă vreodată pe GitHub sau în interfață.\n"
            "5. Local, pentru testare: copiază `.streamlit/secrets.toml.example` ca "
            "`.streamlit/secrets.toml` și completează-l – aplicația îl folosește automat."
        )
    if st.session_state.get("sa_email"):
        st.info("Adresa service account-ului de împărțit cu tabelul:\n`{}`"
                .format(st.session_state.sa_email))
    st.caption("Modul „citire (public)” merge doar dacă tabelul e partajat public; "
               "în acest mod rândurile NU pot fi șterse automat.")

# =========================================================== titlu + sumar ==
st.title("📧 Trimitere Emailuri Automatizate")
st.caption("Conectată direct la Google Sheet-ul tău ·100% în română · fără server sau hosting plătit")

model = st.session_state.sheet_model

st.subheader("1. Situația listei din Google Sheet")
if st.session_state.sheet_error:
    st.error(st.session_state.sheet_error)

if model is None:
    st.info("Conectează tabelul din meniul din stânga: **🔌 Conectează** (service account – "
            "recomandat, șterge rândurile trimise) sau **👁 Mod citire (public)**.")
else:
    info = model.summary()
    if not info["email_column"]:
        st.error("Nu am găsit o coloană cu adrese de email în tabel. "
                 "Coloane găsite: " + ", ".join(model.headers))
    else:
        st.success("În prezent sunt **{} firme necontactate** în listă "
                   "({} rânduri în total · coloana email: „{}”)."
                   .format(info["valid"], info["total"], info["email_column"]))
        if not model.writable:
            st.warning("⚠️ **Mod doar citire:** rândurile NU se vor șterge după trimitere. "
                       "Pentru ștergerea automată, conectează un service account "
                       "(pașii sunt în meniul din stânga).")
        rc1, rc2 = st.columns([1, 3])
        with rc1:
            if st.button("🔄 Reîmprospătează", width="stretch"):
                reload_model()
                st.rerun()
        with rc2:
            st.caption("După fiecare email trimis cu succes, rândul firmei este șters automat din tabel.")
        with st.expander("Vezi primele contacte din tabel"):
            st.dataframe(model.preview(15), width="stretch", hide_index=True)

# ============================================================ expeditor ======
st.subheader("2. Contul tău de email (expeditor)")
sc1, sc2, sc3, sc4 = st.columns([3, 3, 2, 1])
with sc1:
    st.text_input("Email expeditor", key="sender_email", placeholder="nume@gmail.com")
with sc2:
    st.text_input("Parolă de aplicație (App Password)", key="sender_pass",
                  type="password", placeholder="parola de aplicație, nu cea normală")
with sc3:
    st.text_input("Server SMTP", key="smtp_host")
with sc4:
    st.number_input("Port", key="smtp_port", min_value=1, max_value=65535, step=1)

ac1, ac2 = st.columns([1, 3])
with ac1:
    st.button("⚡ Auto SMTP", width="stretch", on_click=auto_smtp_fill)
with ac2:
    if st.session_state.auto_smtp_msg:
        st.caption("💡 " + st.session_state.auto_smtp_msg)

with st.expander("🧩 Cum faci „Parola de aplicație” (App Password) – Gmail / Outlook / Yahoo"):
    st.markdown(
        "**GMAIL:** [myaccount.google.com/security](https://myaccount.google.com/security) → "
        "pornește **Verificarea în doi pași** → caută **„Parole pentru aplicații”** → "
        "**Creati o parola** → lipește cele 16 caractere aici.\n\n"
        "**OUTLOOK/HOTMAIL:** [account.microsoft.com/security](https://account.microsoft.com/security) → "
        "**Parola de aplicatie** → Creează una nouă.\n\n"
        "**YAHOO:** [login.yahoo.com/account/security](https://login.yahoo.com/account/security) → "
        "**Gestioneaza parolele aplicatiilor** → Creează."
    )

# ============================================================ mesaj =========
st.subheader("3. Mesaj și setări de trimitere")
st.text_input("Subiect", key="subject", placeholder="Ex: O colaborare pentru {{Companie}}")
st.text_area("Corp mesaj – variabile: {{Companie}}, {{Persoana_contact}} (și orice altă coloană din tabel)",
             key="body", height=200,
             placeholder="Bună ziua {{Persoana_contact}},\n\n\nCu stimă,\nEchipa noastră")

mc1, mc2, mc3 = st.columns([2, 2, 3])
with mc1:
    st.number_input("La primele câte companii doriți să trimiteți acum?", key="limit",
                    min_value=1, max_value=100000, step=5)
with mc2:
    st.number_input("Pauză între trimiteri (secunde)", key="delay",
                    min_value=0, max_value=3600, step=1)
with mc3:
    st.caption("Recomandare: pauză 10–20 secunde. Gmail (cont obișnuit): max ~500 emailuri/zi – "
               "trimite în transe de 50–100 ca să nu fie blocat contul.")

# ============================================================ trimitere =====
st.subheader("4. Testare fără trimitere (Dry-Run) și Trimitere")

if model is not None and model.summary()["email_column"]:
    st.caption("🔍 Modul uscat: previzualizezi primele emailuri generate cu valorile din tabel – "
               "NU trimite nimic și NU șterge rânduri. Verifică aici cum arată „Stimate partener” "
               "acolo unde lipsește persoana de contact, înainte de trimiterea reală.")
    dr1, dr2 = st.columns([1, 3])
    with dr1:
        n_preview = st.number_input("Câte emailuri previzualizezi?", min_value=1,
                                    max_value=10, value=3, step=1, key="dry_n")
    with dr2:
        st.write("")  # aliniaza butonul cu campul numeric
        btn_dry = st.button("👁 Previzualizează fără trimitere", width="stretch")
    if btn_dry:
        if not (st.session_state.subject or "").strip() and not (st.session_state.body or "").strip():
            st.error("Completează măcar Subiectul sau Corpul mesajului ca să ai ce previzualiza.")
        else:
            from email_core import build_lookup as _lookup, render_template as _render
            for i, t in enumerate(model.targets(int(n_preview)), start=1):
                lk = _lookup(t["data"])
                st.markdown("**{}. Către:** `{}`".format(i, t["recipient"]))
                st.text_input("Subiect generat", value=_render(st.session_state.subject, lk),
                              key="dry_subj_{}".format(i), disabled=True)
                st.text_area("Corp generat", value=_render(st.session_state.body, lk),
                             key="dry_body_{}".format(i), disabled=True)
            st.success("Previzualizare uscată gata: nimic nu a fost trimis, "
                       "niciun rând nu a fost șters din tabel.")
else:
    st.caption("🔍 Previzualizarea uscată (Dry-Run) apare aici după ce conectezi tabelul – "
               "primele 3 emailuri generate, fără trimitere și fără ștergere.")

run = st.session_state.run
busy = bool(run is not None and run.running)

ca, cb = st.columns(2)
with ca:
    btn_start = st.button("🚀 Pornește Trimiterea", type="primary",
                          width="stretch", disabled=busy)
with cb:
    btn_stop = st.button("⏹️ Oprește Trimiterea", width="stretch", disabled=not busy)

if btn_stop and run is not None:
    run.request_stop()
    st.warning("Oprire cerută – se oprește imediat după emailul curent, în siguranță.")

if btn_start:
    if model is None or not model.summary()["email_column"]:
        st.error("Conectează mai întâi Google Sheet-ul (meniul din stânga).")
    elif not (st.session_state.sender_email or "").strip() or not (st.session_state.sender_pass or "").strip():
        st.error("Completează „Email expeditor” și „Parolă de aplicație”.")
    elif not (st.session_state.subject or "").strip() and not (st.session_state.body or "").strip():
        st.error("Completează măcar Subiectul sau Corpul mesajului.")
    else:
        targets = model.targets(int(st.session_state.limit))
        if not targets:
            st.warning("Nu mai sunt firme noi de trimis în acest tabel – "
                       "toate rândurile valide au fost deja contactate.")
        else:
            session = SmtpSession(
                (st.session_state.smtp_host or "").strip(),
                int(st.session_state.smtp_port),
                (st.session_state.sender_email or "").strip(),
                st.session_state.sender_pass,
            )
            run = SendRun(
                session, model, targets,
                (st.session_state.sender_email or "").strip(),
                st.session_state.subject, st.session_state.body,
                delay=int(st.session_state.delay),
            )
            st.session_state.run = run
            run.start()
            busy = True

# ============================================================ progres =======
run = st.session_state.run
if run is not None:
    st.subheader("5. Progres și jurnal (în timp real)")
    bar = st.progress(0.0)
    status_line = st.empty()
    summary_line = st.empty()
    log_box = st.empty()

    def render():
        frac = (run.done / run.total) if run.total else 0.0
        bar.progress(min(1.0, max(0.0, float(frac))))
        icon = "⏳" if run.running else "🏁"
        status_line.markdown("**Status:** {} {}".format(icon, run.status))
        ramas = model.summary()["valid"] if model is not None else "?"
        summary_line.markdown(
            "Trimise: **{}** · Erori: **{}** · Rânduri șterse din tabel: **{}** · "
            "Firme rămase în listă: **{}** · Progres: {}/{}"
            .format(run.sent, run.errors, run.deleted, ramas, run.done, run.total))
        colors = {"ok": "#3fb950", "err": "#f85149", "warn": "#d29922", "info": "#58a6ff"}
        html = ("<div style='height:320px;overflow-y:auto;border:1px solid #30363d;"
                "border-radius:8px;padding:12px;background:#0d1117;font-family:monospace;"
                "font-size:13px;line-height:1.55'>")
        for line in run.log_lines()[-40:]:
            html += "<div style='color:{}'>[{}] {}</div>".format(
                colors.get(line["level"], "#c9d1d9"), line["time"], line["text"])
        html += "</div>"
        log_box.markdown(html, unsafe_allow_html=True)

    render()
    # bucla de actualizare live - se opreste singura cand thread-ul se incheie
    # (daca userul apasa OPRESTE, Streamlit intrerupe aceasta bucla si
    #  reia randarea intr-o noua rulare, cu acelasi fir de trimitere)
    while run.running:
        run.heartbeat()
        time.sleep(0.5)
        render()
    run.heartbeat()
    render()

    if run.finish_reason == "gata":
        st.success("Gata! S-au trimis **{}** emailuri ({} erori, {} rânduri șterse din tabel)."
                   .format(run.sent, run.errors, run.deleted))
    elif run.finish_reason == "oprit":
        st.warning("Trimiterea a fost oprită. Au fost trimise **{}** emailuri – poți relua "
                   "oricând; rândurile deja șterse din tabel nu se mai iau în calcul."
                   .format(run.sent))
    elif run.finish_reason == "eroare":
        st.error(run.fatal_message or "A apărut o eroare la trimitere.")

    if st.button("🔄 Reîmprospătează tabelul după trimitere"):
        reload_model()
        st.rerun()

st.divider()
st.caption("Aplicație locală: datele și parola de aplicație nu părăsesc calculatorul pe care "
           "rulezi această pagină. Varianta desktop (fără browser) este în app.py.")



