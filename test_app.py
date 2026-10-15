# -*- coding: utf-8 -*-
"""
Teste pentru app.py
Rulare:  python3 test_app.py   (merge si cu Python-ul sistemului de pe macOS)
Nu trimit emailuri reale - SMTP-ul este falsat.
"""

import os
import smtplib
import tempfile
import time

import app as appmod
import email_core

RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append(bool(cond))
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not extra else "  -> " + str(extra)))


# ---------------------------------------------------------------------------
#  Teste de logica (fara GUI)
# ---------------------------------------------------------------------------
def test_valid_email():
    check("valid_email accepta adrese valide",
          appmod.valid_email("nume@gmail.com") and appmod.valid_email("a.b+tag@sub.domain.ro"))
    check("valid_email respinge adrese invalide",
          not appmod.valid_email("faraloc") and not appmod.valid_email("a@b")
          and not appmod.valid_email("") and not appmod.valid_email("a b@c.d"))


def test_normalize_header():
    check("normalize_header unify spatii/majuscule/diacritice",
          appmod.normalize_header(" Persoana contact ") == "persoana_contact"
          and appmod.normalize_header("E-Mail") == "e_mail"
          and appmod.normalize_header("Oráș") == "oras")


def test_load_real_csv():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cv_emails_Sheet1.csv")
    if not os.path.isfile(path):
        check("CSV real incarcat (fisierul lipseste)", False, path)
        return
    headers, rows = appmod.load_csv_rows(path)
    check("CSV real: minim 600 randuri", len(rows) >= 600, len(rows))
    check("CSV real: coloane asteptate", "Email" in headers and "Companie" in headers, headers)
    col = appmod.find_email_column(headers, rows)
    check("CSV real: gaseste coloana Email", col == "Email", col)
    valide = sum(1 for r in rows if appmod.valid_email(str(r.get(col, ""))))
    check("CSV real: >500 adrese valide", valide > 500, valide)
    # variabila din mesaj trebuie sa se inlocuiasca cu valoarea reala
    row = rows[0]
    lookup = appmod.build_lookup(row)
    text = appmod.render_template("Buna {{Persoana_contact}} de la {{Companie}}", lookup)
    check("CSV real: personalizare functioneaza",
          row["Persoana_contact"] in text and row["Companie"] in text, text)


def test_find_email_column_variants():
    rows = [{"E-mail": "a@b.c"}, {"x": "1"}]
    check("gaseste coloana 'E-mail'", appmod.find_email_column(["E-mail"], rows) == "E-mail")
    rows2 = [{" contact_email ": "a@b.c"}]
    check("gaseste coloana ' contact_email '",
          appmod.find_email_column([" contact_email "], rows2) == " contact_email ")
    rows3 = [{"Telefon": "123", "Adresa": "a@b.c"}]
    check("fallback: coloana cu cele mai multe @",
          appmod.find_email_column(["Telefon", "Adresa"], rows3) == "Adresa")
    check("returneaza None cand nu exista email",
          appmod.find_email_column(["Telefon"], [{"Telefon": "123"}]) is None)


def test_render_template():
    lookup = appmod.build_lookup({"Companie": "Nordic AS", "Persoana_contact": "Ola", "Oras": "Bodø"})
    check("inlocuire {{Companie}}",
          appmod.render_template("De la {{Companie}}", lookup) == "De la Nordic AS")
    check("inlocuire variabila cu spatiu",
          appmod.render_template("Buna {{ persoana contact }}", lookup) == "Buna Ola")
    check("variabila necunoscuta devine goala",
          appmod.render_template("x{{Lipsa}}y", lookup) == "xy")
    unknown = appmod.find_unknown_vars("Buna {{Companie}} {{Lipsa}}")
    check("find_unknown_vars gaseste toate variabilele",
          unknown == {"Companie", "Lipsa"}, unknown)
    headers_norm = {"companie", "persoana_contact"}
    real_unknown = {v for v in unknown if appmod.normalize_header(v) not in headers_norm}
    check("filtrarea dupa coloane ramane doar {Lipsa}", real_unknown == {"Lipsa"}, real_unknown)


