"""
Promemoria ed email.

- Mailer: invio email via SMTP (Gmail con "password per le app", Brevo, Mailgun, Outlook…), solo libreria standard.
- Scheduler: ogni minuto controlla le preferenze di ogni utente e invia i promemoria
  (email e/o notifica sui dispositivi) all'orario scelto, una sola volta al giorno per tipo.
"""
import html
import json
import logging
import os
import re
import smtplib
import ssl
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

log = logging.getLogger("tracker")

# tipi di promemoria e valori predefiniti (giorno: 0 = lunedì … 6 = domenica, None = ogni giorno)
REMINDERS = {
    "morning": {"label": "Riepilogo del mattino", "hint": "Cosa devi fare oggi, scadenze, abitudini",
                "time": "08:00", "day": None, "email": True, "push": False},
    "habits": {"label": "Abitudini non fatte", "hint": "La sera, solo se ne manca qualcuna",
               "time": "21:00", "day": None, "email": False, "push": True},
    "deadlines": {"label": "Scadenze dei progetti", "hint": "Voci che scadono oggi o domani",
                  "time": "09:00", "day": None, "email": True, "push": True},
    "weekly": {"label": "Resoconto settimanale", "hint": "Spese, insight e abitudini della settimana",
               "time": "08:00", "day": 0, "email": True, "push": False},
    "debts": {"label": "Debiti nelle spese condivise", "hint": "Chi ti deve ancora dei soldi",
              "time": "10:00", "day": 5, "email": False, "push": True},
}
DAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def user_reminders(settings):
    """Preferenze dell'utente unite ai valori predefiniti."""
    saved = (settings or {}).get("reminders") or {}
    out = {}
    for k, d in REMINDERS.items():
        r = {"time": d["time"], "day": d["day"], "email": d["email"], "push": d["push"]}
        sv = dict(saved.get(k) or {})
        if "push" not in sv and "telegram" in sv:  # prima c'era Telegram: chi lo usava riceve la notifica dell'app
            sv["push"] = sv["telegram"]
        r.update({x: v for x, v in sv.items() if x in ("time", "day", "email", "push")})
        out[k] = r
    return out


def clean_reminders(data):
    """Valida ciò che arriva dalla web app."""
    out = {}
    for k in REMINDERS:
        r = (data or {}).get(k) or {}
        t = str(r.get("time", REMINDERS[k]["time"]))
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            raise ValueError("Orario non valido: usa il formato HH:MM.")
        day = r.get("day", REMINDERS[k]["day"])
        day = int(day) if day not in (None, "", "null") and str(day).isdigit() and int(day) < 7 else None
        if REMINDERS[k]["day"] is None:
            day = None
        elif day is None:
            day = REMINDERS[k]["day"]
        out[k] = {"time": t, "day": day, "email": bool(r.get("email")), "push": bool(r.get("push"))}
    return out


