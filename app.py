# -*- coding: utf-8 -*-
"""
Trimitere Emailuri Automatizate - aplicatie desktop pentru Windows/Mac.

Rulare directa:   python app.py
Build executabil: build.bat  (Windows) sau vezi README.md

Totul ruleaza local: fara server, fara hosting, fara VPS.
"""

import os
import queue
import sys
import threading
import time

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from email_core import (
    SMTP_PRESETS,
    TRANSIENT_CODES,
    FatalSmtpError,
    SmtpSession,
    build_lookup,
    build_message,
    find_email_column,
    find_unknown_vars,
    load_csv_rows,
    normalize_header,
    read_json,
    render_template,
    valid_email,
    write_json,
)

APP_TITLE = "Trimitere Emailuri Automatizate"
APP_VERSION = "1.2"

# Setarile si progresul se salveaza langa exe (sau langa app.py la rulare directa)
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SETTINGS_FILE = os.path.join(BASE_DIR, "setari.json")
STATE_FILE = os.path.join(BASE_DIR, "progres_trimis.json")


# --------------------------------------------------------------------------
#  Aplicatia (interfata grafica)
# --------------------------------------------------------------------------
class EmailSenderApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("940x820")
        self.root.minsize(780, 660)

        self.q = queue.Queue()          # mesaje de la thread-ul de lucru catre UI
        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()
        self.running = False
        self.csv_path = ""
        self.csv_headers = []
        self.csv_rows = []
        self.state = read_json(STATE_FILE)

        self._build_ui()
        self._load_settings()
        self._refresh_csv_info()

        self.root.after(80, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        root = self.root

        # ---- 1. Fisier CSV -------------------------------------------------
        f1 = tk.LabelFrame(root, text=" 1. Fișier CSV (lista de contacte) ", padx=10, pady=8)
        f1.pack(fill="x", padx=12, pady=(10, 4))

        row1 = tk.Frame(f1)
        row1.pack(fill="x")
        self.btn_csv = tk.Button(row1, text="📂 Alege fișierul CSV", command=self.load_csv)
        self.btn_csv.pack(side="left")
        self.lbl_csv = tk.Label(row1, text="Niciun fișier selectat", fg="gray")
        self.lbl_csv.pack(side="left", padx=10)
        self.btn_reset = tk.Button(row1, text="♻️ Șterge progresul", command=self.reset_progress)
        self.btn_reset.pack(side="right")

        self.lbl_csv_info = tk.Label(f1, text="", fg="#555")
        self.lbl_csv_info.pack(anchor="w", pady=(6, 0))

        # ---- 2. Cont de email ---------------------------------------------
        f2 = tk.LabelFrame(root, text=" 2. Contul tău de email (Gmail / Yahoo / Outlook) ", padx=10, pady=8)
        f2.pack(fill="x", padx=12, pady=4)
        f2.columnconfigure(1, weight=1)
        f2.columnconfigure(3, weight=1)

        tk.Label(f2, text="Email expeditor:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)
        self.ent_email = tk.Entry(f2, width=34)
        self.ent_email.grid(row=0, column=1, sticky="we", pady=2)

        tk.Label(f2, text="Parolă aplicație:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=2)
        self.ent_pass = tk.Entry(f2, width=34, show="•")
        self.ent_pass.grid(row=1, column=1, sticky="we", pady=2)

        tk.Label(f2, text="Server SMTP:").grid(row=0, column=2, sticky="e", padx=(14, 6), pady=2)
        self.ent_smtp = tk.Entry(f2, width=26)
        self.ent_smtp.grid(row=0, column=3, sticky="we", pady=2)

        tk.Label(f2, text="Port:").grid(row=1, column=2, sticky="e", padx=(14, 6), pady=2)
        self.ent_port = tk.Entry(f2, width=26)
        self.ent_port.grid(row=1, column=3, sticky="we", pady=2)

        row2 = tk.Frame(f2)
        row2.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.btn_auto = tk.Button(row2, text="⚡ Auto SMTP", command=self.auto_smtp)
        self.btn_auto.pack(side="left")
        self.btn_test = tk.Button(row2, text="🔌 Testează conexiunea", command=self.test_connection)
        self.btn_test.pack(side="left", padx=8)
        self.var_remember = tk.IntVar(value=1)
        tk.Checkbutton(row2, text="Reține parola pe acest calculator",
                       variable=self.var_remember).pack(side="left", padx=10)

        # ---- 3. Setari de trimitere ---------------------------------------
        f3 = tk.LabelFrame(root, text=" 3. Setări de trimitere ", padx=10, pady=8)
        f3.pack(fill="x", padx=12, pady=4)
        f3.columnconfigure(1, weight=1)
        f3.columnconfigure(3, weight=1)

        tk.Label(f3, text="Câte emailuri trimiți acum:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)
        self.ent_limit = tk.Entry(f3, width=12)
        self.ent_limit.grid(row=0, column=1, sticky="w", pady=2)

        tk.Label(f3, text="Pauză între emailuri (secunde):").grid(row=0, column=2, sticky="e", padx=(14, 6), pady=2)
        self.ent_delay = tk.Entry(f3, width=12)
        self.ent_delay.grid(row=0, column=3, sticky="w", pady=2)

        self.var_resume = tk.IntVar(value=1)
        tk.Checkbutton(
            f3, text="Sari peste emailurile deja trimise (recomandat – poți opri și relua oricând)",
            variable=self.var_resume,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

        # ---- 4. Mesaj ------------------------------------------------------
        f4 = tk.LabelFrame(root, text=" 4. Mesajul trimis ", padx=10, pady=8)
        f4.pack(fill="x", padx=12, pady=4)

        tk.Label(f4, text="Subiect:").pack(anchor="w")
        self.ent_subject = tk.Entry(f4)
        self.ent_subject.pack(fill="x", pady=(0, 4))

        tk.Label(
            f4,
            text="Corp mesaj – variabile: {{Companie}}, {{Persoana_contact}}, {{Oras}}, "
                 "{{Website}}, {{Telefon}} (orice coloană din CSV)",
            fg="#555",
        ).pack(anchor="w")
        body_frame = tk.Frame(f4)
        body_frame.pack(fill="both", expand=True, pady=(2, 0))
        self.txt_body = tk.Text(body_frame, height=6, wrap="word")
        scroll = tk.Scrollbar(body_frame, command=self.txt_body.yview)
        self.txt_body.configure(yscrollcommand=scroll.set)
        self.txt_body.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # ---- Butoane de control -------------------------------------------
        ctrl = tk.Frame(root)
        ctrl.pack(fill="x", padx=12, pady=(8, 4))

        self.btn_start = tk.Button(
            ctrl, text="▶  PORNEȘTE TRIMITEREA", bg="#4CAF50", fg="white",
            font=("Arial", 11, "bold"), height=2, command=self.start_sending,
        )
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn_stop = tk.Button(
            ctrl, text="■  OPREȘTE", bg="#f44336", fg="white",
            font=("Arial", 11, "bold"), height=2, state="disabled", command=self.stop_sending,
        )
        self.btn_stop.pack(side="left", fill="x", expand=True, padx=4)

        self.btn_help = tk.Button(
            ctrl, text="📖  GHID", font=("Arial", 10), height=2, command=self.show_help,
        )
        self.btn_help.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ---- Progres + status ---------------------------------------------
        prog = tk.Frame(root)
        prog.pack(fill="x", padx=12, pady=(2, 0))
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=1, value=0)
        self.progress.pack(side="left", fill="x", expand=True)
        self.lbl_progress = tk.Label(prog, text="0/0", width=10)
        self.lbl_progress.pack(side="right", padx=(8, 0))

        self.lbl_status = tk.Label(root, text="Status: pregătit", fg="blue", anchor="w")
        self.lbl_status.pack(fill="x", padx=14, pady=(2, 0))

        # ---- Jurnal --------------------------------------------------------
        f5 = tk.LabelFrame(root, text=" Jurnal (ce s-a întâmplat) ", padx=10, pady=6)
        f5.pack(fill="both", expand=True, padx=12, pady=(4, 10))

        log_frame = tk.Frame(f5)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=8, state="disabled", wrap="word",
                           bg="#fafafa", relief="sunken", bd=1)
        log_scroll = tk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        self.log.tag_configure("ok", foreground="#1b7f2f")
        self.log.tag_configure("err", foreground="#c62828")
        self.log.tag_configure("warn", foreground="#e65100")
        self.log.tag_configure("info", foreground="#1565c0")
        self.log.tag_configure("skip", foreground="#757575")

        # widget-uri blocate in timpul rularii
        self.locked_widgets = [
            self.btn_csv, self.btn_reset, self.btn_auto, self.btn_test,
            self.ent_email, self.ent_pass, self.ent_smtp, self.ent_port,
            self.ent_limit, self.ent_delay, self.ent_subject, self.txt_body,
        ]

    # ------------------------------------------------------ setari/salvare --
    def _load_settings(self):
        s = read_json(SETTINGS_FILE)
        self.ent_email.insert(0, s.get("email", ""))
        if s.get("remember", True):
            self.var_remember.set(1)
            self.ent_pass.insert(0, s.get("password", ""))
        else:
            self.var_remember.set(0)
        self.ent_smtp.insert(0, s.get("smtp", "smtp.gmail.com"))
        self.ent_port.insert(0, str(s.get("port", 587)))
        self.ent_limit.insert(0, str(s.get("limit", 50)))
        self.ent_delay.insert(0, str(s.get("delay", 10)))
        self.var_resume.set(1 if s.get("resume", True) else 0)
        self.ent_subject.insert(0, s.get("subject", ""))
        self.txt_body.insert("1.0", s.get("body", ""))
        csv_saved = s.get("csv", "")
        if csv_saved and os.path.isfile(csv_saved):
            self.csv_path = csv_saved

    def _save_settings(self):
        data = {
            "email": self.ent_email.get().strip(),
            "remember": bool(self.var_remember.get()),
            "password": self.ent_pass.get() if self.var_remember.get() else "",
            "smtp": self.ent_smtp.get().strip(),
            "port": self.ent_port.get().strip(),
            "limit": self.ent_limit.get().strip(),
            "delay": self.ent_delay.get().strip(),
            "resume": bool(self.var_resume.get()),
            "subject": self.ent_subject.get(),
            "body": self.txt_body.get("1.0", "end-1c"),
            "csv": self.csv_path,
        }
        write_json(SETTINGS_FILE, data)

    # ------------------------------------------------------- mici ajutoare --
    def _append_log(self, level, text):
        stamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", "[{}] {}\n".format(stamp, text), level)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, text, color="blue"):
        self.lbl_status.configure(text="Status: " + text, fg=color)

    def _set_progress(self, value, text):
        self.progress["value"] = value
        self.lbl_progress["text"] = text

    def _lock_ui(self, locked):
        state = "disabled" if locked else "normal"
        for w in self.locked_widgets:
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        self.btn_start.configure(state="disabled" if locked else "normal")
        self.btn_stop.configure(state="normal" if locked else "disabled")

    # ------------------------------------------------------------- actiuni --
    def load_csv(self):
        path = filedialog.askopenfilename(
            title="Alege fișierul CSV",
            filetypes=[("Fișiere CSV", "*.csv"), ("Toate fișierele", "*.*")],
        )
        if not path:
            return
        try:
            headers, rows = load_csv_rows(path)
        except Exception as e:
            messagebox.showerror("Nu pot citi CSV-ul", str(e), parent=self.root)
            return
        email_col = find_email_column(headers, rows)
        if not email_col:
            messagebox.showerror(
                "Lipsă coloană email",
                "Nu am găsit o coloană cu adrese de email în acest fișier.\n\n"
                "Coloane găsite: " + ", ".join(headers),
                parent=self.root,
            )
            return
        self.csv_path = path
        self.csv_headers, self.csv_rows = headers, rows
        self.lbl_csv.configure(text=os.path.basename(path), fg="black")
        self._refresh_csv_info()
        self._save_settings()
        self._append_log("info", "Încărcat CSV: {} ({} rânduri)".format(
            os.path.basename(path), len(rows)))

    def _refresh_csv_info(self):
        if self.csv_path and not os.path.isfile(self.csv_path):
            self.csv_path = ""  # fisierul a fost sters/mutat intre timp
        if not self.csv_path:
            self.lbl_csv.configure(text="Niciun fișier selectat", fg="gray")
            self.lbl_csv_info.configure(text="")
            return
        self.lbl_csv.configure(text=os.path.basename(self.csv_path), fg="black")
        try:
            headers, rows = load_csv_rows(self.csv_path)
            email_col = find_email_column(headers, rows)
            valid = sum(1 for r in rows if valid_email(str(r.get(email_col or "", ""))))
            key = os.path.basename(self.csv_path).lower()
            sent = len(self.state.get("trimise", {}).get(key, []))
            self.csv_headers, self.csv_rows = headers, rows
            self.lbl_csv_info.configure(
                text="{} rânduri · {} adrese valide · {} deja trimise · "
                     "coloana email: „{}”".format(len(rows), valid, sent, email_col or "?")
            )
        except Exception as e:
            self.lbl_csv_info.configure(text="Nu pot citi fișierul: {}".format(e), fg="#c62828")

    def auto_smtp(self):
        email = self.ent_email.get().strip().lower()
        if "@" not in email:
            messagebox.showwarning("Auto SMTP", "Introdu mai întâi adresa de email.",
                                   parent=self.root)
            return
        domain = email.split("@", 1)[1]
        preset = SMTP_PRESETS.get(domain)
        if preset:
            host, port = preset
            self.ent_smtp.delete(0, "end")
            self.ent_smtp.insert(0, host)
            self.ent_port.delete(0, "end")
            self.ent_port.insert(0, str(port))
            self._append_log("info", "SMTP completat automat pentru {}: {}:{}"
                             .format(domain, host, port))
        else:
            self._append_log("warn", "Nu cunosc domeniul „{}” – completează manual "
                                     "serverul SMTP și portul.".format(domain))

    def reset_progress(self):
        if not self.csv_path:
            messagebox.showwarning("Atenție", "Nu ai selectat niciun fișier CSV.", parent=self.root)
            return
        key = os.path.basename(self.csv_path).lower()
        if not self.state.get("trimise", {}).get(key):
            messagebox.showinfo("Progres",
                                "Nu există emailuri marcate ca trimise pentru acest fișier.",
                                parent=self.root)
            return
        if not messagebox.askyesno(
            "Ștergi progresul?",
            "Toate emailurile din acest fișier vor fi considerate netrimise.\n"
            "La următoarea rulare se va trimite din nou de la început. Continui?",
            parent=self.root,
        ):
            return
        self.state.setdefault("trimise", {}).pop(key, None)
        write_json(STATE_FILE, self.state)
        self._refresh_csv_info()
        self._append_log("warn", "Progres șters pentru {}.".format(os.path.basename(self.csv_path)))

    def show_help(self):
        messagebox.showinfo(
            "Ghid rapid",
            "1. Alege fișierul CSV cu contactele.\n"
            "2. Introdu emailul tău și Parola de Aplicație (App Password).\n"
            "   • Gmail: Setări Google → Securitate → Verificarea în doi pași → "
            "Parole pentru aplicații.\n"
            "3. Apasă „⚡ Auto SMTP” dacă serverul nu e completat.\n"
            "4. Scrie subiectul și mesajul (poți folosi {{Companie}}, {{Persoana_contact}}).\n"
            "5. Alege câte emailuri trimiți acum și pauza (recomandat minim 10 secunde).\n"
            "6. Apasă PORNEȘTE. Poți OPRI oricând și relua mai târziu – "
            "cele deja trimise vor fi sărite automat.\n\n"
            "Toate setările se salvează automat la închidere.",
            parent=self.root,
        )

    def _collect_config(self, need_csv=True, need_message=True):
        """Citeste si valideaza toate campurile (pe thread-ul principal UI)."""
        email = self.ent_email.get().strip()
        if not valid_email(email):
            raise ValueError("Introdu o adresă de email validă (ex: nume@gmail.com).")
        password = self.ent_pass.get().strip()
        if not password:
            raise ValueError("Introdu „Parola de aplicație” (App Password), nu parola normală.")
        smtp = self.ent_smtp.get().strip()
        if not smtp:
            raise ValueError("Serverul SMTP este gol. Apasă butonul „⚡ Auto SMTP”.")
        try:
            port = int(self.ent_port.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("Portul SMTP trebuie să fie un număr între 1 și 65535.")
        try:
            limit = int(self.ent_limit.get().strip())
            if limit < 1:
                raise ValueError
        except ValueError:
            raise ValueError("„Câte emailuri trimiți acum” trebuie să fie un număr mai mare ca 0.")
        try:
            delay = int(self.ent_delay.get().strip())
            if not 0 <= delay <= 3600:
                raise ValueError
        except ValueError:
            raise ValueError("Pauza trebuie să fie un număr de secunde între 0 și 3600.")
        subject = self.ent_subject.get()
        body = self.txt_body.get("1.0", "end-1c")
        if need_message and not subject.strip() and not body.strip():
            raise ValueError("Completează măcar Subiectul sau Corpul mesajului.")
        csv_path = self.csv_path
        if need_csv:
            if not csv_path:
                raise ValueError("Selectează mai întâi fișierul CSV.")
            if not os.path.isfile(csv_path):
                raise ValueError("Fișierul CSV nu mai există:\n{}".format(csv_path))
        return {
            "csv_path": csv_path, "email": email, "password": password,
            "smtp": smtp, "port": port, "limit": limit, "delay": delay,
            "subject": subject, "body": body, "resume": bool(self.var_resume.get()),
        }

    def test_connection(self):
        if self.running:
            return
        try:
            cfg = self._collect_config(need_csv=False, need_message=False)
        except ValueError as e:
            messagebox.showwarning("Atenție", str(e), parent=self.root)
            return
        self.btn_test.configure(state="disabled")
        self._append_log("info", "Testez conexiunea la {}:{}...".format(cfg["smtp"], cfg["port"]))

        def worker():
            session = SmtpSession(cfg["smtp"], cfg["port"], cfg["email"], cfg["password"])
            try:
                session.connect()
                session.close()
                self.q.put(("test", True,
                            "Conexiune reușită la {}:{} – totul funcționează!"
                            .format(cfg["smtp"], cfg["port"])))
            except FatalSmtpError as e:
                self.q.put(("test", False, str(e)))
            except Exception as e:
                self.q.put(("test", False, "Eroare neașteptată: {}".format(e)))

        threading.Thread(target=worker, daemon=True).start()

    def start_sending(self):
        if self.running:
            return
        try:
            cfg = self._collect_config()
        except ValueError as e:
            messagebox.showwarning("Atenție", str(e), parent=self.root)
            return

        self.stop_event.clear()
        self.running = True
        self._lock_ui(True)
        self._set_progress(0, "0/0")
        self._set_status("se pregătește...", "blue")
        self._append_log("info", "―――――――― Rulare nouă ――――――――")
        self._save_settings()
        threading.Thread(target=self.send_process, args=(cfg,), daemon=True).start()

    def stop_sending(self):
        if not self.running:
            return
        self.stop_event.set()
        self._set_status("se oprește după emailul curent...", "orange")
        self._append_log("warn", "Cerere de oprire primită...")

    def test_result(self, ok, message):
        self.btn_test.configure(state="normal")
        if ok:
            self._append_log("ok", message)
            messagebox.showinfo("Test conexiune", message, parent=self.root)
        else:
            self._append_log("err", message.replace("\n", " | "))
            messagebox.showerror("Test eșuat", message, parent=self.root)

    # ------------------------------------------------- bucla de mesaje UI ---
    def _drain_queue(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1], item[2])
                elif kind == "status":
                    self._set_status(item[2], item[1])
                elif kind == "progress":
                    self._set_progress(item[1], item[2])
                elif kind == "progress_max":
                    self.progress["maximum"] = max(1, item[1])
                elif kind == "test":
                    self.test_result(item[1], item[2])
                elif kind == "fatal":
                    self._append_log("err", item[1].replace("\n", " | "))
                    messagebox.showerror("Eroare", item[1], parent=self.root)
                elif kind == "done":
                    self._finish(item[1], item[2])
        except queue.Empty:
            pass
        self.root.after(80, self._drain_queue)

    def _finish(self, reason, stats):
        self.running = False
        self.stop_event.set()  # orice bucla ramasa se opreste
        self._lock_ui(False)
        self._refresh_csv_info()

        trimise, erori, sarite = stats["sent"], stats["errors"], stats["skipped"]
        if reason == "done":
            self._set_status("gata – {} emailuri trimise".format(trimise), "green")
            self._append_log("info", "Rulare încheiată: {} trimise, {} erori, {} sărite."
                             .format(trimise, erori, sarite))
            messagebox.showinfo(
                "Trimitere finalizată",
                "S-au trimis {} emailuri.\nErori: {}\nSărite (deja trimise / invalide): {}"
                .format(trimise, erori, sarite),
                parent=self.root,
            )
        elif reason == "stopped":
            self._set_status("oprit manual – {} emailuri trimise".format(trimise), "orange")
            self._append_log("warn", "Oprire manuală: {} trimise, {} erori."
                             .format(trimise, erori))
            messagebox.showinfo(
                "Oprire",
                "Trimiterea a fost oprită.\n\nAu fost trimise {} emailuri.\n"
                "Poți relua oricând – cele deja trimise vor fi sărite automat."
                .format(trimise),
                parent=self.root,
            )
        else:  # fatal – mesajul deja afisat in _drain_queue
            self._set_status("oprit din cauza unei erori", "red")
            self._append_log("info", "Rulare oprită cu erori: {} trimise, {} erori."
                             .format(trimise, erori))

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno(
                "Trimitere în curs",
                "Trimiterea este în curs și nu s-a terminat.\nVrei să închizi aplicația oricum?",
                parent=self.root,
            ):
                return
            self.stop_event.set()
        self._save_settings()
        self.root.destroy()

    # -------------------------------------------------- memoria de trimitere --
    def _mark_sent(self, key, recipient):
        with self.state_lock:
            lista = self.state.setdefault("trimise", {}).setdefault(key, [])
            if recipient not in lista:
                lista.append(recipient)
            write_json(STATE_FILE, self.state)

    def _wait(self, seconds):
        """Pauza in care butonul OPRESTE ramane eficient (verifica la fiecare secunda)."""
        for remaining in range(seconds, 0, -1):
            if self.stop_event.is_set():
                return False
            self.q.put(("status", "blue", "pauză... încă {}s".format(remaining)))
            time.sleep(1)
        return not self.stop_event.is_set()

    # -------------------------------------------------- thread-ul de lucru ---
    def send_process(self, cfg):
        """Rularea propriu-zisa, pe un thread separat (nu atinge widget-urile UI)."""
        stats = {"sent": 0, "errors": 0, "skipped": 0}
        session = None
        reason = "done"
        try:
            headers, rows = load_csv_rows(cfg["csv_path"])
            email_col = find_email_column(headers, rows)
            if not email_col:
                raise FatalSmtpError(
                    "Nu am găsit o coloană cu adrese de email în CSV.\n"
                    "Coloane găsite: " + ", ".join(headers)
                )

            key = os.path.basename(cfg["csv_path"]).lower()
            norm_headers = {normalize_header(h) for h in headers}
            unknown = sorted(
                v for v in find_unknown_vars(cfg["subject"], cfg["body"])
                if normalize_header(v) not in norm_headers
            )
            if unknown:
                self.q.put(("log", "warn",
                            "Variabile necunoscute (vor apărea goale în mesaj): "
                            + ", ".join("{{" + u + "}}" for u in unknown)))

            with self.state_lock:
                sent_before = (
                    {s.strip().lower() for s in self.state.get("trimise", {}).get(key, [])}
                    if cfg["resume"] else set()
                )

            pending = []
            seen = set()
            for row in rows:
                recipient = str(row.get(email_col, "")).strip()
                if not valid_email(recipient):
                    stats["skipped"] += 1
                    continue
                rk = recipient.lower()
                if rk in seen:
                    stats["skipped"] += 1
                    continue
                seen.add(rk)
                if rk in sent_before:
                    stats["skipped"] += 1
                    continue
                pending.append((recipient, row))

            if cfg["resume"] and stats["skipped"]:
                self.q.put(("log", "info",
                            "{} adrese sărite (deja trimise, duplicate sau invalide)."
                            .format(stats["skipped"])))

            total = min(cfg["limit"], len(pending))
            self.q.put(("progress_max", total))
            self.q.put(("progress", 0, "0/{}".format(total)))

            if not pending:
                self.q.put(("log", "warn",
                            "Nu mai sunt emailuri noi de trimis în acest fișier. "
                            "Folosește „♻️ Șterge progresul” dacă vrei retrimite."))
            else:
                self.q.put(("status", "blue", "conectare la serverul SMTP..."))
                session = SmtpSession(cfg["smtp"], cfg["port"], cfg["email"], cfg["password"])
                session.connect()
                self.q.put(("log", "ok", "Conectat la {}:{} – pornesc trimiterea ({} emailuri)"
                            .format(cfg["smtp"], cfg["port"], total)))

                for i, (recipient, row) in enumerate(pending[:total], start=1):
                    if self.stop_event.is_set():
                        reason = "stopped"
                        break

                    lookup = build_lookup(row)
                    msg = build_message(
                        cfg["email"], recipient,
                        render_template(cfg["subject"], lookup),
                        render_template(cfg["body"], lookup),
                    )
                    self.q.put(("status", "blue", "trimit către {} ({}/{})"
                                .format(recipient, i, total)))

                    ok, err, code = session.send(msg)
                    if not ok and code in TRANSIENT_CODES and not self.stop_event.is_set():
                        self.q.put(("log", "warn",
                                    "Serverul cere o pauză (cod {}), aștept 30s și reîncerc..."
                                    .format(code)))
                        if not self._wait(30):
                            reason = "stopped"
                            break
                        ok, err, code = session.send(msg)

                    if ok:
                        stats["sent"] += 1
                        self._mark_sent(key, recipient)
                        self.q.put(("log", "ok", "Trimis către {}".format(recipient)))
                        self.q.put(("status", "green", "trimis {}/{}".format(i, total)))
                    else:
                        stats["errors"] += 1
                        self.q.put(("log", "err", "Eroare la {}: {}".format(recipient, err)))

                    self.q.put(("progress", i, "{}/{}".format(i, total)))

                    if i < total and not self._wait(cfg["delay"]):
                        reason = "stopped"
                        break

        except FatalSmtpError as e:
            reason = "fatal"
            self.q.put(("fatal", str(e)))
        except Exception as e:
            reason = "fatal"
            self.q.put(("fatal", "Eroare neașteptată: {}".format(e)))
        finally:
            if session is not None:
                session.close()
            self.q.put(("done", reason, stats))


# --------------------------------------------------------------------------
#  Punctul de intrare
# --------------------------------------------------------------------------
def main():
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    EmailSenderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()