def test_build_message_encoding():
    msg = appmod.build_message("eu@test.ro", "tu@test.ro",
                               "Bună ziua – Nordics øå", "Corp mesaj æøå")
    raw = msg.as_string()
    check("subiect non-ASCII codat corect (RFC 2047)", "=?utf-8?" in raw.lower(), raw[:200])
    check("corpul mesajului e UTF-8", "charset=\"utf-8\"" in raw or "charset=utf-8" in raw)
    check("antete To/From pastrate", msg["To"] == "tu@test.ro" and msg["From"] == "eu@test.ro")


def test_json_roundtrip():
    tmp = os.path.join(tempfile.gettempdir(), "test_setari.json")
    data = {"email": "a@b.c", "subject": "Bună"}
    appmod.write_json(tmp, data)
    back = appmod.read_json(tmp)
    check("write_json/read_json pastreaza diacritice", back == data, back)
    check("read_json pe fisier inexistent -> {}", appmod.read_json(tmp + ".nu") == {})
    os.path.exists(tmp) and os.remove(tmp)


def test_smtp_connect_failure():
    s = appmod.SmtpSession("127.0.0.1", 1, "a@b.ro", "parola")
    try:
        s.connect()
        check("conexiune esuata ridica FatalSmtpError", False, "nu a ridicat exceptia")
    except appmod.FatalSmtpError as e:
        check("conexiune esuata ridica FatalSmtpError", "Nu mă pot conecta" in str(e), str(e)[:80])
    except Exception as e:
        check("conexiune esuata ridica FatalSmtpError", False, repr(e))

# ---------------------------------------------------------------------------
#  Test end-to-end cu GUI (fereastra ascunsa) + SMTP falsat
# ---------------------------------------------------------------------------
DIALOGS = []


class FakeSMTP:
    """Server SMTP fals: 'fail@...' este refuzat, restul 'se trimite'."""

    sent = []

    def __init__(self, host, port, timeout=None, context=None, *a, **k):
        self.host = host
        self.port = port

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        if password != "parola-buna":
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


