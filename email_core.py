# -*- coding: utf-8 -*-
"""
Logica comuna pentru trimiterea de emailuri (impartasita de app.py si web_app.py).

Contine: citirea CSV-ului, detectia coloanei de email, personalizarea
mesajelor ({{Companie}}, {{Persoana_contact}}...), construirea mesajului
si sesiunea SMTP (conectare, reconectare automata, tratarea erorilor).

Acest modul NU depinde de tkinter -> merge si pe servere (Streamlit web).
"""

import csv
import io
import json
import os
import re
import smtplib
import ssl
import unicodedata
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# Servere SMTP cunoscute -> completare automata dupa domeniul emailului
SMTP_PRESETS = {
    "gmail.com": ("smtp.gmail.com", 587),
    "googlemail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp.office365.com", 587),
    "outlook.ro": ("smtp.office365.com", 587),
    "hotmail.com": ("smtp.office365.com", 587),
    "hotmail.ro": ("smtp.office365.com", 587),
    "live.com": ("smtp.office365.com", 587),
    "msn.com": ("smtp.office365.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 465),
    "yahoo.ro": ("smtp.mail.yahoo.com", 465),
    "yahoo.de": ("smtp.mail.yahoo.com", 465),
    "yahoo.fr": ("smtp.mail.yahoo.com", 465),
    "icloud.com": ("smtp.mail.me.com", 587),
    "me.com": ("smtp.mail.me.com", 587),
    "mac.com": ("smtp.mail.me.com", 587),
}

# Coduri SMTP temporare (serverul cere o pauza) - le tratam cu o pauza + retry
TRANSIENT_CODES = {421, 450, 451, 452, 454}

# Nume de coloane acceptate ca fiind "emailul" destinatarului
EMAIL_COLUMN_NAMES = {
    "email", "e_mail", "mail", "email_adresa", "adresa_email",
    "adresa_de_email", "email_address", "adresa_mail", "email_destinatar",
}

VAR_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z0-9\-]{2,}$")


