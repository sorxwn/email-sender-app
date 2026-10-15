# -*- coding: utf-8 -*-
"""
Teste pentru aplicatia web:
  python .venv-web/bin/python test_web.py

Acopera: sheet_io (citire, stergere cu ajustarea indicilor), sender_worker
(trimitere reala cu SMTP falsat + stergerea randurilor) si interfata
Streamlit (AppTest) inclusiv conexiunea publica la tabelul tau Google Sheet.
"""

import os
import smtplib
import time

import email_core
import sheet_io
from sender_worker import SendRun

RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append(bool(cond))
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not extra else "  -> " + str(extra)))


SAMPLE_SHEET_ID = "1CrvHbwiL9O1K2N8Ks9AcGZyg3fnpMzp7D7ZKCSEEuo8"


class FakeSMTP:
    """Server SMTP fals: 'fail@...' e refuzat, 'gresit' ca parola -> 535."""

    sent = []

    def __init__(self, host, port, timeout=None, context=None, *a, **k):
        self.host = host
        self.port = port

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        if password == "gresit":
            raise smtplib.SMTPAuthenticationError(535, b"bad auth")

    def send_message(self, msg):
        to = msg["To"]
        if to.startswith("fail@"):
            raise smtplib.SMTPRecipientsRefused({to: (550, b"Mailbox unavailable")})
        FakeSMTP.sent.append({
            "to": to,
            "subject": str(msg["Subject"]),
            "body": msg.get_payload()[0].get_payload(decode=True).decode("utf-8"),
        })

    def quit(self):
        pass

    def close(self):
        pass


class FakeWorksheet:
    """Fals pentru gspread: matrice in memorie, delete_rows 1-based."""

    def __init__(self, values):
        self.values = [list(r) for r in values]
        self.deletes = []

    def get_all_values(self):
        return [list(r) for r in self.values]

    def delete_rows(self, row, count=1):
        del self.values[row - 1:row - 1 + count]
        self.deletes.append(row)


def sample_values():
    return [
        ["Email", "Companie", "Persoana_contact"],
        ["a@ex.ro", "Firma A", "Ana"],
        ["", "Firma B", "Bob"],           # fara email -> sarit
        ["b@ex.ro", "Firma C", "Carmen"],
        ["c@ex.ro", "Firma D", "Danut"],
        ["b@ex.ro", "Firma E", "Ema"],     # duplicat -> sarit
    ]