def pump_until(root, cond, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        root.update()
        if cond():
            return True
        time.sleep(0.02)
    return False


def write_csv(path, emails):
    with open(path, "w", encoding="utf-8") as f:
        f.write("Email,Companie,Persoana_contact\n")
        for i, e in enumerate(emails):
            f.write("{},Firma {},Persoana {}\n".format(e, i, i))


def make_app(root, csv_path, limit, delay):
    a = appmod.EmailSenderApp(root)
    a.csv_path = csv_path
    for ent in (a.ent_email, a.ent_pass, a.ent_smtp, a.ent_port,
                a.ent_limit, a.ent_delay, a.ent_subject):
        ent.delete(0, "end")
    a.txt_body.delete("1.0", "end")
    a.ent_email.insert(0, "expeditor@gmail.com")
    a.ent_pass.insert(0, "parola-buna")
    a.ent_smtp.insert(0, "smtp.gmail.com")
    a.ent_port.insert(0, "587")
    a.ent_limit.insert(0, str(limit))
    a.ent_delay.insert(0, str(delay))
    a.ent_subject.insert(0, "Buna {{Companie}}")
    a.txt_body.insert("1.0", "Salut {{Persoana_contact}} de la {{Companie}}!")
    a.var_resume.set(1)
    return a


def sent_key(path):
    return os.path.basename(path).lower()


def test_gui_flow():
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:
        print("SKIP teste GUI (nu pot deschide Tk): {}".format(e))
        return
    root.withdraw()

    # nu lasam ferestre modale sa blocheze testele
    appmod.messagebox.showinfo = lambda *a, **k: DIALOGS.append(("info", str(a)))
    appmod.messagebox.showerror = lambda *a, **k: DIALOGS.append(("error", str(a)))
    appmod.messagebox.showwarning = lambda *a, **k: DIALOGS.append(("warning", str(a)))
    appmod.messagebox.askyesno = lambda *a, **k: True
    email_core.smtplib.SMTP = FakeSMTP
    email_core.smtplib.SMTP_SSL = FakeSMTP
    appmod.STATE_FILE = os.path.join(tempfile.gettempdir(), "test_progres_trimis.json")
    appmod.SETTINGS_FILE = os.path.join(tempfile.gettempdir(), "test_setari_app.json")
    if os.path.isfile(appmod.STATE_FILE):
        os.remove(appmod.STATE_FILE)

    tmpdir = tempfile.mkdtemp(prefix="mailtest_")
    csv1 = os.path.join(tmpdir, "contacte.csv")
    write_csv(csv1, ["a@ex.ro", "b@ex.ro", "fail@ex.ro", "c@ex.ro", "d@ex.ro"])

    # ---- rularea 1: 4 reusite, 1 respinsa ------------------------------
    a = make_app(root, csv1, limit=10, delay=0)
    a.start_sending()
    done = pump_until(root, lambda: not a.running, timeout=25)
    check("rularea 1 se termina singura", done)
    root.update()

    key = sent_key(csv1)
    trimise = a.state.get("trimise", {}).get(key, [])
    check("rularea 1: 4 emailuri marcate ca trimise (fail@ nu)", len(trimise) == 4, trimise)
    check("rularea 1: SMTP fals a primit 4 mesaje", len(FakeSMTP.sent) == 4, len(FakeSMTP.sent))
    check("rularea 1: mesajul e personalizat",
          FakeSMTP.sent and FakeSMTP.sent[0]["body"].startswith("Salut Persoana 0"),
          FakeSMTP.sent[0]["body"] if FakeSMTP.sent else "-")
    check("rularea 1: subiectul cu {{Companie}} e completat",
          FakeSMTP.sent and "Firma" in FakeSMTP.sent[0]["subject"],
          FakeSMTP.sent[0]["subject"] if FakeSMTP.sent else "-")
    check("rularea 1: dialogul de finalizare afisat",
          any(d[0] == "info" and "finalizată" in d[1] for d in DIALOGS), DIALOGS)
    check("rularea 1: progresul ajunge la capat",
          str(a.lbl_progress["text"]).startswith("5/5"), a.lbl_progress["text"])

    # ---- rularea 2: reluare -> nu retrimite cele 4 ----------------------
    DIALOGS.clear()
    a2 = make_app(root, csv1, limit=10, delay=0)
    a2.start_sending()
    done2 = pump_until(root, lambda: not a2.running, timeout=25)
    check("rularea 2 se termina", done2)
    root.update()
    check("rularea 2: nu s-a retrimis nimic (doar fail@ a fost incercat)",
          any(d[0] == "info" and "S-au trimis 0" in d[1] for d in DIALOGS), DIALOGS)
    check("rularea 2: starea ramane la 4 trimise",
          len(a2.state.get("trimise", {}).get(key, [])) == 4)

    # ---- rularea 3: butonul OPRESTE opreste la timp ---------------------
    DIALOGS.clear()
    csv2 = os.path.join(tmpdir, "contacte2.csv")
    write_csv(csv2, ["n1@ex.ro", "n2@ex.ro", "n3@ex.ro",
                     "n4@ex.ro", "n5@ex.ro", "n6@ex.ro"])
    a3 = make_app(root, csv2, limit=6, delay=2)
    a3.start_sending()
    ok_first = pump_until(
        root, lambda: len(a3.state.get("trimise", {}).get(sent_key(csv2), [])) >= 1, timeout=20)
    check("rularea 3: primul email pleaca", ok_first)
    a3.stop_sending()
    stopped = pump_until(root, lambda: not a3.running, timeout=15)
    root.update()
    check("rularea 3: OPRESTE opreste rularea", stopped)
    n = len(a3.state.get("trimise", {}).get(sent_key(csv2), []))
    check("rularea 3: nu s-a trimis tot (oprit dupa 1-2)", 1 <= n < 6, n)
    check("rularea 3: dialog de oprire afisat",
          any(d[0] == "info" and "oprit" in d[1].lower() for d in DIALOGS), DIALOGS)

    # ---- rularea 4: parola gresita -> FatalSmtpError --------------------
    DIALOGS.clear()
    csv3 = os.path.join(tmpdir, "contacte3.csv")
    write_csv(csv3, ["x@ex.ro"])
    a4 = make_app(root, csv3, limit=1, delay=0)
    a4.ent_pass.delete(0, "end"); a4.ent_pass.insert(0, "parola-proasta")
    a4.start_sending()
    pump_until(root, lambda: not a4.running, timeout=20)
    root.update()
    check("rularea 4: parola gresita produce eroare clara",
          any(d[0] == "error" and "Autentificare" in d[1] for d in DIALOGS), DIALOGS)
    check("rularea 4: UI-ul se deblocheaza dupa eroare",
          str(a4.btn_start["state"]) == "normal" and str(a4.btn_stop["state"]) == "disabled",
          (a4.btn_start["state"], a4.btn_stop["state"]))

    # ---- rularea 5: functionalitati AUXILIARE ----------------------------
    a5 = make_app(root, csv1, limit=1, delay=0)

    # Auto SMTP completeaza serverul dupa domeniu
    a5.ent_email.delete(0, "end"); a5.ent_email.insert(0, "cineva@yahoo.com")
    a5.auto_smtp()
    check("auto_smtp completeaza yahoo",
          a5.ent_smtp.get() == "smtp.mail.yahoo.com" and a5.ent_port.get() == "465",
          (a5.ent_smtp.get(), a5.ent_port.get()))
    a5.ent_email.delete(0, "end"); a5.ent_email.insert(0, "cineva@domeniu-necunoscut.ro")
    a5.auto_smtp()
    check("auto_smtp nu strica ce e necunoscut (lasa ce e acolo)",
          a5.ent_smtp.get() == "smtp.mail.yahoo.com", a5.ent_smtp.get())

    # validarea campurilor
    a5.ent_email.delete(0, "end"); a5.ent_email.insert(0, "nu-este-email")
    try:
        a5._collect_config(need_csv=False, need_message=False)
        check("validare email invalid", False, "nu a ridicat ValueError")
    except ValueError:
        check("validare email invalid", True)
    a5.ent_email.delete(0, "end"); a5.ent_email.insert(0, "ok@ex.ro")
    a5.ent_limit.delete(0, "end"); a5.ent_limit.insert(0, "zero")
    try:
        a5._collect_config(need_csv=False, need_message=False)
        check("validare limita non-numerica", False, "nu a ridicat ValueError")
    except ValueError:
        check("validare limita non-numerica", True)
    a5.ent_limit.delete(0, "end"); a5.ent_limit.insert(0, "25")

    # salvarea si reincarcarea setarilor
    a5.ent_subject.delete(0, "end"); a5.ent_subject.insert(0, "Subiect salvat")
    a5.var_remember.set(1)
    a5.ent_pass.delete(0, "end"); a5.ent_pass.insert(0, "parola-mea")
    a5._save_settings()
    a6 = appmod.EmailSenderApp(root)
    check("setarile se reincarca la deschidere",
          a6.ent_subject.get() == "Subiect salvat"
          and a6.ent_pass.get() == "parola-mea"
          and a6.ent_email.get() == "ok@ex.ro",
          (a6.ent_subject.get(), a6.ent_pass.get(), a6.ent_email.get()))
    a6.ent_pass.delete(0, "end"); a6.var_remember.set(0); a6._save_settings()
    a7 = appmod.EmailSenderApp(root)
    check("parola nu se salveaza daca 'Reține parola' e stins",
          a7.ent_pass.get() == "" and a7.var_remember.get() == 0, a7.ent_pass.get())

    root.destroy()


def main():
    test_valid_email()
    test_normalize_header()
    test_load_real_csv()
    test_find_email_column_variants()
    test_render_template()
    test_build_message_encoding()
    test_json_roundtrip()
    test_smtp_connect_failure()
    test_gui_flow()

    total = len(RESULTS)
    passed = sum(RESULTS)
    print("\n{} / {} teste trecute".format(passed, total))
    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()