# ---------------------------------------------------------------- email
class Mailer:
    def __init__(self, ssl_ctx=None):
        self.host = os.getenv("SMTP_HOST", "").strip()
        self.port = int(os.getenv("SMTP_PORT", "587") or 587)
        self.user = os.getenv("SMTP_USER", "").strip()
        self.password = os.getenv("SMTP_PASSWORD", "").replace(" ", "") if "gmail" in os.getenv("SMTP_HOST", "") else os.getenv("SMTP_PASSWORD", "")
        self.sender = os.getenv("SMTP_FROM", "").strip() or self.user
        self.name = os.getenv("SMTP_FROM_NAME", "Tacuìn").strip() or "Tacuìn"
        self.security = (os.getenv("SMTP_SECURITY", "") or ("ssl" if self.port == 465 else "starttls")).strip().lower()
        if self.port == 465:  # la 465 è sempre SSL diretto: con starttls la connessione resterebbe appesa
            self.security = "ssl"
        self.ctx = ssl_ctx or ssl.create_default_context()
        self.last_error = None
        self.last_ok = None   # data e ora dell'ultimo invio riuscito
        self.source = "env"   # «env» = file .env, «ui» = Impostazioni → Server
        self._env = (self.host, self.port, self.user, self.password, self.sender, self.name, self.security)

    def configure(self, c):
        """Dati scelti dall'interfaccia (root); senza, tornano quelli del file .env."""
        if c and c.get("host"):
            self.host, self.port, self.user = c["host"], int(c.get("port") or 587), c.get("user") or ""
            self.password, self.sender = c.get("password") or "", c.get("from") or c.get("user") or ""
            self.name, self.security = c.get("from_name") or "Tacuìn", c.get("security") or "starttls"
            self.source = "ui"
        else:
            self.host, self.port, self.user, self.password, self.sender, self.name, self.security = self._env
            self.source = "env"

    def probe(self, c):
        """Prova collegamento e accesso con i dati c, senza inviare nulla. Solleva un'eccezione se non va."""
        host, port, sec = c["host"], int(c.get("port") or 587), c.get("security") or "starttls"
        if sec == "ssl":
            s = smtplib.SMTP_SSL(host, port, context=self.ctx, timeout=20)
        else:
            s = smtplib.SMTP(host, port, timeout=20)
            s.ehlo()
            if sec == "starttls":
                s.starttls(context=self.ctx)
                s.ehlo()
        try:
            if c.get("user"):
                s.login(c["user"], c.get("password") or "")
        finally:
            try:
                s.quit()
            except Exception:
                pass

    @property
    def configured(self):
        return bool(self.host and self.sender)

    def send(self, to, subject, html_body, text_body=None, attachments=None, on_behalf=None):
        """on_behalf = (nome, email) di un utente (es. inviti): la mail parte dall'indirizzo di sistema come
        «Nome via Tacuìn», con Rispondi-a sull'email dell'utente. Il mittente vero non si può falsificare:
        Gmail/Outlook la scarterebbero e Brevo/SES non la spediscono."""
        if not self.configured:
            raise RuntimeError("Email non configurata (SMTP_HOST e SMTP_FROM nel file .env)")
        msg = EmailMessage()
        msg["Subject"] = subject
        if on_behalf:
            nome, email = on_behalf
            msg["From"] = formataddr((f"{nome} via {self.name}", self.sender))
            if email:
                msg["Reply-To"] = formataddr((nome, email))
        else:
            msg["From"] = formataddr((self.name, self.sender))
        msg["To"] = to
        msg["Message-ID"] = make_msgid(domain=self.sender.split("@")[-1] or "tracker")
        msg.set_content(text_body or html_to_text(html_body))
        msg.add_alternative(html_body, subtype="html")
        for name, data, mime in attachments or []:  # es. ("invito.ics", bytes, "text/calendar")
            main, sub = mime.split("/", 1)
            msg.add_attachment(data, maintype=main, subtype=sub.split(";")[0], filename=name)
        try:
            if self.security == "ssl":
                with smtplib.SMTP_SSL(self.host, self.port, context=self.ctx, timeout=30) as s:
                    self._deliver(s, msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=30) as s:
                    s.ehlo()
                    if self.security == "starttls":
                        s.starttls(context=self.ctx)
                        s.ehlo()
                    self._deliver(s, msg)
            self.last_error = None
            self.last_ok = time.strftime("%Y-%m-%dT%H:%M:%S")
            log.info("Email inviata a %s", _mask(to))
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("Invio email a %s non riuscito: %s", _mask(to), _mask(self.last_error))
            raise

    def _deliver(self, s, msg):
        if self.user:
            s.login(self.user, self.password)
        s.send_message(msg)


def html_to_text(h):
    t = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", h, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _mask(t):
    import re
    return re.sub(r"([\w.+-])[\w.+-]*@([\w-]+\.[\w.-]+)", r"\1***@\2", str(t or ""))