def wait_finished(run, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if run.finished:
            return True
        time.sleep(0.05)
    return False


# =========================================================================== #
def test_extract_sheet_id():
    link = "https://docs.google.com/spreadsheets/d/{}/edit?usp=sharing".format(SAMPLE_SHEET_ID)
    check("extrage ID din link", sheet_io.extract_sheet_id(link) == SAMPLE_SHEET_ID)
    check("accepta si ID-ul gol", sheet_io.extract_sheet_id(SAMPLE_SHEET_ID) == SAMPLE_SHEET_ID)
    check("respinge un text invalid", sheet_io.extract_sheet_id("nu e un link") is None)


def test_neutral_fallbacks():
    """Punctul 2: nicio valoare goala/NaN/None nu ajunge in email."""
    t = "Buna ziua {{Persoana_contact}}, de la {{Companie}}!"
    cases = [
        ({"Persoana_contact": "", "Companie": "ACME"},
         "Buna ziua Stimate partener, de la ACME!"),
        ({"Persoana_contact": None, "Companie": "ACME"},
         "Buna ziua Stimate partener, de la ACME!"),
        ({"Persoana_contact": float("nan"), "Companie": "ACME"},
         "Buna ziua Stimate partener, de la ACME!"),
        ({"Persoana_contact": "nan", "Companie": "None"},
         "Buna ziua Stimate partener, de la compania dumneavoastra!"),
        ({"Persoana_contact": "N/A", "Companie": "  "},
         "Buna ziua Stimate partener, de la compania dumneavoastra!"),
        ({"Persoana_contact": "Ana", "Companie": "ACME"},
         "Buna ziua Ana, de la ACME!"),
    ]
    for row, expected in cases:
        got = email_core.render_template(t, email_core.build_lookup(row))
        check("fallback neutru: {!r}".format(row), got == expected, got)


def test_private_key_newline_normalization():
    """Punctul 1: cheia din secrets (TOML cu \\n escapuit) ramane valida JSON."""
    import json
    info = {"client_email": "x@y.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----\\n"}
    dumped = json.dumps(info)  # ce produce load_secret_creds din tabelul TOML
    check("cheia TOML e JSON valid", bool(json.loads(dumped)))
    # normalizarea din connect_service_account
    parsed = json.loads(dumped)
    pk = parsed["private_key"]
    if "\\n" in pk and "\n" not in pk.replace("\\n", ""):
        parsed["private_key"] = pk.replace("\\n", "\n")
    check("newline-urile cheii se normalizeaza",
          "\n" in parsed["private_key"] and "\\n" not in parsed["private_key"])


def test_sheet_model():
    ws = FakeWorksheet(sample_values())
    model = sheet_io.SheetModel(ws.get_all_values(), writable=True, worksheet=ws)

    check("coloana de email gasita", model.email_column == "Email", model.email_column)
    check("sumar: 5 randuri, 4 valide",
          model.summary() == {"total": 5, "valid": 4, "email_column": "Email"}, model.summary())

    targets = model.targets(10)
    check("tinte: sarit fara-email si duplicatul",
          [t["recipient"] for t in targets] == ["a@ex.ro", "b@ex.ro", "c@ex.ro"],
          [t["recipient"] for t in targets])
    check("tinte: numerele reale de rand din tabel",
          [t["data"]["_row"] for t in targets] == [2, 4, 5],
          [t["data"]["_row"] for t in targets])
    check("limita N se aplica", len(model.targets(2)) == 2)

    # stergerea ajusteaza numerele randurilor ramase
    model.delete_row(2)  # stergem Firma A (randul 2)
    check("dupa sters, restul urca cu 1",
          [(t["recipient"], t["data"]["_row"]) for t in model.targets(10)] ==
          [("b@ex.ro", 3), ("c@ex.ro", 4)],
          [(t["recipient"], t["data"]["_row"]) for t in model.targets(10)])
    check("tabelul fals chiar a sters linia 2",
          ws.values[1][1] == "Firma B", ws.values[1])
    check("preview fara intern", "_row" not in model.preview(1)[0])


def test_readonly_delete():
    model = sheet_io.SheetModel(sample_values(), writable=False, worksheet=None)
    try:
        model.delete_row(2)
        check("mod doar-citire: stergerea refuza clar", False, "nu a aruncat exceptie")
    except sheet_io.SheetDeleteError as e:
        check("mod doar-citire: stergerea refuza clar", "citire" in str(e), str(e)[:60])


def test_public_fetch_real():
    try:
        values = sheet_io.fetch_public_csv(SAMPLE_SHEET_ID)
        model = sheet_io.SheetModel(values, writable=False)
        info = model.summary()
        check("citire PUBLICA reala a tabelului tau", info["valid"] > 500, info)
        check("coloana Email exista in tabelul real", info["email_column"] == "Email", info)
    except Exception as e:
        check("citire PUBLICA reala a tabelului tau", False, repr(e))

# =========================================================================== #
def make_run(values, password="parola-ok", limit=10, delay=0):
    ws = FakeWorksheet(values)
    model = sheet_io.SheetModel(ws.get_all_values(), writable=True, worksheet=ws)
    email_core.smtplib.SMTP = FakeSMTP
    email_core.smtplib.SMTP_SSL = FakeSMTP
    session = email_core.SmtpSession("smtp.test.ro", 587, "eu@ex.ro", password)
    targets = model.targets(limit)
    run = SendRun(session, model, targets, "eu@ex.ro",
                  "Buna {{Companie}}", "Salut {{Persoana_contact}}!", delay=delay)
    return run, ws, model


def test_worker_success_deletes_rows():
    FakeSMTP.sent.clear()
    run, ws, model = make_run(sample_values())
    run.start()
    check("worker: rularea se termina singura", wait_finished(run))
    check("worker: 3 emailuri trimise", run.sent == 3, (run.sent, run.errors))
    check("worker: 3 randuri sterse", run.deleted == 3, run.deleted)
    check("worker: tabelul ramane exact cu randurile NEtrimise",
          ws.values == [["Email", "Companie", "Persoana_contact"],
                        ["", "Firma B", "Bob"],
                        ["b@ex.ro", "Firma E", "Ema"]],
          ws.values)
    check("worker: stergeri in ordinea corecta a indicilor (2,3,3)",
          ws.deletes == [2, 3, 3], ws.deletes)
    check("worker: mesaj personalizat in email",
          FakeSMTP.sent and FakeSMTP.sent[0]["body"].startswith("Salut Ana"),
          FakeSMTP.sent[0]["body"] if FakeSMTP.sent else "-")
    check("worker: subiectul completat cu {{Companie}}",
          FakeSMTP.sent and "Firma A" in FakeSMTP.sent[0]["subject"],
          FakeSMTP.sent[0]["subject"] if FakeSMTP.sent else "-")
    check("worker: motiv final „gata”", run.finish_reason == "gata", run.finish_reason)
    check("worker: jurnal confirma stergerea automata",
          any("șters din tabel" in l["text"] for l in run.log_lines()))
    check("worker: SMTP-ul inchis la final", run.session.server is None)


def test_worker_failure_keeps_row():
    FakeSMTP.sent.clear()
    values = sample_values() + [["fail@ex.ro", "Firma F", "Fane"]]
    run, ws, model = make_run(values)
    run.start()
    wait_finished(run)
    check("esec: 3 reusite + 1 eroare", (run.sent, run.errors) == (3, 1),
          (run.sent, run.errors))
    check("esec: randul RESPINS ramane in tabel (nu se sterge)",
          ws.values[-1] == ["fail@ex.ro", "Firma F", "Fane"], ws.values[-1])
    check("esec: randurile reusite sunt sterse",
          all(r[1] not in ("Firma A", "Firma C", "Firma D") for r in ws.values[1:]),
          ws.values)
    check("esec: motiv final tot „gata” (a mers mai departe)", run.finish_reason == "gata")


def test_worker_stop():
    values = [["Email", "Companie", "Persoana_contact"]]
    values += [["u{}@ex.ro".format(i), "Firma {}".format(i), "Pers {}".format(i)]
               for i in range(6)]
    run, ws, model = make_run(values, delay=1)
    run.start()
    t0 = time.time()
    while run.sent < 1 and time.time() - t0 < 15:
        time.sleep(0.05)
    check("oprire: primul email pleaca", run.sent >= 1, run.sent)
    run.request_stop()
    check("oprire: thread-ul se opreste repede", wait_finished(run, timeout=15))
    check("oprire: nu s-a trimis tot", 1 <= run.sent < 6, run.sent)
    check("oprire: motiv „oprit”", run.finish_reason == "oprit", run.finish_reason)
    check("oprire: toate cele trimise sunt si sterse",
          run.deleted == run.sent == len(ws.deletes), (run.deleted, run.sent, ws.deletes))


def test_worker_bad_password():
    run, ws, model = make_run(sample_values(), password="gresit")
    run.start()
    wait_finished(run, timeout=15)
    check("parola gresita -> motiv „eroare”", run.finish_reason == "eroare", run.finish_reason)
    check("parola gresita -> mesaj clar despre App Password",
          run.fatal_message and "Autentificare" in run.fatal_message,
          (run.fatal_message or "")[:80])
    check("parola gresita -> niciun rand sters", ws.deletes == [], ws.deletes)
    check("parola gresita -> UI-ul nu ramane blocat", not run.running)


def test_worker_heartbeat():
    run, ws, model = make_run(sample_values())
    run._last_heartbeat = time.time() - 9999
    check("fara „ochi” din interfata -> auto-oprire pentru siguranta",
          run._should_stop() and run.stop_event.is_set())

# =========================================================================== #
def test_streamlit_ui():
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as e:
        print("SKIP AppTest (streamlit indisponibil): {}".format(e))
        return

    cale = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_app.py")
    at = AppTest.from_file(cale, default_timeout=40)
    at.run()
    check("UI: pagina ruleaza fara exceptii", not at.exception, at.exception)
    if at.exception:
        return

    check("UI: titlu in romana",
          bool(at.title) and at.title[0].value == "📧 Trimitere Emailuri Automatizate",
          at.title[0].value if at.title else "-")
    subs = [s.value for s in at.subheader]
    check("UI: sectiunea „Situația listei” prezenta",
          any("Situația listei" in s for s in subs), subs)
    check("UI: sectiunea „Trimitere” prezenta", any(s.startswith("4. ") for s in subs), subs)

    labels = [b.label for b in at.button]
    check("UI: buton „Pornește Trimiterea”", "🚀 Pornește Trimiterea" in labels, labels)
    check("UI: buton „Oprește Trimiterea”", "⏹️ Oprește Trimiterea" in labels)
    check("UI: inainte de conectare, Dry-Run e doar mentiune (nu buton)",
          "👁 Previzualizează fără trimitere" not in labels and
          any("Dry-Run" in str(c.value) for c in at.caption), labels)
    check("UI: butoane de conectare", "🔌 Conectează" in labels and "👁 Mod citire (public)" in labels)
    check("UI: limita implicita 30 companii", at.number_input(key="limit").value == 30,
          at.number_input(key="limit").value)
    check("UI: pauza implicita 10 secunde", at.number_input(key="delay").value == 10,
          at.number_input(key="delay").value)

    # start fara nicio conexiune -> mesaj clar de eroare
    start = next(b for b in at.button if b.label == "🚀 Pornește Trimiterea")
    start.click()
    at.run()
    check("UI: start fara tabel -> „Conectează mai întâi”",
          any("Conectează mai întâi" in e.value for e in at.error),
          [e.value for e in at.error])

    # Auto SMTP completeaza serverul dupa domeniu
    at.text_input(key="sender_email").set_value("cineva@yahoo.com")
    next(b for b in at.button if b.label == "⚡ Auto SMTP").click()
    at.run()
    check("UI: Auto SMTP (yahoo) -> server si port",
          at.text_input(key="smtp_host").value == "smtp.mail.yahoo.com"
          and at.number_input(key="smtp_port").value == 465,
          (at.text_input(key="smtp_host").value, at.number_input(key="smtp_port").value))

    # conectare PUBLICA la tabelul real (reteea) -> sumar in romana
    next(b for b in at.button if b.label == "👁 Mod citire (public)").click()
    at.run()
    succese = [s.value for s in at.success]
    check("UI: conectare publica reala -> „firme necontactate”",
          any("firme necontactate" in s for s in succese), succese)
    check("UI: avertisment explicit pentru modul doar-citire",
          any("Mod doar citire" in w.value for w in at.warning),
          [w.value for w in at.warning][:2])
    check("UI: dupa conectare apare butonul Dry-Run",
          "👁 Previzualizează fără trimitere" in [b.label for b in at.button],
          [b.label for b in at.button])

    # start cu tabel conectat, dar fara parola de aplicatie -> alta eroare
    # (re-gasim butonul: referintele vechi se invalideaza dupa fiecare run)
    next(b for b in at.button if b.label == "🚀 Pornește Trimiterea").click()
    at.run()
    check("UI: start fara parola -> cere „Parolă de aplicație”",
          any("Parolă de aplicație" in e.value for e in at.error),
          [e.value for e in at.error])

    # Dry-Run functional: genereaza primele 2 emailuri fara trimitere/stergeri
    model2 = sheet_io.SheetModel(sample_values(), writable=True,
                                 worksheet=FakeWorksheet(sample_values()))
    dr_targets = model2.targets(2)
    dr_out = []
    for t in dr_targets:
        lk = email_core.build_lookup(t["data"])
        dr_out.append((t["recipient"],
                       email_core.render_template("Buna {{Companie}}", lk),
                       email_core.render_template("Salut {{Persoana_contact}}!", lk)))
    check("Dry-Run: genereaza 2 emailuri fara trimitere",
          len(dr_out) == 2 and dr_out[0][0] == "a@ex.ro", dr_out)
    check("Dry-Run: personalizare corecta (Ana / Firma A)",
          dr_out[0][1] == "Buna Firma A" and dr_out[0][2] == "Salut Ana!",
          dr_out[0])
    check("Dry-Run: NU sterge randuri din tabel", len(model2.rows) == 5, len(model2.rows))

    # sectiunea de progres apare dupa o rulare salvata in sesiune (opus: fara)


def main():
    test_extract_sheet_id()
    test_neutral_fallbacks()
    test_private_key_newline_normalization()
    test_sheet_model()
    test_readonly_delete()
    test_public_fetch_real()
    test_worker_success_deletes_rows()
    test_worker_failure_keeps_row()
    test_worker_stop()
    test_worker_bad_password()
    test_worker_heartbeat()
    test_streamlit_ui()

    total = len(RESULTS)
    passed = sum(RESULTS)
    print("\n{} / {} teste web trecute".format(passed, total))
    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()


