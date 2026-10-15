# -*- coding: utf-8 -*-
"""
Conectare si operatiuni pe Google Sheet (impartasita de aplicatia web).

- SheetModel: copia locala a tabelului. Tine minte NUMARUL REAL al
  fiecarui rand din Google Sheet, il ajusteaza dupa fiecare stergere
  (altfel s-ar sterge randuri gresite din cauza deplasarii indicilor)
  si ofera lista de tinte valide (primele N firme).
- Conectare: service account (gspread, citire + STERGERE) sau
  link public al tabelului (doar citire).
"""

import csv as _csv
import io as _io
import json
import re
import urllib.request

from email_core import find_email_column, valid_email

SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9\-_]+)")
PLAIN_ID_RE = re.compile(r"^[a-zA-Z0-9\-_]{20,}$")


class SheetDeleteError(Exception):
    """Nu am putut sterge randul din Google Sheet."""


def extract_sheet_id(url_or_id):
    """Extrage ID-ul dintr-un link Google Sheet (sau accepta ID-ul gol)."""
    text = (url_or_id or "").strip()
    m = SHEET_ID_RE.search(text)
    if m:
        return m.group(1)
    if PLAIN_ID_RE.match(text):
        return text
    return None


def fetch_public_csv(spreadsheet_id):
    """Citeste un Google Sheet public ca CSV (fara posibilitatea de stergere)."""
    url = "https://docs.google.com/spreadsheets/d/{}/export?format=csv".format(spreadsheet_id)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    text = raw.decode("utf-8-sig", errors="replace")
    return [list(row) for row in _csv.reader(_io.StringIO(text))]


def connect_service_account(credentials_json_text):
    """
    Conectare gspread cu un service account (JSON lipit in aplicatie
    sau venit din st.secrets pe Streamlit Cloud).
    Returneaza (client_gspread, adresa_client_email).
    """
    import gspread  # import intarziat -> modulul "doar citire" merge fara gspread
    info = json.loads(credentials_json_text)
    # Pe Streamlit Cloud, cheia privata vine din TOML cu "\n" escapuit
    # (text literal) in loc de newline real -> o normalizam aici.
    pk = info.get("private_key", "")
    if isinstance(pk, str) and "\\n" in pk and "\n" not in pk.replace("\\n", ""):
        info["private_key"] = pk.replace("\\n", "\n")
    client = gspread.service_account_from_dict(info)
    return client, info.get("client_email", "?")


class SheetModel:
    """O copie a unui tabel Google Sheets, cu numere de rand reale."""

    def __init__(self, values, writable=False, worksheet=None):
        values = [[str(c).strip() for c in (row or [])] for row in (values or [])]
        if not values or not any(values[0]):
            raise ValueError("Tabelul nu are rând de antete (prima linie).")
        header_row = values[0]
        self.headers = [h for h in header_row if h]
        self.writable = bool(writable) and worksheet is not None
        self.worksheet = worksheet

        rows = []
        for idx, raw in enumerate(values[1:], start=2):  # randul 1 = antetul
            row = {}
            for j, h in enumerate(header_row):
                if not h:
                    continue
                row[h] = raw[j].strip() if j < len(raw) else ""
            if any(v for k, v in row.items()):
                row["_row"] = idx  # numarul REAL al randului in Google Sheet
                rows.append(row)
        self.rows = rows
        self.email_column = find_email_column(self.headers, rows)

    # ------------------------------------------------------------- informatii
    @property
    def valid_rows(self):
        if not self.email_column:
            return []
        return [r for r in self.rows if valid_email(r.get(self.email_column, ""))]

    def summary(self):
        return {
            "total": len(self.rows),
            "valid": len(self.valid_rows),
            "email_column": self.email_column,
        }

    def preview(self, n=15):
        """Primele n randuri pentru afisare (fara intern)."""
        out = []
        for r in self.rows[:n]:
            out.append({h: r.get(h, "") for h in self.headers})
        return out

    def targets(self, limit):
        """
        Primele N randuri valide, deduplicate dupa email:
        [{recipient, data}, ...] in ordinea din tabel.

        ATENTIE: „data" este chiar dictionarul din self.rows, deci
        data["_row"] (numarul REAL din tabel) se ACTUALIZEAZA automat
        dupa fiecare stergere - il citim chiar inainte de stergere.
        """
        out = []
        seen = set()
        for r in self.valid_rows:
            recipient = r.get(self.email_column, "").strip()
            key = recipient.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"recipient": recipient, "data": r})
            if len(out) >= int(limit):
                break
        return out

    # ---------------------------------------------------------------- stergere
    def delete_row(self, sheet_row):
        """
        Sterge un rand din Google Sheet si ajusteaza numerele randurilor
        ramase (cele de sub el urca cu 1). Arunca SheetDeleteError daca nu
        se poate (sau daca suntem in mod doar-citire).
        """
        if not self.writable or self.worksheet is None:
            raise SheetDeleteError(
                "Mod doar citire: nu pot șterge rânduri. "
                "Conectează un service account pentru ștergerea automată.")
        try:
            try:
                self.worksheet.delete_rows(sheet_row, 1)
            except TypeError:
                self.worksheet.delete_rows(sheet_row)  # gspread vechi
        except SheetDeleteError:
            raise
        except Exception as e:
            raise SheetDeleteError(str(e))
        # ajustam indicii locali: randul dispare, cele de sub el urca cu 1
        self.rows = [r for r in self.rows if r["_row"] != sheet_row]
        for r in self.rows:
            if r["_row"] > sheet_row:
                r["_row"] -= 1
        return True