def email_html(title, body_tg_html, button_url=None, button_text="Apri Tacuìn", footer=None):
    """Email semplice e leggibile ovunque; il corpo usa solo <b>, <i> e gli a capo."""
    body = body_tg_html.replace("\n", "<br>")
    btn = (f'<div style="height:1px;background:#eeede9;margin:22px 0 18px"></div><a href="{html.escape(button_url)}" style="background:#2f2f2d;color:#ffffff;'
           f'text-decoration:none;padding:11px 18px;border-radius:10px;font-weight:600;font-size:14.5px;display:inline-block">{html.escape(button_text)}</a>'
           if button_url else "")
    foot = footer or "Ricevi questa email perché l'hai attivata nel Profilo di Tacuìn. Puoi cambiarla o disattivarla quando vuoi."
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;background:#f4f4f2;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#2f2f2d">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">
<div style="font-weight:300;font-size:20px;letter-spacing:.01em;margin:0 0 14px 4px;color:#2a2a27">tacu<span style="color:#c65a12">ì</span>n</div>
<div style="background:#ffffff;border:1px solid #e6e5e1;border-radius:14px;padding:22px 24px;font-size:15px;line-height:1.6">
<div style="font-size:19px;font-weight:700;margin:0 0 12px">{html.escape(title)}</div>
<div>{body}</div>{btn}
</div>
<p style="font-size:12px;color:#75746f;margin:14px 4px">{html.escape(foot)}</p>
</div></body></html>"""


# ---------------------------------------------------------------- scheduler
class Scheduler:
    """
    Ogni minuto: per ogni utente attivo, per ogni promemoria abilitato, se è l'ora (e il giorno) giusti
    e non è già stato inviato oggi, prepara il contenuto e lo invia.
    """

    def __init__(self, db, iam, mailer, build, send_push, push_ready, app_url, on_tick=None):
        self.db, self.iam, self.mailer = db, iam, mailer
        self.build = build                    # build(kind, user) -> dict(subject, body, kb) | None
        self.send_push = send_push            # send_push(user, titolo, testo, url, tag=…) -> dispositivi raggiunti
        self.push_ready = push_ready          # (user) -> bool: ha almeno un dispositivo iscritto
        self.app_url = app_url                # () -> str
        self.on_tick = on_tick                # on_tick(now): lavoro extra ogni minuto (es. avvisi degli appuntamenti)
        db.run("CREATE TABLE IF NOT EXISTS reminder_log (user_id TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL, "
               "PRIMARY KEY (user_id, kind, day))")

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="promemoria").start()

    def _loop(self):
        time.sleep(5)
        while True:
            try:
                self.tick(datetime.now())
            except Exception:
                log.exception("Promemoria")
            time.sleep(60 - datetime.now().second + 0.5)

    def due(self, r, now):
        if not (r["email"] or r["push"]):
            return False
        if r["day"] is not None and now.weekday() != r["day"]:
            return False
        h, m = map(int, r["time"].split(":"))
        sched = now.replace(hour=h, minute=m, second=0, microsecond=0)
        # entro 2 ore dall'orario: se il PC era spento o in stop all'ora esatta, arriva appena riparte
        return sched <= now < sched + timedelta(hours=2)

    def tick(self, now):
        today = now.date().isoformat()
        for u in self.db.q("SELECT * FROM users WHERE status='active'"):
            u = dict(u)
            rem = user_reminders(json.loads(u.get("settings") or "{}"))
            for kind, r in rem.items():
                if not self.due(r, now):
                    continue
                if self.db.one("SELECT 1 FROM reminder_log WHERE user_id=? AND kind=? AND day=?", (u["id"], kind, today)):
                    continue
                self.db.run("INSERT OR IGNORE INTO reminder_log(user_id, kind, day) VALUES(?,?,?)", (u["id"], kind, today))
                self.deliver(kind, u, r)
        if self.on_tick:
            try:
                self.on_tick(now)
            except Exception:
                log.exception("Promemoria extra")

    def deliver(self, kind, u, r=None, force_channels=None):
        """Prepara e invia un promemoria. Ritorna i canali usati (lista) oppure [] se non c'era niente da dire."""
        r = r or user_reminders(json.loads(u.get("settings") or "{}"))[kind]
        content = self.build(kind, u)
        if not content:
            return []
        sent = []
        channels = force_channels or [c for c in ("email", "push") if r.get(c)]
        if "email" in channels and self.mailer.configured:
            try:
                self.mailer.send(u["email"], content["subject"],
                                 email_html(content.get("title") or content["subject"], content["body"], self.app_url()))
                sent.append("email")
            except Exception:
                pass
        if "push" in channels and self.push_ready(u):
            try:
                if self.send_push(u, content["subject"], content.get("push") or content["body"], "/#/home", tag=kind):
                    sent.append("push")
            except Exception:
                log.exception("Promemoria come notifica")
        return sent
