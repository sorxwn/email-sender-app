# -*- coding: utf-8 -*-
"""
Thread-ul de trimitere pentru aplicatia web (Streamlit).

Nu depinde de streamlit -> poate fi testat cu SMTP si Google Sheet falsate.
Face exact ca aplicatia desktop: trimite personalizat, trateaza erorile,
si STERGE randul din Google Sheet imediat dupa fiecare succes.
"""

import threading
import time

from email_core import (
    TRANSIENT_CODES,
    FatalSmtpError,
    SmtpSession,
    build_lookup,
    build_message,
    find_unknown_vars,
    normalize_header,
    render_template,
)

# daca interfata nu se mai uita la rulare atata timp, ne oprim singuri
# (protectie: daca userul inchide fila browserului, nu mai trimite orbeste)
HEARTBEAT_TIMEOUT = 60


class SendRun:
    """O rulare de trimitere: tinte din Sheet + SMTP + stergere dupa succes."""

    def __init__(self, session, sheet, targets, sender_email, subject, body, delay=10):
        self.session = session          # email_core.SmtpSession
        self.sheet = sheet              # sheet_io.SheetModel
        self.targets = targets          # lista rezolvata la pornire
        self.sender_email = sender_email
        self.subject = subject
        self.body = body
        self.delay = max(0, int(delay))

        self.total = len(targets)
        self.done = 0                   # incercari -> progres
        self.sent = 0
        self.errors = 0
        self.deleted = 0                # randuri sterse din tabel
        self.status = "pregătire..."
        self.log = []
        self.running = False
        self.finished = False
        self.finish_reason = None       # "gata" | "oprit" | "eroare"
        self.fatal_message = None

        self.stop_event = threading.Event()
        self._last_heartbeat = time.time()
        self._thread = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- comenzi UI
    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request_stop(self):
        self.stop_event.set()

    def heartbeat(self):
        """Interfata „trage cu ochiul” la rulare -> resusciteaza ceasul."""
        self._last_heartbeat = time.time()

    # ---------------------------------------------------------------- jurnal
    def _log(self, level, text):
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            self.log.append({"level": level, "text": text, "time": stamp})
            if len(self.log) > 500:
                del self.log[:100]

    def log_lines(self):
        with self._lock:
            return list(self.log)

    def _should_stop(self):
        if self.stop_event.is_set():
            return True
        if time.time() - self._last_heartbeat > HEARTBEAT_TIMEOUT:
            self._log("warn", "Interfața s-a închis – opresc automat trimiterea "
                              "pentru siguranță. Poți relua oricând.")
            self.stop_event.set()
            return True
        return False

    def _sleep(self, seconds):
        """Pauza in care OPRESTE si inchiderea paginii raman eficiente."""
        for _ in range(int(seconds)):
            if self._should_stop():
                return False
            time.sleep(1)
        return not self._should_stop()

    # ---------------------------------------------------------------- rulare
    def _run(self):
        try:
            self._log("info", "Pornesc trimiterea către {} contacte (pauză {}s)."
                      .format(self.total, self.delay))
            headers_norm = {normalize_header(h) for h in self.sheet.headers}
            unknown = sorted(
                v for v in find_unknown_vars(self.subject, self.body)
                if normalize_header(v) not in headers_norm)
            if unknown:
                self._log("warn", "Variabile necunoscute (vor apărea goale): "
                          + ", ".join("{{" + u + "}}" for u in unknown))

            for i, target in enumerate(self.targets, start=1):
                if self._should_stop():
                    break
                recipient = target["recipient"]
                self.status = "Trimit către {} ({}/{})".format(recipient, i, self.total)

                lookup = build_lookup(target["data"])
                msg = build_message(
                    self.sender_email, recipient,
                    render_template(self.subject, lookup),
                    render_template(self.body, lookup),
                )
                ok, err, code = self.session.send(msg)
                if not ok and code in TRANSIENT_CODES and not self._should_stop():
                    self._log("warn", "Serverul cere o pauză (cod {}), "
                              "aștept 30s și reîncerc...".format(code))
                    if not self._sleep(30):
                        break
                    ok, err, code = self.session.send(msg)

                if ok:
                    self.sent += 1
                    companie = (target["data"].get("Companie")
                                or target["data"].get("companie") or "?")
                    self._log("ok", "✅ Trimis către {}  (compania: {})".format(recipient, companie))
                    # sterge IMEDIAT randul din Google Sheet
                    # (citim chiar acum numarul real, actualizat de stergerea
                    #  precedenta - nu o copie veche!)
                    if self.sheet.writable:
                        try:
                            self.sheet.delete_row(target["data"]["_row"])
                            self.deleted += 1
                            self._log("info", "🗑 Rândul cu „{}” a fost șters din tabel "
                                              "(rămân mereu doar firmele necontactate).".format(companie))
                        except Exception as e:
                            self._log("err", "⚠️ ATENȚIE: emailul a plecat, dar NU am putut "
                                             "șterge rândul din tabel: {}".format(e))
                            self._log("err", "⚠️ Nu reporni trimiterea pe același tabel fără "
                                             "verificare – s-ar retrimite!")
                    else:
                        self._log("warn", "Mod doar citire: rândul NU a fost șters din tabel "
                                          "(conectează un service account).")
                else:
                    self.errors += 1
                    self._log("err", "❌ Eroare la {}: {}".format(recipient, err))

                self.done += 1
                if i < self.total and not self._should_stop():
                    self.status = "Pauză {}s... ({}/{})".format(self.delay, i, self.total)
                    if not self._sleep(self.delay):
                        break

            self.finish_reason = "oprit" if self.stop_event.is_set() else "gata"
        except FatalSmtpError as e:
            self.finish_reason = "eroare"
            self.fatal_message = str(e)
            self._log("err", "🛑 " + str(e).replace("\n", " "))
        except Exception as e:
            self.finish_reason = "eroare"
            self.fatal_message = "Eroare neașteptată: {}".format(e)
            self._log("err", "🛑 Eroare neașteptată: {}".format(e))
        finally:
            try:
                self.session.close()
            except Exception:
                pass
            self.status = "Încheiat: {} trimise, {} erori, {} rânduri șterse din tabel" \
                .format(self.sent, self.errors, self.deleted)
            self._log("info", "―――― Rulare încheiată ――――")
            self.running = False
            self.finished = True