# --------------------------------------------------------------------------
#  Ajutoare generale
# --------------------------------------------------------------------------
def normalize_header(value):
    """'Persoana_contact' / 'Persoana contact' -> 'persoana_contact'."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def valid_email(addr):
    return bool(addr) and bool(EMAIL_RE.match(addr))


def find_email_column(headers, rows):
    """Gaseste coloana care contine adresele destinatarilor."""
    norm_map = {h: normalize_header(h) for h in headers}
    for candidate in EMAIL_COLUMN_NAMES:
        for h, n in norm_map.items():
            if n == candidate:
                return h
    for h, n in norm_map.items():
        if "email" in n:
            return h
    for h, n in norm_map.items():
        if "mail" in n:
            return h
    # ultima solutie: coloana cu cele mai multe "@"
    best, best_score = None, 0
    for h in headers:
        score = sum(1 for r in rows if "@" in str(r.get(h, "")))
        if score > best_score:
            best, best_score = h, score
    if best and best_score >= max(1, len(rows) // 2):
        return best
    return None


def load_csv_rows(path):
    """Citeste un CSV (encoding/delimitator variabil) -> (headers, rows)."""
    with open(path, "rb") as f:
        raw = f.read()
    if not raw.strip():
        raise ValueError("Fișierul CSV este gol.")

    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1250", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Nu am reușit să citesc codarea fișierului CSV.")

    lines = text.splitlines()
    sample = "\n".join(lines[:5])
    counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
    delim = max(counts, key=counts.get) if max(counts.values()) > 0 else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    headers = [h for h in (reader.fieldnames or []) if h and str(h).strip()]
    if not headers:
        raise ValueError("CSV-ul nu are rând de antete (prima linie cu coloanele).")

    rows = []
    for raw_row in reader:
        row = {}
        for k, v in raw_row.items():
            if k is None:
                continue
            row[k] = ("" if v is None else str(v)).strip()
        if any(row.values()):
            rows.append(row)
    if not rows:
        raise ValueError("CSV-ul nu conține niciun rând de date.")
    return headers, rows

def build_lookup(row):
    lookup = {}
    for k, v in row.items():
        lookup[str(k).strip()] = v
        lookup[normalize_header(k)] = v
    return lookup


# Ce inseamna „celula goala" - inclusiv resturi lasate de pandas (CSV „nan"/„None")
BLANK_TOKENS = {"", "nan", "none", "null", "n/a", "na", "#n/a", "-", "--"}


def is_blank_value(val):
    if val is None:
        return True
    try:
        if isinstance(val, float) and val != val:
            return True
    except Exception:
        pass
    return str(val).strip().lower() in BLANK_TOKENS

# Text neutru pentru variabilele goale in email (niciodata „nan"/„None"/gol)
NEUTRAL_FALLBACKS = {
    "persoana_contact": "Stimate partener",
    "persoana": "Stimate partener",
    "nume_contact": "Stimate partener",
    "contact": "Stimate partener",
    "companie": "compania dumneavoastra",
    "firma": "compania dumneavoastra",
    "societate": "compania dumneavoastra",
}


def render_template(template, lookup, fallbacks=None):
    """Inlocuieste {{Companie}}, {{Persoana_contact}} etc. cu valorile din rand.

    Celulele goale sau „nan"/„None" NU ajung niciodata in email:
    - {{Persoana_contact}} gol -> „Stimate partener" (NEUTRAL_FALLBACKS)
    - celelalte variabile goale -> text gol (ex: „Salut , " nu apare)
    """
    fb = NEUTRAL_FALLBACKS if fallbacks is None else fallbacks

    def repl(match):
        key = match.group(1)
        val = lookup.get(key)
        if val is None:
            val = lookup.get(normalize_header(key))
        if is_blank_value(val):
            return fb.get(normalize_header(key), "")
        return str(val)

    return VAR_RE.sub(repl, template)


def find_unknown_vars(*templates):
    """Variabilele {{...}} care nu corespund niciunei coloane din CSV."""
    unknown = set()
    for tpl in templates:
        for m in VAR_RE.finditer(tpl or ""):
            unknown.add(m.group(1))
    return unknown


def build_message(sender, recipient, subject, body):
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = Header(subject, "utf-8")  # caractere speciale (æøå, șț) corect codate
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_json(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


class FatalSmtpError(Exception):
    """Eroare care opreste intreaga rulare (autentificare / server picat)."""


def smtp_error_code(exc):
    """Codul SMTP al unei exceptii (SMTPRecipientsRefused il are in .recipients)."""
    code = getattr(exc, "smtp_code", None)
    if code is None:
        recipients = getattr(exc, "recipients", None)
        if isinstance(recipients, dict) and recipients:
            try:
                code = next(iter(recipients.values()))[0]
            except Exception:
                code = None
    return code


# --------------------------------------------------------------------------
#  Sesizune SMTP: conectare, trimitere cu reconectare automata
# --------------------------------------------------------------------------
class SmtpSession:
    def __init__(self, host, port, user, password):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.server = None

    def close(self):
        if self.server is not None:
            try:
                self.server.quit()
            except Exception:
                try:
                    self.server.close()
                except Exception:
                    pass
            self.server = None

    def connect(self):
        self.close()
        server = None
        try:
            if self.port == 465:
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=30,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=30)
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            server.login(self.user, self.password)
        except smtplib.SMTPAuthenticationError as e:
            if server:
                try:
                    server.close()
                except Exception:
                    pass
            raise FatalSmtpError(
                "Autentificare eșuată la serverul {}\n\n"
                "Verifică:\n"
                "  1. Adresa de email și parola sunt corecte.\n"
                "  2. Folosești „Parola de aplicație” (App Password), NU parola normală.\n"
                "     Gmail: Setări Google → Securitate → Verificarea în doi pași → "
                "Parole pentru aplicații.\n"
                "     Outlook/Yahoo: setează o parolă de aplicație din contul lor.\n\n"
                "Detalii tehnice: {}".format(self.host, e)
            )
        except (smtplib.SMTPException, OSError) as e:
            if server:
                try:
                    server.close()
                except Exception:
                    pass
            raise FatalSmtpError(
                "Nu mă pot conecta la serverul SMTP {}:{}\n\n"
                "Verifică serverul, portul și conexiunea la internet.\n"
                "Detalii tehnice: {}".format(self.host, self.port, e)
            )
        self.server = server

    def send(self, msg):
        """
        Trimite un mesaj.
        Returneaza (ok, mesaj_eroare, cod_smtp). Ridica FatalSmtpError daca
        trebuie oprita rularea (autentificare / conexiune moarta).

        ATENTIE: in Python, SMTPException MOSTENESTE din OSError, deci
        exceptiile SMTP trebuie prinse INAINTEA ramurii OSError.
        """
        last_conn_error = None
        for attempt in (1, 2):
            try:
                if self.server is None:
                    self.connect()
                self.server.send_message(msg)
                return True, None, None
            except FatalSmtpError:
                raise
            except smtplib.SMTPAuthenticationError as e:
                raise FatalSmtpError(
                    "Sesiunea SMTP a fost respinsă (parolă invalidă).\n"
                    "Folosește o „Parolă de aplicație”.\n\nDetalii: {}".format(e)
                )
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                    smtplib.SMTPHeloError) as e:
                # conexiunea a picat (ex. pauza lunga intre trimiteri)
                last_conn_error = e
                self.server = None
                continue  # a doua incercare se reconecteaza automat
            except smtplib.SMTPException as e:
                # refuz de destinatar / date -> doar eroare pentru acest email
                code = smtp_error_code(e)
                text = "[{}] {}".format(code, e) if code else str(e)
                return False, text, code
            except (OSError, ssl.SSLError) as e:
                last_conn_error = e
                self.server = None
                continue
        raise FatalSmtpError(
            "Conexiunea SMTP s-a întrerupt și nu se poate reconecta la "
            "{}:{}\n\nDetalii: {}".format(self.host, self.port, last_conn_error)
        )
