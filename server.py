#!/usr/bin/env python3
"""
Tacuìn (nome tecnico: tracker) — web app con assistente, promemoria via email e notifiche push.

Usa solo la libreria standard di Python (3.9 o superiore): non serve installare nulla.
Funziona su Windows, macOS, Linux e in Docker.
  • Serve la web app su http://localhost:8080
  • Salva i dati in data/data.json (con un backup giornaliero in data/backup/)
  • Capisce i messaggi scritti all'assistente e registra spese, entrate, abitudini e altro

Configurazione nel file .env (vedi .env.example) oppure tramite variabili d'ambiente.
"""
import base64
import hmac
import html
import json
import logging
import os
import re
import secrets
import shutil
import socket
import ssl
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from struct import error as struct_error
from zipfile import BadZipFile as zipfile_error
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE = Path(__file__).resolve().parent
WINDOWS = os.name == "nt"

# La console di Windows non usa UTF-8 di default: evita errori con lettere accentate ed emoji
for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_env(path):
    """Legge un file .env semplice (CHIAVE=valore) senza sovrascrivere l'ambiente."""
    if not path.exists():
        return
    vals = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        # la stessa chiave scritta due volte (es. riga vuota del modello + riga aggiunta in fondo): vince l'ultima compilata
        if v or k not in vals:
            vals[k] = v
    for k, v in vals.items():
        os.environ.setdefault(k, v)


def make_ssl_context():
    """
    Connessioni HTTPS in uscita (notifiche push, Garmin, meteo, mappe).
    Usa i certificati di sistema (come Chrome/Edge) tramite truststore, così funziona anche
    quando l'antivirus o la rete aziendale ispezionano HTTPS con un proprio certificato.
    """
    ca = os.getenv("SSL_CA_FILE", "").strip()
    if ca:
        return ssl.create_default_context(cafile=ca), f"file {ca}"
    try:
        sys.path.insert(0, str(BASE / "vendor"))
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), "certificati di sistema"
    except Exception:
        return ssl.create_default_context(), "certificati di Python"


ENV_FILE = BASE / ".env"
ENV_FOUND = ENV_FILE.exists()
load_env(ENV_FILE)
SSL_CTX, SSL_SOURCE = make_ssl_context()

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))
DATA_DIR = Path(os.getenv("DATA_DIR") or BASE / "data")
WEB_DIR = BASE / "web"
# il token incollato a volte si porta dietro codici invisibili del terminale (ESC[201~, \r…): restano solo i caratteri validi
COOKIE = "tk_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "").strip() in ("1", "true", "yes")
TRUST_PROXY = os.getenv("TRUST_PROXY", "").strip() in ("1", "true", "yes")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
# Licenza AGPL-3.0: chi usa Tacuìn via rete deve poter avere il codice. Se modifichi Tacuìn, metti qui il link alla TUA versione.
SOURCE_URL = os.getenv("SOURCE_URL", "").strip() or "https://github.com/Denzel18/tacuin"
VISION_URL = os.getenv("VISION_URL", "").strip().rstrip("/")
VISION_TOKEN = os.getenv("VISION_TOKEN", "").strip()

DATA_DIR.mkdir(parents=True, exist_ok=True)
_fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
_handlers = [RotatingFileHandler(DATA_DIR / "server.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
if sys.stderr is not None:  # con pythonw (avvio in background) non c'è una console
    _handlers.append(logging.StreamHandler(sys.stderr))
for _h in _handlers:
    _h.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=_handlers)
log = logging.getLogger("tracker")

MESI = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto",
        "Settembre", "Ottobre", "Novembre", "Dicembre"]
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
DEFAULT_CATEGORIES = ["Casa", "Spesa alimentare", "Bollette", "Trasporti", "Salute", "Svago",
                      "Abbonamenti", "Stipendio", "Altro"]

# Parole che fanno scegliere la categoria in automatico (solo se la categoria esiste)
KEYWORDS = {
    "Spesa alimentare": ["supermercato", "spesa", "conad", "coop", "esselunga", "lidl", "carrefour", "eurospin",
                         "pam", "despar", "aldi", "pane", "frutta", "verdura", "macelleria", "alimentari"],
    "Trasporti": ["benzina", "carburante", "diesel", "gasolio", "treno", "trenitalia", "italo", "bus", "autobus",
                  "metro", "taxi", "uber", "autostrada", "pedaggio", "parcheggio", "biglietto", "meccanico", "gomme"],
    "Svago": ["pizza", "ristorante", "cinema", "bar", "aperitivo", "birra", "cena", "pranzo", "sushi", "caffe",
              "concerto", "teatro", "gelato", "pub", "vino", "libro", "libri"],
    "Casa": ["affitto", "mutuo", "ikea", "condominio", "arredamento", "pulizie", "ferramenta"],
    "Bollette": ["luce", "gas", "bolletta", "internet", "telefono", "fibra", "enel", "tari", "ricarica"],
    "Salute": ["farmacia", "medico", "dentista", "visita", "analisi", "ottico", "medicine"],
    "Abbonamenti": ["netflix", "spotify", "prime", "disney", "dazn", "palestra", "abbonamento", "icloud",
                    "youtube", "chatgpt", "claude"],
    "Stipendio": ["stipendio", "paga", "salario", "busta"],
}
# Parole di comando da non mettere nella descrizione
FILLER = {"spesa", "speso", "spese", "uscita", "pagato", "entrata", "incasso", "ricevuto", "euro", "eur", "€", "di", "per"}


# ---------------------------------------------------------------- utilità
def uid():
    return uuid.uuid4().hex[:8]


def today():
    return date.today()


def iso(d):
    return d.isoformat()


def norm(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


def num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def eur(x):
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s + " €"


def pct(x):
    return f"{x:+.1f}%".replace(".", ",")


def esc(s):
    return html.escape(str(s), quote=False)


def day_label(d):
    if d == today():
        return "oggi"
    if d == today() - timedelta(days=1):
        return "ieri"
    return f"{d.day} {MESI[d.month - 1].lower()}"


def parse_amount(w):
    w = re.sub(r"(?i)(€|euro|eur)", "", w)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d{1,2})?", w):
        return float(w.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d+([.,]\d{1,2})?", w):
        return float(w.replace(",", "."))
    return None


def take_date(words):
    """Estrae 'ieri', 'altroieri' o una data tipo 28/10 dalle parole. Ritorna (data, parole_rimaste)."""
    d, rest = today(), []
    for w in words:
        n = norm(w)
        if n == "ieri":
            d = today() - timedelta(days=1)
            continue
        if n == "altroieri":
            d = today() - timedelta(days=2)
            continue
        if n == "oggi":
            continue
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", w)
        if m:
            yy = m.group(3)
            y = (int(yy) + (2000 if len(yy) == 2 else 0)) if yy else today().year
            try:
                c = date(y, int(m.group(2)), int(m.group(1)))
                if not yy and c > today():
                    c = c.replace(year=y - 1)
                d = c
                continue
            except ValueError:
                pass
        rest.append(w)
    return d, rest


def streak(h):
    done = h.get("done") or {}
    d = today()
    if not done.get(iso(d)):
        d -= timedelta(days=1)
    s = 0
    while done.get(iso(d)):
        s += 1
        d -= timedelta(days=1)
    return s


TRASH_DAYS = 30


def trash_put(st, kind, data, label, sub="", meta=None):
    """Sposta qualcosa nel cestino (restano 30 giorni). Stessa struttura usata dalla web app."""
    st.setdefault("trash", []).insert(0, {"id": uid(), "kind": kind, "at": int(time.time() * 1000), "label": str(label)[:120],
                                         "sub": str(sub)[:160], "data": data, "meta": meta or {}})


def trash_take(st, data_id):
    """Toglie dal cestino l'elemento con quel contenuto (dopo un «annulla»)."""
    st["trash"] = [x for x in st.get("trash", []) if (x.get("data") or {}).get("id") != data_id]


def migrate(st):
    """Garantisce che tutte le sezioni esistano (stessa logica della web app)."""
    st = st if isinstance(st, dict) else {}
    if not isinstance(st.get("trackers"), list):
        st["trackers"] = []
    if not isinstance(st.get("habits"), dict):
        st["habits"] = {"range": 7, "list": [{"id": uid(), "name": n, "created": iso(today()), "done": {}}
                                             for n in ["Bere 2 litri d'acqua", "Leggere 20 minuti", "Allenamento", "Scrivere nel diario"]]}
    if not isinstance(st.get("expenses"), dict):
        st["expenses"] = {"categories": list(DEFAULT_CATEGORIES), "items": []}
    if not isinstance(st.get("investments"), dict):
        st["investments"] = {"items": []}
    st["habits"].setdefault("list", [])
    st["expenses"].setdefault("items", [])
    st["expenses"].setdefault("categories", list(DEFAULT_CATEGORIES))
    sh = st["expenses"].setdefault("shared", {})
    for k in ("people", "items", "settlements"):
        sh.setdefault(k, [])
    st["investments"].setdefault("items", [])
    for k in ("plans", "log"):
        if not isinstance(st["investments"].get(k), list):
            st["investments"][k] = []
    if not isinstance(st["investments"].get("hist"), dict):
        st["investments"]["hist"] = {}
    if not isinstance(st.get("trips"), list):
        st["trips"] = []
    gym = st.get("gym") if isinstance(st.get("gym"), dict) else {}
    for k in ("sessions", "templates", "custom"):
        if not isinstance(gym.get(k), list):
            gym[k] = []
    gym.setdefault("goal", 3)
    gym.setdefault("rest", 90)
    gym.setdefault("active", None)
    st["gym"] = gym
    cal = st.get("calendar") if isinstance(st.get("calendar"), dict) else {}
    if not isinstance(cal.get("events"), list):
        cal["events"] = []
    st["calendar"] = cal
    if not isinstance(st.get("icons"), dict):
        st["icons"] = {}
    gym.setdefault("courses", []) if isinstance(gym.get("courses"), list) else gym.update(courses=[])
    for k in ("todos", "trash"):
        if not isinstance(st.get(k), list):
            st[k] = []
    old = time.time() * 1000 - TRASH_DAYS * 864e5
    st["trash"] = [x for x in st["trash"] if isinstance(x, dict) and (x.get("at") or 0) >= old]
    if not isinstance(st.get("journal"), dict) or not isinstance(st["journal"].get("entries"), dict):
        st["journal"] = {"entries": {}}
    if not isinstance(st.get("menu"), dict):
        st["menu"] = {"order": [], "hidden": []}
    st.setdefault("view", "home")
    return st


# ---------------------------------------------------------------- archivio dati e utenti
import iam as iam_mod  # noqa: E402
import webpush  # noqa: E402
import legal  # noqa: E402
from iam import DB, IAM, Store, CURRENT_USER, ROLE_LABEL, TotpNeeded, check_password  # noqa: E402
import brain  # noqa: E402
import food as food_mod  # noqa: E402
import invest  # noqa: E402
import quotes as quotes_mod  # noqa: E402
import notify  # noqa: E402
import travel  # noqa: E402
import agenda  # noqa: E402
import fit  # noqa: E402
import garmin  # noqa: E402
import contextvars  # noqa: E402

CHAT_EXTRA = contextvars.ContextVar("chat_extra", default=None)  # dati extra per la chat web (grafici)

db = DB(DATA_DIR / "tracker.db")
iam = IAM(db)
store = Store(db, migrate)
LEGACY_JSON = DATA_DIR / "data.json"


def import_legacy(user_id):
    """Porta i dati della versione a utente singolo (data/data.json) nell'account indicato."""
    if not LEGACY_JSON.exists() or store.snapshot(user_id)[1] is not None:
        return False
    try:
        d = json.loads(LEGACY_JSON.read_text(encoding="utf-8"))
        st = d.get("state") if isinstance(d, dict) else None
        if isinstance(st, dict):
            store.replace(st, 0, user_id)
            LEGACY_JSON.rename(LEGACY_JSON.with_name("data.json.importato"))
            log.info("Dati della versione precedente importati nell'account root")
            return True
    except Exception:
        log.exception("Importazione di data.json non riuscita")
    return False


def bootstrap():
    invites_table()
    send_log_table()
    try:
        apply_mail_conf()
    except Exception:
        log.exception("Impostazioni email dall'interfaccia non applicate: uso il file .env")
    # Telegram non c'è più: niente collegamenti o codici rimasti in giro
    db.run("UPDATE users SET telegram_id=NULL WHERE telegram_id IS NOT NULL")
    db.run("DELETE FROM tokens WHERE kind='telegram'")
    root = db.one("SELECT * FROM users WHERE role='root'")
    if not root:
        email, pw = os.getenv("ROOT_EMAIL", "").strip(), os.getenv("ROOT_PASSWORD", "")
        if email and pw:
            try:
                iam.create_user(email, os.getenv("ROOT_NAME", "Root").strip() or "Root", pw, role="root", status="active",
                                actor="system")
                log.info("Account root creato da .env (%s)", email)
            except ValueError as e:
                log.error("Impossibile creare l'account root da .env: %s", e)
        root = db.one("SELECT * FROM users WHERE role='root'")
    if root:
        import_legacy(root["id"])


def keep_awake():
    """
    Impedisce al PC di andare in standby da solo mentre il tracker è acceso (lo schermo può spegnersi).
    Un PC in standby non può eseguire niente: questo evita che ci vada per inattività.
    KEEP_AWAKE=0 nel file .env per disattivarlo.
    """
    if os.getenv("KEEP_AWAKE", "1").strip().lower() in ("0", "false", "no", "off"):
        return "consentito (KEEP_AWAKE=0)"
    if os.path.exists("/.dockerenv"):
        return "non necessario (Docker)"
    try:
        if WINDOWS:
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            # vale finché questo processo è attivo (va chiamato dal thread principale, che non termina)
            if ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                return "bloccato mentre il tracker è attivo"
            return "impossibile bloccarlo"
        if sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            return "bloccato mentre il tracker è attivo (caffeinate)"
    except Exception as e:
        return f"impossibile bloccarlo ({e})"
    return "gestito dal sistema"


STANDBY = {"status": "—"}


AUDIT_RETENTION_DAYS = max(30, int(os.getenv("AUDIT_RETENTION_DAYS", "365") or 365))  # registro attività: 12 mesi


def purge_old():
    """Scadenze di conservazione: sessioni e codici scaduti, registro attività più vecchio di AUDIT_RETENTION_DAYS."""
    db.run("DELETE FROM sessions WHERE expires < ?", (time.time(),))
    db.run("DELETE FROM tokens WHERE expires < ?", (time.time(),))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=AUDIT_RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = db.one("SELECT COUNT(*) AS n FROM audit WHERE ts < ?", (cutoff,))["n"]
    if n:
        db.run("DELETE FROM audit WHERE ts < ?", (cutoff,))
        log.info("Registro attività: tolte %d voci più vecchie di %d giorni", n, AUDIT_RETENTION_DAYS)
    db.run("DELETE FROM invites WHERE owner_id NOT IN (SELECT id FROM users)")
    limit = (date.today() - timedelta(days=INVITE_RETENTION_DAYS)).isoformat()
    old = [r["token"] for r in db.q("SELECT token, event FROM invites") if str((json.loads(r["event"] or "{}")).get("date") or "9999") < limit]
    for t in old:
        db.run("DELETE FROM invites WHERE token=?", (t,))
    if old:
        log.info("Inviti: tolti %d inviti di appuntamenti di oltre %d giorni fa", len(old), INVITE_RETENTION_DAYS)


def maintenance():
    while True:
        try:
            db.backup_daily(DATA_DIR / "backup")
            purge_old()
        except Exception:
            log.exception("Manutenzione")
        time.sleep(3600)


mailer = notify.Mailer(SSL_CTX)
TRAVEL = travel.Travel(SSL_CTX)

# Tessere della mappa: le chiede il server (con un User-Agent che identifica l'app, come vuole OpenStreetMap)
# e le tiene in cache in data/tiles per 30 giorni. Così il browser non dipende da Referer, localhost o IP.
TILE_URL = os.getenv("TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png").strip()
TILE_UA = f"MyPersonalBrain-Tracker/1.0 (self-hosted personal app; {PUBLIC_URL or 'local install'})"
TILE_DIR = DATA_DIR / "tiles"
TILE_MAX_AGE = 30 * 86400
TILE_LOCK = threading.Lock()
TILE_RE = re.compile(r"^/tiles/(\d{1,2})/(\d{1,7})/(\d{1,7})\.png$")
TILE_MAX_FILES = 30000      # circa 500 MB: oltre, le tessere nuove si servono senza salvarle
_tile_count = {"n": None}


def get_tile(z, x, y):
    """→ bytes del PNG (dalla cache se recente, altrimenti dal server delle tessere)."""
    if not (0 <= z <= 19 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise HTTPError(404, "tessera non valida")
    f = TILE_DIR / str(z) / str(x) / f"{y}.png"
    if f.is_file() and time.time() - f.stat().st_mtime < TILE_MAX_AGE:
        return f.read_bytes()
    req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": TILE_UA, "Accept": "image/png"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
            body = r.read(2_000_000)
            if not r.headers.get("Content-Type", "").startswith("image/"):
                raise ValueError("risposta non immagine")
    except Exception as e:
        if f.is_file():  # meglio una tessera vecchia che nessuna
            return f.read_bytes()
        log.warning("Tessera mappa %s/%s/%s non disponibile: %s", z, x, y, e)
        raise HTTPError(502, "mappa non raggiungibile")
    with TILE_LOCK:
        if _tile_count["n"] is None:
            _tile_count["n"] = sum(1 for _ in TILE_DIR.rglob("*.png")) if TILE_DIR.exists() else 0
        if _tile_count["n"] < TILE_MAX_FILES or f.exists():
            new = not f.exists()
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(f)
            _tile_count["n"] += 1 if new else 0
    return body


def app_url():
    try:
        p = server_conf().get("primary") or ""
    except Exception:
        p = ""
    return f"https://{p}" if p else (PUBLIC_URL or f"http://localhost:{PORT}")


# ---------------------------------------------------------------- impostazioni del server (solo root, da interfaccia)
DOMAIN_RE = re.compile(r"(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
SECRET_FILE = DATA_DIR / "secret.key"


def _secret():
    """Chiave per cifrare i segreti salvati dall'interfaccia (es. password della casella email). Resta in data/, 0600."""
    if not SECRET_FILE.exists():
        SECRET_FILE.write_bytes(secrets.token_bytes(32))
        os.chmod(SECRET_FILE, 0o600)
    return SECRET_FILE.read_bytes()


def seal(text):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    n = secrets.token_bytes(12)
    return base64.b64encode(n + AESGCM(_secret()).encrypt(n, text.encode("utf-8"), b"tacuin")).decode()


def unseal(blob):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(blob or "")
    return AESGCM(_secret()).decrypt(raw[:12], raw[12:], b"tacuin").decode("utf-8")


def server_conf():
    root = db.one("SELECT id FROM users WHERE role='root'")
    return dict(iam.settings(root["id"]).get("_server") or {}) if root else {}


def save_server_conf(conf):
    root = db.one("SELECT id FROM users WHERE role='root'")
    iam.set_settings(root["id"], {"_server": conf})


def mail_conf_plain():
    c = dict(server_conf().get("smtp") or {})
    if c.get("password_enc"):
        try:
            c["password"] = unseal(c["password_enc"])
        except Exception:
            log.warning("Password della casella email non leggibile (chiave cambiata?): reinseriscila in Impostazioni → Server")
            c["password"] = ""
    return c


def apply_mail_conf():
    mailer.configure(mail_conf_plain())


def env_domain():
    return urllib.parse.urlparse(PUBLIC_URL).hostname or ""


def all_domains():
    """Domini serviti: quello del file .env più quelli aggiunti dall'interfaccia."""
    out = [env_domain()] if env_domain() else []
    for d in server_conf().get("domains") or []:
        if d not in out:
            out.append(d)
    return out


def resolve4(name):
    try:
        return {a[4][0] for a in socket.getaddrinfo(name, 443, socket.AF_INET, socket.SOCK_STREAM)}
    except OSError:
        return set()


def server_ips():
    ips = {x.strip() for x in os.getenv("PUBLIC_IP", "").split(",") if x.strip()}
    for d in all_domains():
        ips |= resolve4(d)
    return ips


def email_user(u, subject, body_html, button="Apri Tacuìn", url=None, footer=None):
    """Email a un utente (se l'invio email è configurato). Ritorna True se inviata."""
    if not mailer.configured or not u or not u.get("email"):
        return False
    try:
        mailer.send(u["email"], subject, notify.email_html(subject, body_html, url or app_url(), button, footer))
        return True
    except Exception:
        return False


INVITE_RETENTION_DAYS = max(30, int(os.getenv("INVITE_RETENTION_DAYS", "365") or 365))  # inviti: 12 mesi dopo l'appuntamento


def legal_info():
    """Titolare per l'informativa: PRIVACY_* nel .env, altrimenti quanto scelto dal root in Impostazioni."""
    root = db.one("SELECT * FROM users WHERE role='root'")
    saved = (iam.settings(root["id"]).get("_legal") or {}) if root else {}
    return {k: os.getenv("PRIVACY_" + k.upper(), "").strip() or str(saved.get(k) or "") for k in ("owner", "address", "email")}


def contact_address():
    """Chi riceve «Scrivi all'amministratore»: CONTACT_EMAIL nel .env, poi l'indirizzo scelto dal root, poi l'email del root."""
    e = os.getenv("CONTACT_EMAIL", "").strip()
    if e:
        return e
    root = db.one("SELECT * FROM users WHERE role='root'")
    if not root:
        return ""
    return iam.settings(root["id"]).get("_contact_email") or root["email"]


def notify_admins(subject, text):
    """Avvisa root e admin: email (se configurata) e notifica sui loro dispositivi."""
    log.info("Avviso agli amministratori: %s", subject)
    for a in iam.admins():
        email_user(a, subject, esc(text), "Apri la gestione utenti", app_url() + "/#/utenti",
                   "Ricevi questa email perché sei amministratore di Tacuìn.")
        push_user(a, subject, text, "/#/utenti", tag="admin")


def mask_emails(text):
    """m***@dominio.it: negli errori e nei registri visti da altri non compaiono indirizzi completi."""
    if not text:
        return text
    return re.sub(r"([\w.+-])[\w.+-]*@([\w-]+\.[\w.-]+)", r"\1***@\2", str(text))


def warn_user(user_id, subject, text):
    """Avviso di sicurezza a un utente: email (se configurata) e notifica sui suoi dispositivi."""
    u = iam.user(user_id)
    if not u:
        return
    try:
        email_user(u, subject, f"Ciao {esc(u['name'])},\n{esc(text)}")
    except Exception:
        pass
    push_user(u, subject, text, "/#/profilo", tag="sicurezza")


# ---------------------------------------------------------------- notifiche push (Tacuìn installato sul telefono o nel browser)
def _vapid_subject():
    if PUBLIC_URL.startswith("https://"):
        return PUBLIC_URL
    return "mailto:" + (os.getenv("SMTP_FROM", "").strip() or mailer.sender or "")


PUSH = webpush.WebPush(DATA_DIR / "vapid_private.pem", _vapid_subject(), SSL_CTX,
                       extra_hosts=tuple(h.strip() for h in os.getenv("PUSH_TEST_HOSTS", "").split(",") if h.strip()))
PUSH_MAX_DEVICES = 10


def plain(text_html):
    return html.unescape(re.sub(r"<[^>]+>", "", str(text_html or ""))).strip()


def push_subs(u):
    return [x for x in (iam.settings(u["id"]).get("_push") or []) if isinstance(x, dict) and x.get("endpoint")]


def push_user(u, title, body, url="/", tag=None, urgency="normal"):
    """Notifica su tutti i dispositivi dell'utente. Ritorna su quanti è stata consegnata."""
    if not PUSH.ok or not u:
        return 0
    subs = push_subs(u)
    if not subs:
        return 0
    data = {"title": plain(title)[:120], "body": plain(body)[:300], "url": url if str(url).startswith("/") else "/", "tag": tag}
    ok, gone = 0, []
    for x in subs:
        try:
            PUSH.send(x, data, urgency=urgency, topic=tag)
            ok += 1
        except webpush.Gone:
            gone.append(x["endpoint"])
        except Exception as e:
            log.warning("Notifica non consegnata a %s: %s", urllib.parse.urlparse(x["endpoint"]).hostname, e)
    if gone:
        iam.set_settings(u["id"], {"_push": [x for x in subs if x["endpoint"] not in gone]})
        log.info("Tolti %d dispositivi che non ricevono più le notifiche", len(gone))
    return ok


# ---------------------------------------------------------------- limiti di frequenza
class RateLimit:
    """Al massimo `limit` eventi per chiave in `window` secondi (in memoria: si azzera al riavvio, va bene così)."""

    def __init__(self, limit, window):
        self.limit, self.window, self.hits, self.lock = limit, window, {}, threading.Lock()

    def hit(self, key):
        """Registra un tentativo; False se la chiave ha già superato il limite."""
        now = time.time()
        with self.lock:
            q = [t for t in self.hits.get(key, []) if t > now - self.window]
            if len(q) >= self.limit:
                self.hits[key] = q
                return False
            q.append(now)
            self.hits[key] = q
            if len(self.hits) > 5000:  # pulizia delle chiavi vecchie
                for k in [k for k, v in self.hits.items() if not v or v[-1] < now - self.window]:
                    self.hits.pop(k, None)
            return True


RL_REGISTER = RateLimit(5, 3600)        # richieste di accesso per IP all'ora
SOURCES = {"instagram": "Instagram", "amici": "Amici o passaparola", "ricerca": "Ricerca su internet", "altro": "Altro"}  # «Come ci hai conosciuto?»
RL_SERVERCONF = RateLimit(20, 600)      # modifiche e prove delle impostazioni del server
RL_RECEIPT = RateLimit(30, 3600)        # foto di scontrini da leggere per utente all'ora
RL_SEARCH = RateLimit(40, 600)          # ricerche di utenti per nome utente, per account e per rete, ogni 10 minuti
RL_HANDLE = RateLimit(30, 600)          # verifiche «nome utente libero?» dalla pagina di accesso, per rete ogni 10 minuti
RL_SHARE = RateLimit(20, 3600)          # avvisi di spesa condivisa inviati da un account all'ora
RL_SHARE_PAIR = RateLimit(6, 86400)     # avvisi allo stesso destinatario dallo stesso mittente al giorno
RL_CONTACT = RateLimit(5, 3600)         # messaggi «Scrivi all'amministratore» per IP e per email all'ora
RL_CONTACT_ALL = RateLimit(60, 3600)    # in tutto, all'ora (contro chi cambia IP)
RL_RESET = RateLimit(20, 3600)          # tentativi di link di reset per IP all'ora
RL_PUSH = RateLimit(30, 3600)           # iscrizioni alle notifiche per utente all'ora
RL_TILES = RateLimit(3000, 600)         # tessere della mappa per utente ogni 10 minuti (navigare normalmente ne usa poche centinaia)
RL_FOOD = RateLimit(60, 600)           # ricerche di prodotti per utente ogni 10 minuti
RL_QUOTES = RateLimit(120, 600)        # richieste di prezzi di borsa per utente ogni 10 minuti
RL_TOTP = RateLimit(6, 900)             # codici di verifica sbagliati per utente ogni 15 minuti
ANON_BODY_MAX = 64 * 1024               # senza accesso il corpo di una richiesta non può superare 64 KB

# intestazioni di sicurezza su ogni risposta della web app
SECURITY_HEADERS = {
    # gli script in pagina partono solo con il «nonce» di quella risposta: un testo iniettato non può eseguire codice
    "Content-Security-Policy": "default-src 'self'; script-src 'self'{nonce}; script-src-attr 'none'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; object-src 'none'; "
                               "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "Permissions-Policy": "geolocation=(), microphone=(), payment=(), usb=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


# ---------------------------------------------------------------- web server
class HTTPError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def user_payload(u):
    p = iam.public(u)
    p["totp"] = iam.totp_on(u["id"])
    p["role_label"] = ROLE_LABEL.get(u["role"], u["role"])
    return p


class Handler(BaseHTTPRequestHandler):
    server_version = "Tracker"
    sys_version = ""   # non dice la versione di Python
    timeout = 60       # una connessione ferma viene chiusa dopo 60 secondi

    def log_message(self, *args):
        pass

    # --- utilità
    def ip(self):
        if TRUST_PROXY and self.headers.get("X-Forwarded-For"):
            # l'ultimo valore è quello aggiunto dal nostro proxy (Caddy o Cloudflare): i precedenti li può scrivere chiunque
            return self.headers["X-Forwarded-For"].split(",")[-1].strip()
        return self.client_address[0]

    def is_local(self):
        """Richiesta fatta dal PC dove gira il server, senza proxy davanti (nessuna intestazione da proxy)."""
        if TRUST_PROXY or any(self.headers.get(h) for h in ("X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP", "Forwarded",
                                                           "Cf-Connecting-Ip", "Via")):
            return False
        return self.client_address[0] in ("127.0.0.1", "::1")

    def base_url(self):
        """Indirizzo del sito per i link nelle email: PUBLIC_URL; senza, l'Host della richiesta solo se è un nome valido."""
        if PUBLIC_URL or server_conf().get("primary"):
            return app_url()
        host = self.headers.get("Host") or ""
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}(:\d{1,5})?|\[[0-9a-fA-F:]+\](:\d{1,5})?", host):
            return app_url()
        return f"{'https' if self.secure() else 'http'}://{host}"

    def secure(self):
        return COOKIE_SECURE or self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def cookie_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def set_cookie(self, token, max_age):
        c = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax"
        if max_age is not None:
            c += f"; Max-Age={int(max_age)}"
        if self.secure():
            c += "; Secure"
        self._cookie = c

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")  # OpenStreetMap blocca le tessere senza Referer
        nonce = getattr(self, "_nonce", None)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v.replace("{nonce}", f" 'nonce-{nonce}'" if nonce else ""))
        if getattr(self, "_cookie", None):
            self.send_header("Set-Cookie", self._cookie)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)

    def body(self):
        return self._body

    def _read_body(self):
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("application/json"):
            raise HTTPError(415, "richiesta non valida")
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            raise HTTPError(403, "origine non consentita")
        n = int(self.headers.get("Content-Length", "0"))
        if n < 0 or n > 8 * 1024 * 1024:  # i dati di una persona stanno in poche centinaia di KB
            raise HTTPError(413, "dati troppo grandi")
        # chi non ha fatto l'accesso può mandare solo richieste piccole (login, registrazione...): niente server intasato
        if n > ANON_BODY_MAX and not iam.session_user(self.cookie_token()):
            raise HTTPError(413, "dati troppo grandi")
        try:
            b = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            raise HTTPError(400, "richiesta non valida")
        if not isinstance(b, dict):
            raise HTTPError(400, "richiesta non valida")
        return b

    def current(self, admin=False):
        u = iam.session_user(self.cookie_token())
        if not u:
            raise HTTPError(401, "accesso richiesto")
        if admin and u["role"] not in ("root", "admin"):
            raise HTTPError(403, "non hai i permessi")
        CURRENT_USER.set(u["id"])
        return u

    def handle_one_request(self):
        CURRENT_USER.set(None)
        self._cookie = None
        self._nonce = None
        super().handle_one_request()

    STATIC_TYPES = {".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png",
                    ".svg": "image/svg+xml", ".woff2": "font/woff2"}

    def static(self, path):
        base = (WEB_DIR / "vendor").resolve()
        f = (WEB_DIR / path.lstrip("/")).resolve()
        if base not in f.parents or not f.is_file() or f.suffix not in self.STATIC_TYPES:
            raise HTTPError(404, "non trovato")
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self.STATIC_TYPES[f.suffix])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def route(self, method):
        try:
            url = urllib.parse.urlparse(self.path)
            if method == "GET" and url.path.startswith("/vendor/"):
                return self.static(url.path)
            if method == "GET" and url.path.startswith("/tiles/"):
                m = TILE_RE.match(url.path)
                if not m:
                    raise HTTPError(404, "non trovato")
                u = self.current()  # solo per chi ha fatto l'accesso: niente proxy aperto a tutti
                if not RL_TILES.hit(u["id"]):
                    raise HTTPError(429, "troppe tessere della mappa: riprova tra qualche minuto")
                body = get_tile(*map(int, m.groups()))
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "private, max-age=604800")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
                return
            fn = getattr(self, f"{method}_{url.path.strip('/').replace('/', '_').replace('-', '_').replace('.', '_') or 'index'}", None)
            if not fn:
                raise HTTPError(404, "non trovato")
            if method == "POST" and url.path in ("/api/receipt", "/api/garmin/fit"):  # file: corpo binario + intestazione dedicata
                if self.headers.get("X-Tracker") != "1":
                    raise HTTPError(403, "richiesta non valida")
                origin = self.headers.get("Origin")
                if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
                    raise HTTPError(403, "origine non consentita")
            elif method == "POST":  # ogni POST: solo JSON e dalla stessa origine (protezione CSRF)
                self._body = self._read_body()
            fn(url)
        except HTTPError as e:
            self._json(e.code, {"error": e.msg})
        except PermissionError as e:
            self._json(403, {"error": str(e)})
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception:
            log.exception("Errore nella richiesta %s %s", method, self.path.split("?", 1)[0])
            self._json(500, {"error": "errore interno"})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    # --- pagine e stato generale
    def GET_index(self, url):
        self._nonce = secrets.token_urlsafe(18)
        page = (WEB_DIR / "index.html").read_bytes().replace(b"<script>", f'<script nonce="{self._nonce}">'.encode())
        self._send(200, page, "text/html; charset=utf-8")

    GET_index_html = GET_index

    # --- Impostazioni → Server: email e dominio (solo root, con la password)
    def _root_with_password(self):
        u = self.current(admin=True)
        if u["role"] != "root":
            raise HTTPError(403, "Solo il root può cambiare le impostazioni del server.")
        b = self.body()
        if not RL_SERVERCONF.hit(u["id"]):
            raise HTTPError(429, "Troppi tentativi: riprova tra qualche minuto.")
        if not iam_mod.check_password(str(b.get("password") or ""), u["pw_hash"]):
            raise HTTPError(400, "Password di Tacuìn non corretta.")
        return u, b

    def GET_api_admin_server(self, url):
        u = self.current(admin=True)
        if u["role"] != "root":
            raise HTTPError(403, "Solo il root.")
        c = server_conf()
        sm = dict(c.get("smtp") or {})
        smtp = {k: sm.get(k, "") for k in ("host", "port", "security", "user", "from", "from_name")}
        if not sm.get("host"):  # finché non salvi dall'interfaccia valgono quelli del file .env
            smtp = {"host": mailer._env[0], "port": mailer._env[1], "user": mailer._env[2], "from": mailer._env[4],
                    "from_name": mailer._env[5], "security": mailer._env[6]}
        smtp["has_password"] = bool(sm.get("password_enc") if sm.get("host") else mailer._env[3])
        prim = c.get("primary") or env_domain()
        self._json(200, {"smtp": smtp, "source": mailer.source, "configured": mailer.configured,
                         "last_ok": mailer.last_ok, "error": mask_emails(mailer.last_error),
                         "domains": [{"name": d, "primary": d == prim, "env": d == env_domain()} for d in all_domains()],
                         "ips": sorted(server_ips()), "quotes": quotes_on(), "quotes_env_off": QUOTES_ENV_OFF, "food": food_on()})

    def POST_api_admin_server_food(self, url):
        """Ricerca dei prodotti confezionati su Open Food Facts: accesa o spenta per tutto il server."""
        u = self.current(admin=True)
        if u["role"] != "root":
            raise HTTPError(403, "Solo il root.")
        if not RL_SERVERCONF.hit(u["id"]):
            raise HTTPError(429, "Troppe modifiche: riprova tra qualche minuto.")
        on = bool(self.body().get("on"))
        conf = server_conf(); conf["food"] = on; save_server_conf(conf)
        iam.audit(u["id"], "admin.server.food", u["id"], "acceso" if on else "spento")
        self._json(200, {"ok": True, "food": food_on()})

    def POST_api_admin_server_quotes(self, url):
        """Prezzi di borsa automatici (Yahoo Finance / OpenFIGI): acceso o spento per tutto il server."""
        u = self.current(admin=True)
        if u["role"] != "root":
            raise HTTPError(403, "Solo il root.")
        if not RL_SERVERCONF.hit(u["id"]):
            raise HTTPError(429, "Troppe modifiche: riprova tra qualche minuto.")
        on = bool(self.body().get("on"))
        conf = server_conf(); conf["quotes"] = on; save_server_conf(conf)
        iam.audit(u["id"], "admin.server.quotes", u["id"], "acceso" if on else "spento")
        self._json(200, {"ok": True, "quotes": quotes_on()})

    def POST_api_admin_server_email(self, url):
        u, b = self._root_with_password()
        old = server_conf().get("smtp") or {}
        if b.get("reset"):  # torna ai valori del file .env
            conf = server_conf(); conf.pop("smtp", None); save_server_conf(conf); apply_mail_conf()
            iam.audit(u["id"], "admin.server.email", u["id"], "valori del file .env")
            return self._json(200, {"ok": True, "message": "Tornato ai valori del file .env."})
        host = str(b.get("host") or "").strip().lower()
        ip_lit = re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host) and not host.startswith("169.254.")
        if not (DOMAIN_RE.fullmatch(host) or ip_lit):
            raise HTTPError(400, "Server SMTP non valido (es. smtps.aruba.it).")
        sec = b.get("security") if b.get("security") in ("ssl", "starttls", "none") else "starttls"
        port = int(num(b.get("port")) or (465 if sec == "ssl" else 587))
        if not 1 <= port <= 65535:
            raise HTTPError(400, "Porta non valida.")
        user = str(b.get("user") or "").strip()[:120]
        sender = str(b.get("from") or user).strip()[:120]
        if not iam_mod.valid_email(sender):
            raise HTTPError(400, "Indirizzo del mittente non valido.")
        name = re.sub(r"[\r\n<>\"]", "", str(b.get("from_name") or "Tacuìn")).strip()[:60] or "Tacuìn"
        pw = str(b.get("smtp_password") or "")
        if not pw:  # vuoto = non cambiare
            pw = (mail_conf_plain().get("password") if old.get("host") else mailer._env[3]) or ""
        conf_try = {"host": host, "port": port, "security": sec, "user": user, "password": pw, "from": sender, "from_name": name}
        try:
            mailer.probe(conf_try)
        except Exception as e:
            raise HTTPError(400, f"Non riesco a collegarmi: {type(e).__name__}. Controlla server, porta, protezione, utente e password. Non ho cambiato niente.")
        try:
            enc = seal(pw) if pw else ""
        except ImportError:
            raise HTTPError(500, "Manca la libreria «cryptography» per salvare la password in modo sicuro.")
        conf = server_conf()
        conf["smtp"] = {k: v for k, v in conf_try.items() if k != "password"} | {"password_enc": enc}
        save_server_conf(conf)
        apply_mail_conf()
        iam.audit(u["id"], "admin.server.email", u["id"], f"{host}:{port} {sec}")
        warn_user(u["id"], "Impostazioni email del server cambiate", f"Le email di Tacuìn ora partono da {host} con mittente {sender}. "
                  "Se non sei stato tu, cambia subito la password.")
        self._json(200, {"ok": True, "message": "Salvato: collegamento al server di posta riuscito."})

    def POST_api_admin_server_email_test(self, url):
        u = self.current(admin=True)
        if u["role"] != "root":
            raise HTTPError(403, "Solo il root.")
        if not RL_SERVERCONF.hit(u["id"]):
            raise HTTPError(429, "Troppi tentativi: riprova tra qualche minuto.")
        if not mailer.configured:
            raise HTTPError(400, "Email non configurata.")
        try:
            mailer.send(u["email"], "Prova di invio da Tacuìn", notify.email_html(
                "Prova di invio", "Se leggi questo messaggio, le email di Tacuìn funzionano.", app_url()))
        except Exception:
            raise HTTPError(502, f"Invio non riuscito: {mask_emails(mailer.last_error)}")
        self._json(200, {"ok": True, "message": f"Inviata a {u['email']}: controlla la casella (anche lo spam)."})

    def POST_api_admin_server_domain(self, url):
        u, b = self._root_with_password()
        act = b.get("action")
        d = str(b.get("domain") or "").strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0].rstrip(".")
        if not DOMAIN_RE.fullmatch(d):
            raise HTTPError(400, "Dominio non valido (es. app.tuodominio.it).")
        conf = server_conf()
        doms = [x for x in conf.get("domains") or [] if x != env_domain()]
        if act == "add":
            if d in all_domains():
                raise HTTPError(400, "Questo indirizzo è già attivo.")
            mine, theirs = server_ips(), resolve4(d)
            if not theirs:
                raise HTTPError(400, f"{d} non ha ancora un record A: crealo nel DNS e riprova tra qualche minuto.")
            if mine and not (theirs & mine):
                raise HTTPError(400, f"Non ancora: {d} punta a {', '.join(sorted(theirs))}, questo server è {', '.join(sorted(mine))}. "
                                     "Riprova tra qualche minuto.")
            if not mine:
                raise HTTPError(400, "Non riesco a capire l'indirizzo IP di questo server: imposta PUBLIC_IP nel file .env.")
            if len(doms) >= 10:
                raise HTTPError(400, "Al massimo 10 indirizzi.")
            conf["domains"] = doms + [d]
            msg = f"Fatto: {d} punta qui ed è attivo. Il certificato HTTPS arriva al primo accesso."
        elif act == "primary":
            if d not in all_domains():
                raise HTTPError(404, "Indirizzo non attivo.")
            conf["primary"] = "" if d == env_domain() else d
            msg = f"Ora i link nelle email usano {d}."
        elif act == "remove":
            if d == env_domain():
                raise HTTPError(400, "Questo indirizzo è nel file .env: si cambia da lì.")
            if d == (conf.get("primary") or ""):
                raise HTTPError(400, "Prima scegli un altro indirizzo principale.")
            conf["domains"] = [x for x in doms if x != d]
            msg = f"{d} non è più servito."
        else:
            raise HTTPError(400, "Azione non valida.")
        save_server_conf(conf)
        iam.audit(u["id"], "admin.server.domain", u["id"], f"{act} {d}")
        if act != "primary":
            warn_user(u["id"], "Indirizzi del server cambiati", f"{msg} Se non sei stato tu, cambia subito la password.")
        self._json(200, {"ok": True, "message": msg})

    def GET_api_caddy_ask(self, url):
        """Caddy chiede se può creare il certificato per un dominio (HTTPS on demand). Risponde solo sulla rete interna."""
        if self.headers.get("X-Forwarded-For"):
            raise HTTPError(404, "non trovato")
        d = ((urllib.parse.parse_qs(url.query).get("domain") or [""])[0]).strip().lower()
        if d and d in all_domains():
            return self._json(200, {"ok": True})
        raise HTTPError(404, "dominio non autorizzato")

    def _legal_page(self, kind):
        info = legal_info()
        body = legal.page(kind, info["owner"], info["address"], info["email"], app_url(), INVITE_RETENTION_DAYS)
        self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def GET_privacy(self, url):  # pubblica: si legge anche senza account
        self._legal_page("privacy")

    def GET_condizioni(self, url):
        self._legal_page("terms")

    def GET_api_admin_legal(self, url):
        u = self.current(admin=True)
        if u["role"] != "root":
            raise HTTPError(403, "Solo il root può cambiarlo.")
        saved = iam.settings(u["id"]).get("_legal") or {}
        self._json(200, {"saved": {k: saved.get(k) or "" for k in ("owner", "address", "email")},
                         "env": {k: bool(os.getenv("PRIVACY_" + k.upper(), "").strip()) for k in ("owner", "address", "email")}})

    def POST_api_admin_legal(self, url):
        u = self.current(admin=True)
        if u["role"] != "root":
            raise HTTPError(403, "Solo il root può cambiarlo.")
        b = self.body()
        v = {k: re.sub(r"\s+", " ", str(b.get(k) or "")).strip()[:160] for k in ("owner", "address", "email")}
        if v["email"] and not iam_mod.valid_email(v["email"]):
            raise HTTPError(400, "Email per la privacy non valida.")
        iam.set_settings(u["id"], {"_legal": v})
        iam.audit(u["id"], "settings.legal", u["id"], v["owner"])
        self._json(200, {"ok": True})

    def POST_api_me_consent(self, url):
        """Presa visione dell'informativa e accettazione delle condizioni (anche dopo un aggiornamento dei testi)."""
        u = self.current()
        b = self.body()
        if not (b.get("privacy") and b.get("terms") and b.get("clauses")):
            raise HTTPError(400, "Per usare Tacuìn servono tutte e tre le conferme.")
        consent = {"version": legal.VERSION, "at": datetime.now().isoformat(timespec="seconds")}
        iam.set_settings(u["id"], {"consent": consent})
        iam.audit(u["id"], "consent.accept", u["id"], legal.VERSION, self.ip())
        self._json(200, {"ok": True, "consent": consent})

    def POST_api_me_health_consent(self, url):
        """Consenso esplicito ai dati sulla salute (Garmin, file .FIT): art. 9 GDPR."""
        u = self.current()
        if not self.body().get("accept"):
            raise HTTPError(400, "Serve il consenso per importare dati sulla salute.")
        hc = {"version": legal.VERSION, "at": datetime.now().isoformat(timespec="seconds")}
        iam.set_settings(u["id"], {"health_consent": hc})
        iam.audit(u["id"], "consent.health", u["id"], legal.VERSION, self.ip())
        self._json(200, {"ok": True, "health_consent": hc})

    def GET_foods_js(self, url):  # tabella degli alimenti generici (si carica solo aprendo Alimentazione)
        self._send(200, (WEB_DIR / "foods.js").read_bytes(), "application/javascript; charset=utf-8", {"Cache-Control": "public, max-age=86400"})

    def GET_sw_js(self, url):  # service worker delle notifiche: deve stare nella radice del sito
        self._send(200, (WEB_DIR / "sw.js").read_bytes(), "application/javascript; charset=utf-8", {"Cache-Control": "no-cache"})

    def GET_manifest_webmanifest(self, url):  # rende Tacuìn installabile come app
        self._send(200, (WEB_DIR / "manifest.webmanifest").read_bytes(), "application/manifest+json; charset=utf-8",
                   {"Cache-Control": "public, max-age=3600"})

    def GET_api_health(self, url):
        u = iam.session_user(self.cookie_token())
        if u and u["role"] in ("root", "admin") or self.is_local():  # i dettagli solo agli admin (e al controllo di Docker)
            return self._json(200, {"ok": True, "push": PUSH.ok, "email": mailer.configured, "standby": STANDBY["status"]})
        self._json(200, {"ok": True})

    def GET_api_auth(self, url):
        u = iam.session_user(self.cookie_token())
        setup = iam.count() == 0
        self._json(200, {"setup": setup, "setup_here": setup and self.is_local(),
                         "user": user_payload(u) if u else None, "features": {"vision": bool(u) and vision_ok(), "quotes": bool(u) and quotes_on(), "food": bool(u) and food_on()},
                         "legal": legal.VERSION, "source": SOURCE_URL})

    # --- accesso
    def POST_api_setup(self, url):
        b = self.body()
        if iam.count() != 0:
            raise HTTPError(409, "La configurazione iniziale è già stata fatta.")
        if not self.is_local():
            raise HTTPError(403, "Per sicurezza l'account root si crea solo dal PC dove gira il server "
                                 "(http://localhost), oppure con ROOT_EMAIL e ROOT_PASSWORD nel file .env.")
        u = iam.create_user(b.get("email", ""), b.get("name", ""), b.get("password", ""),
                            role="root", status="active", actor="system", ip=self.ip())
        import_legacy(u["id"])
        token, u, life = iam.login(b["email"], b["password"], self.ip(), self.headers.get("User-Agent"))
        self.set_cookie(token, life)
        self._json(200, {"user": user_payload(u)})

    def POST_api_login(self, url):
        b = self.body()
        try:
            token, u, life = iam.login(b.get("email", ""), b.get("password", ""), self.ip(),
                                       self.headers.get("User-Agent"), bool(b.get("remember", True)), b.get("totp"))
        except PermissionError as e:
            raise HTTPError(429, str(e))
        except TotpNeeded as e:
            return self._json(200, {"need_totp": True, "message": str(e)})
        self.set_cookie(token, life if b.get("remember", True) else None)
        self._json(200, {"user": user_payload(u)})

    def POST_api_logout(self, url):
        iam.logout(self.cookie_token())
        self.set_cookie("", 0)
        self._json(200, {"ok": True})

    def POST_api_register(self, url):
        b = self.body()
        if iam.count() == 0:
            raise HTTPError(409, "Prima va creato l'account root.")
        if not RL_REGISTER.hit(self.ip()):
            raise HTTPError(429, "Troppe richieste da questa rete: riprova tra un'ora.")
        if not (b.get("accept_privacy") and b.get("accept_terms") and b.get("accept_clauses")):
            raise HTTPError(400, "Per chiedere l'accesso servono le tre conferme su informativa, condizioni ed età.")
        done = {"ok": True, "message": "Richiesta inviata! Riceverai un'email quando l'amministratore avrà approvato il tuo account."}
        ex = iam.by_email(str(b.get("email") or ""))
        if ex:  # stessa risposta di una richiesta nuova: da qui non si scopre chi è iscritto
            try:
                email_user(ex, "Richiesta di accesso con la tua email", "Qualcuno ha chiesto un nuovo account Tacuìn usando il tuo indirizzo. "
                           "Hai già un account: se hai dimenticato la password chiedi all'amministratore un link per reimpostarla. "
                           "Se non sei stato tu, puoi ignorare questo messaggio.")
            except Exception:
                pass
            return self._json(200, done)
        try:
            u = iam.create_user(b.get("email", ""), b.get("name", ""), b.get("password", ""), ip=self.ip(), handle=b.get("handle") or "")
        except ValueError as e:
            raise HTTPError(400, str(e))
        iam.set_settings(u["id"], {"consent": {"version": legal.VERSION, "at": datetime.now().isoformat(timespec="seconds")},
                                   "_source": b.get("source") if b.get("source") in SOURCES else ""})
        iam.audit(u["id"], "consent.accept", u["id"], legal.VERSION, self.ip())
        if mailer.configured:  # l'indirizzo va confermato: finché non lo è, gli inviti per quell'email non arrivano a questo account
            iam.set_settings(u["id"], {"_email_unverified": True})
            t = iam.make_token("verify", u["id"], 7 * 24 * 60)
            try:
                mailer.send(u["email"], "Conferma la tua email", notify.email_html(
                    "Conferma la tua email", f"Ciao {esc(u['name'])},\nhai chiesto di usare Tacuìn. Apri il link per confermare che questo indirizzo è tuo.",
                    f"{app_url()}/api/email/verify?t={t}", "Conferma l'email", "Se non sei stato tu, ignora questo messaggio."))
            except Exception:
                log.warning("Email di conferma non inviata")
        notify_admins("Nuova richiesta di accesso", f"{u['name']} ({u['email']}) ha chiesto di usare Tacuìn.")
        self._json(200, {"ok": True, "message": "Richiesta inviata! Riceverai un'email quando l'amministratore "
                                                 "avrà approvato il tuo account."})

    def POST_api_contact(self, url):
        """«Scrivi all'amministratore» dalla pagina di accesso: il messaggio parte dal server, l'indirizzo resta nascosto."""
        b = self.body()
        if str(b.get("website") or "").strip():  # campo trappola: lo compilano solo i bot
            self._json(200, {"ok": True, "message": "Messaggio inviato."})
            return
        name = re.sub(r"\s+", " ", str(b.get("name") or "")).strip()[:80]
        email = str(b.get("email") or "").strip()[:120]
        text = str(b.get("message") or "").strip()[:3000]
        if not name:
            raise HTTPError(400, "Scrivi il tuo nome.")
        if not iam_mod.valid_email(email):
            raise HTTPError(400, "Email non valida.")
        if len(text) < 5:
            raise HTTPError(400, "Il messaggio è troppo corto.")
        if not mailer.configured:
            raise HTTPError(503, "Al momento non si possono inviare messaggi: l'invio di email non è configurato sul server.")
        to = contact_address()
        if not to:
            raise HTTPError(503, "Al momento non si possono inviare messaggi.")
        if not (RL_CONTACT.hit("ip:" + self.ip()) and RL_CONTACT.hit("mail:" + email.lower()) and RL_CONTACT_ALL.hit("all")):
            raise HTTPError(429, "Hai già scritto più volte: riprova tra un'ora.")
        subj = f"Messaggio da {name} (pagina di accesso)"
        body = f"<b>{esc(name)}</b> ({esc(email)}) ti ha scritto dalla pagina di accesso di Tacuìn:\n\n{esc(text)}"
        try:
            mailer.send(to, subj, notify.email_html(subj, body, None, footer=f"Per rispondere basta rispondere a questa email: arriva a {email}."),
                        on_behalf=(name, email))
        except Exception as e:
            log.warning("Messaggio dalla pagina di accesso non inviato: %s", e)
            raise HTTPError(502, "Invio non riuscito: riprova più tardi.")
        log.info("Messaggio dalla pagina di accesso da %s", mask_emails(email))
        self._json(200, {"ok": True, "message": f"L'amministratore lo riceve via email e ti risponderà a {email}."})

    def GET_api_admin_contact(self, url):
        u = self.current(admin=True)
        if u["role"] != "root":
            raise HTTPError(403, "Solo il root può cambiarlo.")
        self._json(200, {"email": iam.settings(u["id"]).get("_contact_email") or "", "default": u["email"],
                         "env": bool(os.getenv("CONTACT_EMAIL", "").strip())})

    def POST_api_admin_contact(self, url):
        u = self.current(admin=True)
        if u["role"] != "root":
            raise HTTPError(403, "Solo il root può cambiarlo.")
        e = str(self.body().get("email") or "").strip()[:120]
        if e and not iam_mod.valid_email(e):
            raise HTTPError(400, "Email non valida.")
        iam.set_settings(u["id"], {"_contact_email": e})
        iam.audit(u["id"], "settings.contact", u["id"], e or "(email del root)")
        self._json(200, {"ok": True})

    def POST_api_reset(self, url):
        b = self.body()
        if not RL_RESET.hit(self.ip()):
            raise HTTPError(429, "Troppi tentativi: riprova tra un'ora.")
        user_id = iam.use_token("reset", b.get("token", ""))
        if not user_id:
            raise HTTPError(400, "Il link non è valido o è scaduto. Chiedine uno nuovo.")
        iam.change_password(user_id, None, b.get("password", ""), check_old=False)
        iam.logout_all(user_id, ip=self.ip())
        warn_user(user_id, "La password del tuo account è stata cambiata",
                  "La password del tuo account è appena stata cambiata con un link di reimpostazione. "
                  "Se non sei stato tu, avvisa subito l'amministratore.")
        self._json(200, {"ok": True, "message": "Password aggiornata: ora puoi accedere."})

    # --- prezzi di borsa (ISIN → simbolo → prezzo)
    def GET_api_quotes_isin(self, url):
        _need_quotes(self)
        isin = urllib.parse.parse_qs(url.query).get("isin", [""])[0].strip().upper().replace(" ", "")
        if not quotes_mod.isin_ok(isin):
            raise HTTPError(400, "Codice ISIN non valido: controlla lettere e cifre (12 caratteri, es. IE00BK5BQT80).")
        try:
            cands = QUOTES.lookup(isin)
        except quotes_mod.QuoteError as e:
            raise HTTPError(502, f"Non riesco a cercare l'ISIN: {e}.")
        if not cands:
            return self._json(200, {"isin": isin, "candidates": [], "quote": None})
        best = None
        for c in cands[:3]:  # il primo simbolo con un prezzo
            try:
                best = QUOTES.quote(c["symbol"])
                break
            except quotes_mod.QuoteError:
                continue
        self._json(200, {"isin": isin, "candidates": cands, "quote": best})

    def GET_api_quotes(self, url):
        _need_quotes(self)
        out, err = {}, {}
        for s_ in _symbols(url):
            try:
                out[s_] = QUOTES.quote(s_)
            except quotes_mod.QuoteError as e:
                err[s_] = str(e)
        self._json(200, {"quotes": out, "errors": err})

    def GET_api_quotes_history(self, url):
        _need_quotes(self)
        rng = urllib.parse.parse_qs(url.query).get("range", ["1y"])[0]
        if rng not in quotes_mod.RANGES:
            raise HTTPError(400, "periodo non valido")
        out = {}
        for s_ in _symbols(url, 25):
            try:
                out[s_] = QUOTES.history(s_, rng)
            except quotes_mod.QuoteError:
                continue
        self._json(200, {"series": out})

    # --- prodotti confezionati (Open Food Facts)
    def _need_food(self):
        u = self.current()
        if not food_on():
            raise HTTPError(503, "La ricerca dei prodotti confezionati è spenta su questo server.")
        if not RL_FOOD.hit(u["id"]):
            raise HTTPError(429, "Troppe ricerche: riprova tra qualche minuto.")

    def GET_api_food_search(self, url):
        self._need_food()
        q = urllib.parse.parse_qs(url.query).get("q", [""])[0]
        try:
            self._json(200, {"items": FOODS.search(q)})
        except food_mod.FoodError as e:
            raise HTTPError(502, f"Non riesco a cercare i prodotti: {e}.")

    def GET_api_food_barcode(self, url):
        self._need_food()
        code = urllib.parse.parse_qs(url.query).get("code", [""])[0].strip()
        if not food_mod.CODE_RE.fullmatch(code):
            raise HTTPError(400, "Il codice a barre ha da 8 a 14 cifre.")
        try:
            self._json(200, {"item": FOODS.product(code)})
        except food_mod.FoodError as e:
            raise HTTPError(502, f"Non riesco a cercare il prodotto: {e}.")

    # --- dati personali
    def GET_api_state(self, url):
        self.current()
        rev, state = store.snapshot()
        since = urllib.parse.parse_qs(url.query).get("since", [None])[0]
        if since == str(rev):
            return self._json(200, {"rev": rev, "changed": False})
        self._json(200, {"rev": rev, "changed": True, "state": state})

    def POST_api_state(self, url):
        self.current()
        b = self.body()
        if not isinstance(b.get("state"), dict):
            raise HTTPError(400, "richiesta non valida")
        ok, rev, state = store.replace(b["state"], int(b.get("rev") or 0))
        if ok:
            return self._json(200, {"rev": rev})
        self._json(409, {"rev": rev, "state": state})

    # --- profilo
    def POST_api_me_totp(self, url):
        """Verifica in due passaggi: start (con password) → QR; confirm (codice) → attiva e dà i codici di recupero; disable."""
        u = self.current()
        b = self.body()
        a = b.get("action")
        if not RL_TOTP.hit(u["id"]):
            raise HTTPError(429, "Troppi tentativi: riprova tra qualche minuto.")
        if a in ("start", "disable") and not check_password(b.get("password") or "", iam.user(u["id"])["pw_hash"]):
            raise HTTPError(400, "Password non corretta.")
        if a == "start":
            sec = iam.totp_start(u["id"])
            label = urllib.parse.quote(f"Tacuìn:{u['email']}")
            return self._json(200, {"secret": sec, "uri": f"otpauth://totp/{label}?secret={sec}&issuer={urllib.parse.quote('Tacuìn')}&digits=6&period=30"})
        if a == "confirm":
            codes = iam.totp_confirm(u["id"], b.get("code"))
            return self._json(200, {"ok": True, "recovery": codes, "user": user_payload(iam.user(u["id"]))})
        if a == "disable":
            if not iam.totp_check(u["id"], b.get("code")):
                raise HTTPError(400, "Codice non valido.")
            iam.totp_disable(u["id"])
            return self._json(200, {"ok": True, "user": user_payload(iam.user(u["id"]))})
        raise HTTPError(400, "azione sconosciuta")

    def GET_api_me(self, url):
        self._json(200, {"user": user_payload(self.current())})

    def POST_api_me_profile(self, url):
        """Nome: subito. Email: serve la password attuale e, se le email funzionano, la conferma dal nuovo indirizzo."""
        u = self.current()
        b = self.body()
        msg = "Profilo salvato."
        new = str(b.get("email") or "").strip()
        try:
            if new and new.lower() != u["email"].lower():
                if not iam_mod.check_password(str(b.get("password") or ""), u["pw_hash"]):
                    raise HTTPError(400, "Per cambiare l'email scrivi la password attuale.")
                if not iam_mod.valid_email(new) or len(new) > 120:
                    raise HTTPError(400, "Email non valida.")
                if iam.by_email(new):
                    raise HTTPError(400, "Questa email non si può usare.")
                if mailer.configured:
                    t = iam.make_token("email", u["id"], 24 * 60, extra=new)
                    mailer.send(new, "Conferma il nuovo indirizzo email", notify.email_html(
                        "Conferma il nuovo indirizzo email",
                        f"Ciao {esc(u['name'])},\nper usare <b>{esc(new)}</b> come email del tuo account Tacuìn apri il link qui sotto entro 24 ore.\n"
                        "Se non l'hai chiesto tu, ignora questo messaggio.", f"{app_url()}/api/me/email/confirm?t={t}", "Conferma l'email",
                        "Ricevi questa email perché qualcuno ha indicato questo indirizzo per un account Tacuìn."))
                    warn_user(u["id"], "Richiesta di cambio email", "È stato chiesto di cambiare l'email del tuo account. "
                              "Se non sei stato tu, cambia subito la password e avvisa l'amministratore.")
                    msg = f"Ti abbiamo scritto a {new}: apri il link per confermare il nuovo indirizzo."
                else:
                    old = u["email"]
                    iam.update_profile(u["id"], None, new)
                    warn_user(u["id"], "Email del tuo account cambiata", f"L'email del tuo account è cambiata da {old} a {new}. "
                              "Se non sei stato tu, avvisa subito l'amministratore.")
                    msg = "Email cambiata."
            iam.update_profile(u["id"], b.get("name"), None)
        except ValueError as e:
            raise HTTPError(400, str(e))
        self._json(200, {"user": user_payload(iam.user(u["id"])), "message": msg})

    def GET_api_email_verify(self, url):
        if not RL_RESET.hit(self.ip()):
            raise HTTPError(429, "Troppi tentativi: riprova tra un'ora.")
        uid_ = iam.use_token("verify", (urllib.parse.parse_qs(url.query).get("t") or [""])[0])
        if uid_:
            iam.set_settings(uid_, {"_email_unverified": False})
        body = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Tacuìn</title>"
                "<p style='font-family:sans-serif;padding:24px;font-size:17px'>"
                + ("Email confermata. Potrai accedere appena l'amministratore approva il tuo account." if uid_
                   else "Il link non è valido o è scaduto.") + "</p>")
        self._send(200 if uid_ else 400, body.encode("utf-8"), "text/html; charset=utf-8")

    def GET_api_me_email_confirm(self, url):
        if not RL_RESET.hit(self.ip()):
            raise HTTPError(429, "Troppi tentativi: riprova tra un'ora.")
        uid_, new = iam.peek_token("email", (urllib.parse.parse_qs(url.query).get("t") or [""])[0])
        ok = False
        if uid_ and new and not iam.by_email(new):
            try:
                iam.update_profile(uid_, None, new)
                iam.set_settings(uid_, {"_email_unverified": False})
                ok = True
            except ValueError:
                ok = False
        self.send_response(303)
        self.send_header("Location", "/#/profilo" if ok else "/#/home")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --- nome utente @, ricerca e avvisi di spesa condivisa
    def GET_api_handle_check(self, url):
        """«Nome utente libero?» mentre lo si sceglie (anche dalla pagina di accesso): limitato per rete."""
        if not RL_HANDLE.hit(self.ip()):
            raise HTTPError(429, "Troppe verifiche: riprova tra qualche minuto.")
        h = iam_mod.norm_handle((urllib.parse.parse_qs(url.query).get("h") or [""])[0])
        p = iam_mod.handle_problem(h)
        me = iam.session_user(self.cookie_token())
        self._json(200, {"ok": not p and iam.handle_free(h, me["id"] if me else None), "error": p or ""})

    def POST_api_me_handle(self, url):
        u = self.current()
        try:
            h = iam.set_handle(u["id"], self.body().get("handle"))
        except ValueError as e:
            raise HTTPError(400, str(e))
        self._json(200, {"ok": True, "handle": h, "user": user_payload(iam.user(u["id"]))})

    def POST_api_me_privacy(self, url):
        """«Non farti trovare»: nella ricerca compari solo a chi scrive il tuo nome utente esatto."""
        u = self.current()
        iam.set_settings(u["id"], {"hidden": bool(self.body().get("hidden"))})
        self._json(200, {"ok": True, "user": user_payload(iam.user(u["id"]))})

    def GET_api_users_search(self, url):
        u = self.current()
        if not (RL_SEARCH.hit("u:" + u["id"]) and RL_SEARCH.hit("ip:" + self.ip())):
            raise HTTPError(429, "Troppe ricerche: riprova tra qualche minuto.")
        q = (urllib.parse.parse_qs(url.query).get("q") or [""])[0][:30]
        self._json(200, {"users": iam.search_handles(q, u["id"])})

    def POST_api_me_block(self, url):
        """Blocca (o sblocca) gli avvisi di spesa condivisa da un utente, indicato con il suo nome utente."""
        u = self.current()
        b = self.body()
        other = iam.by_handle(b.get("handle"))
        if not other or other["id"] == u["id"]:
            raise HTTPError(404, "Nessun utente con questo nome utente.")
        cur = [x for x in (iam.settings(u["id"]).get("_blocked") or []) if isinstance(x, str)]
        cur = [x for x in cur if x != other["id"]] + ([other["id"]] if b.get("on", True) else [])
        iam.set_settings(u["id"], {"_blocked": cur[-200:]})
        self._json(200, {"ok": True, "blocked": self._blocked_list(u["id"])})

    def GET_api_me_block(self, url):
        u = self.current()
        self._json(200, {"blocked": self._blocked_list(u["id"])})

    def _blocked_list(self, uid_):
        out = []
        for x in iam.settings(uid_).get("_blocked") or []:
            o = iam.user(x) if isinstance(x, str) else None
            if o and o.get("handle"):
                out.append({"name": o["name"], "handle": o["handle"]})
        return out

    def POST_api_share_notify(self, url):
        """Avviso a un utente Tacuìn incluso in una spesa condivisa. Nei suoi dati non viene scritto nulla."""
        u = self.current()
        b = self.body()
        other = iam.by_handle(b.get("handle"))
        if not other or other["id"] == u["id"] or other.get("status") != "active":
            return self._json(200, {"ok": False})
        if u["id"] in (iam.settings(other["id"]).get("_blocked") or []):
            return self._json(200, {"ok": False})  # bloccato: non lo diciamo al mittente
        if not RL_SHARE.hit(u["id"]) or not RL_SHARE_PAIR.hit(u["id"] + ">" + other["id"]):
            raise HTTPError(429, "Hai inviato troppi avvisi: riprova più tardi.")
        desc = re.sub(r"\s+", " ", plain(str(b.get("desc") or ""))).strip()[:60] or "una spesa"
        quota, total = abs(num(b.get("quota"))), abs(num(b.get("total")))
        if not (0 < quota <= total <= 1_000_000):
            raise HTTPError(400, "Importi non validi.")
        who = u["name"] + (f" (@{u['handle']})" if u.get("handle") else "")
        body = f"{desc} · {eur(total)} in totale · la tua quota è {eur(quota)}"
        n = push_user(other, f"{who} ha diviso una spesa con te", body, "/#/spese/condivise", tag="share" + u["id"][:8])
        iam.audit(u["id"], "share.notify", other["id"], f"{n} dispositivi")
        self._json(200, {"ok": bool(n)})

    def POST_api_me_flags(self, url):
        """Piccole preferenze dell'interfaccia salvate nel profilo (es. guida introduttiva già vista)."""
        u = self.current()
        b = self.body()
        flags = dict(iam.settings(u["id"]).get("flags") or {})
        for k in ("tour", "setup"):
            if k in b:
                flags[k] = bool(b[k])
        iam.set_settings(u["id"], {"flags": flags})
        self._json(200, {"ok": True, "flags": flags})

    def POST_api_me_look(self, url):
        """Aspetto scelto (tema, luce calda la sera, testo): salvato nel profilo, vale su tutti i dispositivi."""
        u = self.current()
        b = self.body()
        hm = lambda v, d: v if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(v or "")) else d
        look = {"theme": b.get("theme") if b.get("theme") in ("auto", "light", "dark", "warm") else "auto",
                "night": bool(b.get("night")), "from": hm(b.get("from"), "21:00"), "to": hm(b.get("to"), "07:00"),
                "size": b.get("size") if b.get("size") in ("m", "l", "xl") else "m"}
        iam.set_settings(u["id"], {"look": look})
        self._json(200, {"ok": True, "look": look})

    def POST_api_me_password(self, url):
        u = self.current()
        b = self.body()
        try:
            iam.change_password(u["id"], b.get("old", ""), b.get("new", ""))
        except ValueError as e:
            raise HTTPError(400, str(e))
        iam.logout_others(u["id"], self.cookie_token())
        warn_user(u["id"], "La password del tuo account è stata cambiata",
                  "La password del tuo account è appena stata cambiata dal Profilo. Gli altri dispositivi sono stati scollegati. "
                  "Se non sei stato tu, avvisa subito l'amministratore.")
        self._json(200, {"ok": True, "message": "Password aggiornata. Gli altri dispositivi sono stati scollegati."})

    def POST_api_me_logout_all(self, url):
        u = self.current()
        iam.logout_all(u["id"], ip=self.ip())
        self.set_cookie("", 0)
        self._json(200, {"ok": True})

    def GET_api_push_key(self, url):
        self.current()
        self._json(200, {"ok": PUSH.ok, "key": PUSH.public_key})

    def POST_api_push_subscribe(self, url):
        u = self.current()
        if not PUSH.ok:
            raise HTTPError(503, "Le notifiche non sono disponibili su questo server.")
        if not RL_PUSH.hit(u["id"]):
            raise HTTPError(429, "Troppi tentativi: riprova più tardi.")
        b = self.body()
        sub = b.get("subscription") if isinstance(b.get("subscription"), dict) else {}
        ep, keys = str(sub.get("endpoint") or "")[:600], sub.get("keys") if isinstance(sub.get("keys"), dict) else {}
        if not webpush.allowed_endpoint(ep, PUSH.extra_hosts):
            raise HTTPError(400, "Servizio di notifiche del browser non riconosciuto.")
        try:
            if len(webpush.b64d(keys.get("p256dh"))) != 65 or len(webpush.b64d(keys.get("auth"))) < 16:
                raise ValueError
        except Exception:
            raise HTTPError(400, "Iscrizione non valida.")
        device = re.sub(r"[^\w\s.,()/+-]", "", str(b.get("device") or "Dispositivo"))[:60]
        subs = [x for x in push_subs(u) if x["endpoint"] != ep]
        subs.append({"endpoint": ep, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}, "device": device,
                     "created": datetime.now().isoformat(timespec="seconds")})
        iam.set_settings(u["id"], {"_push": subs[-PUSH_MAX_DEVICES:]})
        iam.audit(u["id"], "push.subscribe", u["id"], device, ip=self.ip())
        self._json(200, {"ok": True, "devices": len(subs[-PUSH_MAX_DEVICES:])})

    def POST_api_push_unsubscribe(self, url):
        u = self.current()
        ep = str(self.body().get("endpoint") or "")
        subs = push_subs(u)
        keep = [x for x in subs if x["endpoint"] != ep] if ep else []
        iam.set_settings(u["id"], {"_push": keep})
        self._json(200, {"ok": True, "devices": len(keep)})

    def GET_api_push_devices(self, url):
        u = self.current()
        self._json(200, {"ok": PUSH.ok, "devices": [{"device": x.get("device") or "Dispositivo", "created": x.get("created"),
                                                    "endpoint": x["endpoint"]} for x in push_subs(u)]})

    def GET_api_me_reminders(self, url):
        u = self.current()
        self._json(200, {"reminders": notify.user_reminders(iam.settings(u["id"])),
                         "meta": {k: {"label": v["label"], "hint": v["hint"], "weekly": v["day"] is not None}
                                  for k, v in notify.REMINDERS.items()},
                         "channels": {"email": mailer.configured, "push": PUSH.ok, "push_devices": len(push_subs(u))}})

    def POST_api_me_reminders(self, url):
        u = self.current()
        r = notify.clean_reminders(self.body().get("reminders"))
        iam.set_settings(u["id"], {"reminders": r})
        self._json(200, {"ok": True, "reminders": r})

    def POST_api_me_reminders_test(self, url):
        u = self.current()
        b = self.body()
        ch = b.get("channel")
        if ch not in ("email", "push"):
            raise HTTPError(400, "Canale non valido")
        if ch == "email" and not mailer.configured:
            raise HTTPError(400, "L'invio email non è ancora configurato sul server (SMTP nel file .env).")
        if ch == "push" and not push_subs(u):
            raise HTTPError(400, "Attiva prima le notifiche su questo dispositivo.")
        sent = scheduler.deliver(b.get("kind") if b.get("kind") in notify.REMINDERS else "morning", u, force_channels=[ch])
        if not sent:
            if ch == "email" and mailer.last_error:
                raise HTTPError(502, f"Invio non riuscito: {mask_emails(mailer.last_error)}")
            raise HTTPError(400, "Per ora non c'è niente da inviare per questo promemoria.")
        self._json(200, {"ok": True, "message": "Inviato! Controlla " + ("la posta (anche lo spam)." if ch == "email" else "le notifiche del dispositivo.")})

    def GET_api_me_export(self, url):
        u = self.current()
        data = {"user": {k: u[k] for k in ("name", "email", "created")}, "state": store.read()}
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8",
                   {"Content-Disposition": f'attachment; filename="tracker-{date.today().isoformat()}.json"'})

    def POST_api_me_delete(self, url):
        u = self.current()
        b = self.body()
        if u["role"] == "root":
            raise HTTPError(403, "L'account root non si può eliminare.")
        from iam import check_password
        if not check_password(b.get("password", ""), u["pw_hash"]):
            raise HTTPError(400, "Password non corretta.")
        iam.audit(u["id"], "user.self_delete", u["id"], u["email"], self.ip())
        db.run("DELETE FROM users WHERE id=?", (u["id"],))
        db.run("DELETE FROM invites WHERE owner_id=?", (u["id"],))
        self.set_cookie("", 0)
        self._json(200, {"ok": True})

    # --- assistente
    def _pack(self, res):
        if res is None:
            return None
        text, kb = res if isinstance(res, tuple) else (res, None)
        return {"html": text, "buttons": [[{"text": b["text"], "data": b["callback_data"]} for b in row] for row in (kb or [])]}

    def POST_api_chat(self, url):
        u = self.current()
        text = str(self.body().get("text", ""))[:1000]
        CHAT_EXTRA.set(None)
        try:
            res = process(text, f"web:{u['id']}")
        except Reply as r:
            res = (r.text, r.kb)
        msg = self._pack(res)
        extra = CHAT_EXTRA.get()
        if extra:
            msg.update(extra)
        self._json(200, {"messages": [msg]})

    def POST_api_chat_action(self, url):
        u = self.current()
        r = callback_action(str(self.body().get("data", "")), f"web:{u['id']}")
        self._json(200, {"toast": r.get("toast"), "edit": self._pack(r.get("edit")), "new": self._pack(r.get("new"))})

    def POST_api_receipt(self, url):
        u = self.current()
        if not RL_RECEIPT.hit(u["id"]):
            raise HTTPError(429, "Troppi scontrini in poco tempo: riprova tra qualche minuto.")
        n = int(self.headers.get("Content-Length", "0"))
        if not n or n > 15 * 1024 * 1024:
            raise HTTPError(413, "Foto mancante o troppo grande (max 15 MB).")
        data = self.rfile.read(n)
        try:
            d = receipt_draft(data)
        except ValueError as e:
            raise HTTPError(502 if "servizio" in str(e) else 400, str(e))
        d["categories"] = store.read()["expenses"]["categories"]
        self._json(200, d)

    def POST_api_calendar_notify(self, url):
        u = self.current()
        b = self.body()
        ev = b.get("event") if isinstance(b.get("event"), dict) else None
        kind = b.get("kind")
        if not ev or kind not in ("invite", "update", "cancel") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(ev.get("date", ""))):
            raise HTTPError(400, "richiesta non valida")
        ev = clean_event(ev)
        sent, failed = notify_people(u, ev, kind)
        self._json(200, {"sent": sent, "failed": failed})

    def GET_api_invite(self, url):
        """Invito per chi usa Tacuìn (dal link nell'email o dalla notifica)."""
        u = self.current()
        inv = invite_get((urllib.parse.parse_qs(url.query).get("t") or [""])[0])
        if not inv:
            raise HTTPError(404, "Invito non trovato.")
        if inv["email"].lower() != u["email"].lower():
            raise HTTPError(403, "Questo invito è per un altro indirizzo email: entra con quell'account per rispondere.")
        owner = iam.user(inv["owner_id"]) or {}
        ev = inv["ev"]
        self._json(200, {"event": ev, "when": ev_when(ev), "from": owner.get("name") or "", "rsvp": inv.get("rsvp"),
                         "cancelled": False})

    def POST_api_invite(self, url):
        u = self.current()
        b = self.body()
        inv = invite_get(b.get("t"))
        if not inv:
            raise HTTPError(404, "Invito non trovato.")
        if inv["email"].lower() != u["email"].lower():
            raise HTTPError(403, f"Questo invito è per {inv['email']}.")
        r = RSVP_SLUG.get(str(b.get("r") or ""))
        if not r:
            raise HTTPError(400, "Risposta non valida.")
        record_rsvp(inv, r)
        self._json(200, {"ok": True, "rsvp": r})

    def POST_api_invite_answer(self, url):
        """Risposta di chi non usa Tacuìn (pagina pubblica dell'invito, protetta dal token personale)."""
        if not RL_INVITE.hit(self.ip()):
            raise HTTPError(429, "Troppe richieste: riprova più tardi.")
        b = self.body()
        inv = invite_get(b.get("t"))
        r = RSVP_SLUG.get(str(b.get("r") or ""))
        if not inv or not r:
            raise HTTPError(404, "Invito non trovato o scaduto.")
        record_rsvp(inv, r)
        self._json(200, {"ok": True})

    def GET_api_invite_ics(self, url):
        if not RL_INVITE.hit(self.ip()):
            raise HTTPError(429, "Troppe richieste: riprova più tardi.")
        inv = invite_get((urllib.parse.parse_qs(url.query).get("t") or [""])[0])
        if not inv:
            raise HTTPError(404, "Invito non trovato.")
        owner = iam.user(inv["owner_id"]) or {}
        body = ics_event(inv["ev"], "PUBLISH", (owner.get("name") or "", owner.get("email") or ""))
        name = re.sub(r"[^\w-]+", "-", inv["ev"].get("title") or "appuntamento").strip("-")[:40] or "appuntamento"
        self._send(200, body, "text/calendar; charset=utf-8", {"Content-Disposition": f'attachment; filename="{name}.ics"'})

    def GET_invito(self, url):
        """Pagina pubblica dell'invito, per chi non usa Tacuìn: mostra l'appuntamento e fa rispondere con un tocco."""
        if not RL_INVITE.hit(self.ip()):
            raise HTTPError(429, "Troppe richieste: riprova più tardi.")
        q = urllib.parse.parse_qs(url.query)
        inv = invite_get((q.get("t") or [""])[0])
        if not inv:
            body = "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Invito</title>" \
                   "<p style='font-family:sans-serif;padding:24px'>Questo invito non esiste più o il link è incompleto.</p>"
            return self._send(404, body.encode("utf-8"), "text/html; charset=utf-8")
        owner = iam.user(inv["owner_id"]) or {}
        ev, r0 = inv["ev"], RSVP_SLUG.get((q.get("r") or [""])[0], "")
        cur_r = inv.get("rsvp") or ""
        btns = "".join(f'<button data-r="{k}" class="{"on" if k == cur_r else "pre" if k == r0 else ""}">{v}</button>'
                       for k, v in RSVP_L.items())
        intro = (f'<p id="intro" style="margin:16px 0 0">Confermi la risposta <b>{RSVP_L[r0]}</b>? Tocca il pulsante qui sotto.</p>' if r0 and r0 != cur_r
                 else (f'<p id="intro" style="margin:16px 0 0">Hai già risposto: <b>{RSVP_L[cur_r]}</b>. Puoi cambiare idea.</p>' if cur_r else ""))
        g, o, ics = cal_links(ev, inv["token"])
        vals = {"WHO": esc(owner.get("name") or "Qualcuno"), "TITLE": esc(ev.get("title") or "Appuntamento"), "WHEN": esc(ev_when(ev)),
                "PLACE": f'<div class="m">{esc(ev["place"])}</div>' if ev.get("place") else "",
                "BODY": intro + f'<div class="r">{btns}</div><div id="ok" class="ok" hidden></div>'
                        f'<div class="r"><a class="b" href="{esc(g)}" target="_blank" rel="noopener">Google Calendar</a>'
                        f'<a class="b" href="{esc(o)}" target="_blank" rel="noopener">Outlook</a><a class="b" href="{esc(ics)}">Apple / altro (.ics)</a></div>',
                "TOK": json.dumps(inv["token"]), "R": json.dumps(r0)}
        # una sola passata: il testo scritto da chi invita non può mai diventare un segnaposto
        page = re.sub(r"__(WHO|TITLE|WHEN|PLACE|BODY|TOK|R)__", lambda m: vals[m.group(1)], INVITE_PAGE)
        self._nonce = secrets.token_urlsafe(18)
        self._send(200, page.replace("<script>", f'<script nonce="{self._nonce}">').encode("utf-8"), "text/html; charset=utf-8")

    def GET_api_garmin(self, url):
        u = self.current()
        c = iam.settings(u["id"]).get("_garmin") or {}
        self._json(200, {"connected": bool(c.get("key")), "last_sync": c.get("last_sync"), "error": c.get("error"), "count": c.get("count") or 0})

    def POST_api_garmin(self, url):
        u = self.current()
        b = self.body()
        a = b.get("action")
        try:
            if a == "connect":
                if not iam.settings(u["id"]).get("health_consent"):
                    raise HTTPError(400, "Prima serve il consenso ai dati sulla salute.")
                key = str(b.get("key") or "").strip()
                if len(key) < 10:
                    raise HTTPError(400, "Incolla la chiave personale che ti dà Garmin Connect.")
                tok, exp = GARMIN.token(key)
                iam.set_settings(u["id"], {"_garmin": {"key": key, "token": tok, "exp": exp, "count": 0}})
                iam.audit(u["id"], "garmin.link", u["id"])
                n, tot = garmin_sync(u, days=60)
                return self._json(200, {"ok": True, "message": f"Garmin collegato: importate {n} attività degli ultimi 60 giorni, con passi e battito."})
            if a == "sync":
                n, tot = garmin_sync(u)
                return self._json(200, {"ok": True, "message": f"Sincronizzato: {n} {'nuova attività' if n == 1 else 'nuove attività'}." if n else "Sincronizzato: nessuna attività nuova."})
            if a == "wellness":  # aggiornamento veloce di passi e battito (la pagina lo chiede se i dati hanno più di 10 minuti)
                w = garmin_wellness(u)
                return self._json(200, {"ok": True, "error": w.get("err")})
            if a == "disconnect":  # revoca del consenso: via anche passi, battito e attività importate
                iam.set_settings(u["id"], {"_garmin": {}, "health_consent": None})
                CURRENT_USER.set(u["id"])

                def wipe(st):
                    st.pop("garmin", None)
                    g = st.get("gym") or {}
                    before = len(g.get("sessions") or [])
                    g["sessions"] = [x for x in g.get("sessions") or [] if x.get("source") != "garmin"]
                    return before - len(g["sessions"])
                n = store.mutate(wipe)
                iam.audit(u["id"], "garmin.unlink", u["id"], f"{n} attività cancellate")
                return self._json(200, {"ok": True, "message": f"Garmin scollegato e consenso revocato: cancellati passi, battito e {n} "
                                                               f"{'attività importata' if n == 1 else 'attività importate'}."})
        except garmin.GarminError as e:
            raise HTTPError(502, str(e))
        raise HTTPError(400, "azione sconosciuta")

    def POST_api_garmin_fit(self, url):
        u = self.current()
        if not iam.settings(u["id"]).get("health_consent"):
            raise HTTPError(400, "Prima serve il consenso ai dati sulla salute.")
        n = int(self.headers.get("Content-Length", "0"))
        if not n or n > 30 * 1024 * 1024:
            raise HTTPError(413, "File mancante o troppo grande (max 30 MB).")
        try:
            ss = fit.sessions(self.rfile.read(n))
        except (fit.FitError, struct_error, zipfile_error) as e:
            raise HTTPError(400, str(e) or "File non leggibile")
        out = []
        for x in ss:
            typ = x["sub_sport"] if x["sub_sport"] in ("treadmill", "indoor_cycling", "indoor_rowing", "elliptical", "stair_climbing", "lap_swimming", "open_water",
                                                       "strength_training", "yoga", "pilates", "indoor_climbing", "bouldering", "hiit", "flexibility_training") else x["sport"]
            typ = {"treadmill": "treadmill_running", "open_water": "open_water_swimming", "flexibility_training": "training_flexibility_training"}.get(typ, typ)
            loc = x["start"].astimezone().replace(tzinfo=None)
            out.append(garmin_session(f"fit-{int(x['start'].timestamp())}", typ, loc, x["elapsed_s"], x["distance_m"], x["calories"], x["avg_hr"], x["max_hr"]))
        added = garmin_merge(out)
        self._json(200, {"added": added, "found": len(out),
                         "message": f"Importate {added} {'attività' if added != 1 else 'attività'}" + (f" ({len(out) - added} c'erano già)" if len(out) > added else "") + "."})

    def GET_api_geo(self, url):
        self.current()
        q = urllib.parse.parse_qs(url.query).get("q", [""])[0][:80]
        try:
            self._json(200, {"results": TRAVEL.geocode(q)})
        except ValueError as e:
            raise HTTPError(502, str(e))

    def GET_api_weather(self, url):
        self.current()
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            lat, lon = float(qs["lat"]), float(qs["lon"])
            for k in ("start", "end"):
                if qs.get(k):
                    date.fromisoformat(qs[k])
        except (KeyError, ValueError):
            raise HTTPError(400, "parametri non validi")
        try:
            self._json(200, TRAVEL.weather(lat, lon, qs.get("start") or None, qs.get("end") or None))
        except ValueError as e:
            raise HTTPError(502, str(e))

    def GET_api_insights(self, url):
        self.current()
        self._json(200, {"items": brain.insights(store.read())})

    # --- amministrazione
    def GET_api_admin_users(self, url):
        self.current(admin=True)
        users = []
        for x in iam.list_users():  # solo i dati per gestire l'account: niente impostazioni, consensi o preferenze
            users.append({"id": x["id"], "email": x["email"], "name": x["name"], "handle": x.get("handle"), "role": x["role"],
                          "role_label": ROLE_LABEL.get(x["role"], x["role"]), "status": x["status"], "created": x["created"],
                          "last_login": x.get("last_login"), "totp": iam.totp_on(x["id"]),
                          "invites_off": bool(iam.settings(x["id"]).get("_no_invites")),
                          "source": SOURCES.get(iam.settings(x["id"]).get("_source") or "", "")})
        by_source = {}
        for x in users:
            if x["source"]:
                by_source[x["source"]] = by_source.get(x["source"], 0) + 1
        self._json(200, {"users": users, "stats": iam.stats(), "sources": by_source,
                         "email": {"configured": mailer.configured, "error": mask_emails(mailer.last_error)}})

    ADMIN_AUDIT = ("admin.", "user.register", "user.create", "login.fail", "user.self_delete", "settings.legal", "settings.contact")

    def GET_api_admin_audit(self, url):
        """Registro per root/admin: solo eventi di gestione e sicurezza; niente IP o dettagli personali degli utenti."""
        self.current(admin=True)
        items = []
        for r in iam.audit_list(1500):
            a = r.get("action") or ""
            if not any(a == k or (k.endswith(".") and a.startswith(k)) for k in self.ADMIN_AUDIT):
                continue
            if not a.startswith("admin."):
                r["ip"] = None
                r["detail"] = mask_emails(r.get("detail"))
            items.append(r)
            if len(items) >= 300:
                break
        self._json(200, {"items": items})

    def POST_api_admin_user(self, url):
        actor = self.current(admin=True)
        b = self.body()
        target = iam.user(b.get("id", ""))
        if b.get("action") == "reset_link" and not mailer.configured:
            # senza email il link finirebbe all'admin, che potrebbe entrare nell'account: non lo permettiamo
            raise HTTPError(400, "Per mandare un link di nuova password servono le email (SMTP nel file .env): il link va solo all'utente.")
        r = iam.admin_action(actor, b.get("id", ""), b.get("action", ""), b.get("value"), self.ip())
        base = self.base_url()
        if r.get("token"):
            link = f"{base}/#/reimposta/{r.pop('token')}"
            # l'admin non deve poter entrare nell'account di un altro: il link va solo all'utente, per email.
            # Solo se l'email non è configurata viene mostrato all'admin, che lo consegna di persona.
            if email_user(target, "Imposta una nuova password", f"Ciao {esc(target['name'])},\n"
                          "ecco il link per scegliere una nuova password. Vale 24 ore.", "Scegli la password", link,
                          "Se non l'hai chiesto tu, puoi ignorare questa email."):
                r["message"] = f"Link per la nuova password inviato via email a {target['name']} (vale 24 ore)."
            else:
                iam.use_token("reset", link.rsplit("/", 1)[-1])  # invio non riuscito: il link non resta valido e non viene mostrato
                raise HTTPError(502, "Invio dell'email non riuscito: il link non è stato creato. Controlla le impostazioni email.")
        elif b.get("action") == "approve" and target:
            if email_user(target, "Il tuo account è attivo", f"Ciao {esc(target['name'])},\n"
                          "l'amministratore ha approvato il tuo account: ora puoi accedere a Tacuìn.", "Accedi", base):
                r["message"] += " Gli ho inviato un'email."
        self._json(200, r)


# ---------------------------------------------------------------- assistente: logica dei comandi
class Reply(Exception):
    def __init__(self, text, kb=None):
        super().__init__(text)
        self.text, self.kb = text, kb


UNDO = {}

HELP = (
    "👋 <b>Ciao!</b> Scrivimi così:\n\n"
    "💸 <b>spesa 12,50 pizza</b> — registra un'uscita\n"
    "     (basta anche <b>12,50 pizza</b>, o più spese: <b>ieri 40 benzina e 12 pranzo</b>)\n"
    "💰 <b>entrata 1500 stipendio</b>\n"
    "🏷 aggiungi <b>#categoria</b> per sceglierla, <b>ieri</b> o <b>28/10</b> per la data\n\n"
    "✅ <b>fatto acqua</b> — segna un'abitudine di oggi\n"
    "     <b>abitudini</b> — lista con i pulsanti da toccare\n\n"
    "📈 <b>prezzo VWCE 110,5</b> — aggiorna il prezzo di un investimento\n"
    "📋 <b>progetti</b> — cosa c'è da fare nel progetto «Progetti»\n"
    "     <b>progetti: comprare vernice</b> — aggiunge una voce\n"
    "     <b>tracker</b> — elenco di tutti i tuoi progetti\n"
    "     <b>crea progetto giochi</b> — nuovo progetto (puoi aggiungere le colonne:\n"
    "     <b>crea progetto giochi: piattaforma, voto (numero), uscita (data)</b>)\n"
    "     <b>elimina progetto giochi</b>\n\n"
    "👥 <b>condivisa 60 cena con marco e giulia</b> — spesa da dividere\n"
    "     <b>condivisa 90 cena con tony 1/3</b> — a quote (anche 40% o 30€; il resto è tuo)\n"
    "     <b>marco ha pagato 40 benzina</b> · <b>conti</b> — chi deve a chi\n"
    "     <b>saldato marco</b> — registra che avete pareggiato\n\n"
    "📝 <b>da fare: pagare il bollo, chiamare Marco</b> · <b>da fare domani: …</b> · <b>da fare</b> per vederle\n"
    "📖 <b>diario: oggi è stata una bella giornata</b> — scrive nel diario di oggi\n\n"
    "📅 <b>dentista venerdì alle 15:30</b> — nuovo appuntamento (anche <b>calcetto ogni giovedì alle 19</b>)\n"
    "     <b>cosa ho domani</b> · <b>agenda</b> · <b>cosa è successo ieri</b> · <b>cancella appuntamento dentista</b>\n\n"
    "✈️ <b>viaggi</b> — i prossimi viaggi · <b>meteo lisbona</b> — previsioni\n"
    "🧾 <b>manda una foto dello scontrino</b> — la leggo e registro la spesa\n\n"
    "📝 <b>da fare</b> — tutto quello che ti resta: abitudini di oggi e voci aperte\n"
    "🧠 <b>analisi</b> — previsioni, anomalie, abbonamenti, abitudini\n"
    "❓ <b>quanto ho speso in svago a ottobre?</b> — domande sulle tue spese\n"
    "📊 <b>oggi</b> · <b>mese</b> — riepiloghi\n"
    "🗑 <b>cancella ultima spesa</b> · <b>cancella pizza</b> · <b>cancella spesa di ieri da 12</b>\n"
    "↩️ <b>annulla</b> — annulla l'ultima operazione"
)


def push_undo(chat, op):
    ops = UNDO.setdefault(chat, [])
    ops.append(op)
    del ops[:-20]


def month_totals(st, ym):
    items = [x for x in st["expenses"]["items"] if str(x.get("date", "")).startswith(ym)]
    inc = sum(num(x.get("amount")) for x in items if x.get("type") == "in")
    out = sum(num(x.get("amount")) for x in items if x.get("type") == "out")
    return items, inc, out


def match_category(desc, hint, cats, typ):
    """Ritorna (categoria, è_nuova)."""
    ncats = {norm(c): c for c in cats}
    if hint:
        h = norm(hint)
        for nc, c in ncats.items():
            if nc == h or nc.startswith(h) or h in nc:
                return c, False
        return hint[:1].upper() + hint[1:], True
    nd = norm(desc)
    words = re.findall(r"\w+", nd)
    for w in words:
        for nc, c in ncats.items():
            if w == nc or (len(w) >= 4 and nc.startswith(w)):
                return c, False
    for cat, kws in KEYWORDS.items():
        if norm(cat) in ncats and any(k in words for k in kws):
            return ncats[norm(cat)], False
    if "altro" in ncats:
        return ncats["altro"], False
    return (cats[0] if cats else "Altro"), not cats


def guess_category(st, desc, hint, typ):
    """Categoria: #suggerimento > modello ML (impara dalle tue spese) > parole chiave. Ritorna (cat, nuova, info_ml)."""
    E = st["expenses"]
    if not hint and typ == "out" and desc:
        p = brain.CategoryModel(E["items"], E["categories"]).predict(desc)
        if p:
            return p["category"], False, p
    cat, new = match_category(desc, hint, E["categories"], typ)
    return cat, new, None


def ml_note(ml):
    if not ml:
        return None
    if ml["source"] == "memoria":
        return f"🧠 come le altre {ml['similar']} volte" if ml["similar"] > 1 else "🧠 come l'ultima volta"
    n = ml["similar"]
    return f"🧠 dedotta da {n} {'spesa simile' if n == 1 else 'spese simili'} ({ml['confidence'] * 100:.0f}%)"


def parse_expense_words(words):
    """[parole] → (data, importo, descrizione, #categoria). Importo None se manca."""
    d, words = take_date(words)
    amt, hint, desc = None, None, []
    for w in words:
        if amt is None:
            a = parse_amount(w)
            if a is not None:
                amt = a
                continue
        if w.startswith("#") and len(w) > 1:
            hint = w[1:].replace("_", " ")
            continue
        if norm(w) in FILLER:
            continue
        desc.append(w)
    desc_s = " ".join(desc).strip()
    return d, amt, desc_s[:1].upper() + desc_s[1:], hint


def expense_kb(item_id):
    return [[{"text": "🏷 Cambia categoria", "callback_data": f"c|{item_id}"}, {"text": "↩ Annulla", "callback_data": f"u|{item_id}"}]]


def split_multi(words):
    """'ieri 40 benzina e 12 pranzo' → [['ieri','40','benzina'], ['ieri','12','pranzo']] se ci sono più importi."""
    if sum(1 for w in words if parse_amount(w) is not None) < 2:
        return None
    dates = [w for w in words if norm(w) in ("ieri", "altroieri", "oggi") or re.fullmatch(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", w)]
    segs, cur = [], []
    for w in words:
        if w in dates:
            continue
        if norm(w) in ("e", "poi", "più", "piu", "+", ",", ";") or w.endswith(","):
            if w.endswith(",") and len(w) > 1:
                cur.append(w[:-1])
            if cur:
                segs.append(cur)
            cur = []
            continue
        cur.append(w)
    if cur:
        segs.append(cur)
    segs = [dates + sg for sg in segs]
    if len(segs) < 2 or any(sum(1 for w in sg if parse_amount(w) is not None) != 1 for sg in segs):
        return None
    return segs


def add_expense(words, typ, chat, assumed=False):
    multi = split_multi(words)
    if multi:
        return add_expenses_multi(multi, typ, chat)
    d, amt, desc_s, hint = parse_expense_words(words)
    if not amt:
        raise Reply("Manca l'importo 🤔\nEsempio: <b>spesa 12,50 pizza</b>")

    def fn(st):
        E = st["expenses"]
        cat, new, ml = guess_category(st, desc_s, hint, typ)
        if new:
            E["categories"].append(cat)
        item = {"id": uid(), "date": iso(d), "desc": desc_s, "category": cat, "type": typ, "amount": round(amt, 2)}
        E["items"].append(item)
        _, _, out = month_totals(st, iso(d)[:7])
        return item, out, new, ml

    item, out, new, ml = store.mutate(fn)
    push_undo(chat, ("expense", item["id"]))
    head = ("💸 Spesa" if typ == "out" else "💰 Entrata") + f" registrata: <b>{eur(amt)}</b>"
    if desc_s:
        head += f" · {esc(desc_s)}"
    lines = [head, f"🏷 <b>{esc(item['category'])}</b>{' (nuova categoria)' if new else ''} · 📅 {day_label(d)}"]
    if ml:
        lines.append(f"<i>{ml_note(ml)}</i>")
    if typ == "out":
        lines.append(f"Uscite di {MESI[d.month - 1].lower()}: {eur(out)}")
    if assumed:
        lines.append("<i>L'ho registrata come spesa.</i>")
    return "\n".join(lines), expense_kb(item["id"])


def add_expenses_multi(segs, typ, chat):
    parsed = [parse_expense_words(sg) for sg in segs]

    def fn(st):
        E, res = st["expenses"], []
        for d, amt, desc_s, hint in parsed:
            cat, new, ml = guess_category(st, desc_s, hint, typ)
            if new:
                E["categories"].append(cat)
            item = {"id": uid(), "date": iso(d), "desc": desc_s, "category": cat, "type": typ, "amount": round(amt, 2)}
            E["items"].append(item)
            res.append((item, ml))
        return res

    res = store.mutate(fn)
    push_undo(chat, ("expenses", [it["id"] for it, _ in res]))
    days = {it["date"] for it, _ in res}
    when = day_label(date.fromisoformat(res[0][0]["date"])) if len(days) == 1 else "date diverse"
    lines = [f"{'💸' if typ == 'out' else '💰'} Ho registrato <b>{len(res)} {'spese' if typ == 'out' else 'entrate'}</b> · {when}:"]
    lines += [f"· {esc(it['desc'] or it['category'])} · <i>{esc(it['category'])}</i> — <b>{eur(it['amount'])}</b>" for it, _ in res]
    if any(ml for _, ml in res):
        lines.append("<i>🧠 categorie dedotte dalle tue spese passate</i>")
    lines.append("<i>Scrivi «annulla» per annullarle tutte.</i>")
    return "\n".join(lines)


def category_menu(item_id):
    st = store.read()
    it = next((x for x in st["expenses"]["items"] if x["id"] == item_id), None)
    if not it:
        return "Questa spesa non esiste più."
    cats = st["expenses"]["categories"]
    btns = [{"text": ("✓ " if c == it["category"] else "") + c, "callback_data": f"cs|{item_id}|{i}"} for i, c in enumerate(cats[:40])]
    return (f"🏷 Scegli la categoria per <b>{esc(it['desc'] or 'questa spesa')}</b> ({eur(num(it['amount']))}).\n"
            f"<i>Me lo ricorderò per le prossime volte.</i>", [btns[i:i + 2] for i in range(0, len(btns), 2)])


def set_category(item_id, idx):
    def fn(st):
        it = next((x for x in st["expenses"]["items"] if x["id"] == item_id), None)
        cats = st["expenses"]["categories"]
        if not it or not (0 <= idx < len(cats)):
            return None
        it["category"] = cats[idx]
        return it
    return store.mutate(fn)


def habits_kb(st, d):
    k = iso(d)
    return [[{"text": ("✅ " if (h.get("done") or {}).get(k) else "⬜️ ") + h["name"],
              "callback_data": f"h|{h['id']}|{k}"}] for h in st["habits"]["list"]]


def habits_menu(d=None):
    d = d or today()
    st = store.read()
    H = st["habits"]["list"]
    if not H:
        return "Non hai ancora abitudini: aggiungile dalla web app con il pulsante +."
    n = sum(1 for h in H if (h.get("done") or {}).get(iso(d)))
    return f"✅ <b>Abitudini di {day_label(d)}</b> · {n}/{len(H)}\nTocca per segnare o togliere:", habits_kb(st, d)


def habit_done(words, chat):
    d, words = take_date(words)
    q = norm(" ".join(words))
    st = store.read()
    H = st["habits"]["list"]
    m = [h for h in H if q and q in norm(h["name"])]
    if not m:
        m = [h for h in H if any(len(w) >= 3 and w in norm(h["name"]) for w in q.split())]
    if len(m) != 1:
        if m:
            return "Quale abitudine? Tocca per segnare:", [r for r in habits_kb(st, d) if r[0]["callback_data"].split("|")[1] in {h["id"] for h in m}]
        if not H:
            return "Non hai ancora abitudini: aggiungile dalla web app."
        return f"Non trovo un'abitudine simile a «{esc(q)}». Tocca quella giusta:", habits_kb(st, d)
    hid, k = m[0]["id"], iso(d)

    def fn(st):
        h = next(x for x in st["habits"]["list"] if x["id"] == hid)
        done = h.setdefault("done", {})
        was = bool(done.get(k))
        done[k] = True
        if not h.get("created") or k < h["created"]:
            h["created"] = k
        n = sum(1 for x in st["habits"]["list"] if (x.get("done") or {}).get(k))
        return h["name"], was, streak(h), n, len(st["habits"]["list"])

    name, was, s, n, tot = store.mutate(fn)
    if was:
        return f"👌 «{esc(name)}» era già segnata per {day_label(d)}."
    push_undo(chat, ("habit", hid, k))
    txt = f"✅ <b>{esc(name)}</b> segnata per {day_label(d)}"
    if s > 1:
        txt += f"\n🔥 Serie: {s} giorni"
    txt += f"\n{day_label(d).capitalize()}: {n}/{tot} completate"
    return txt + ("\n🎉 Tutte fatte, grande!" if n == tot and d == today() else "")


def update_price(words, chat):
    amt, name = None, []
    for w in words:
        a = parse_amount(w)
        if a is not None:
            amt = a
        else:
            name.append(w)
    if amt is None or not name:
        raise Reply("Esempio: <b>prezzo VWCE 110,5</b>")
    q = norm(" ".join(name))
    items = store.read()["investments"]["items"]
    m = [x for x in items if q in norm(x.get("name", ""))]
    if len(m) != 1:
        names = ", ".join(esc(x.get("name") or "senza nome") for x in (m or items)) or "nessuno"
        return ("Più investimenti corrispondono: " if m else f"Non trovo «{esc(q)}». I tuoi investimenti: ") + names
    xid = m[0]["id"]

    def fn(st):
        x = next(i for i in st["investments"]["items"] if i["id"] == xid)
        old = (x.get("price", ""), x.get("updated"))
        x["price"], x["updated"] = amt, iso(today())
        qty = num(x.get("qty"))
        return x.get("name"), old, qty * num(x.get("avg")), qty * amt

    nm, old, inv, val = store.mutate(fn)
    push_undo(chat, ("price", xid, old[0], old[1]))
    pl = val - inv
    txt = f"📈 <b>{esc(nm)}</b>: prezzo aggiornato a {eur(amt)}\nValore: {eur(val)} · Guadagno: {eur(pl)}"
    return txt + (f" ({pct(pl / inv * 100)})" if inv else "")


def tracker_add(tr_id, body, chat):
    def fn(st):
        t = next(x for x in st["trackers"] if x["id"] == tr_id)
        col = next((c for c in t["columns"] if c.get("type") == "text"), None)
        if not col:
            col = {"id": uid(), "name": "Nome", "type": "text"}
            t["columns"].insert(0, col)
        row = {"id": uid(), "cells": {col["id"]: body}}
        m = tracker_meta(t)
        if m["status"] and (m["status"].get("options") or []):
            row["cells"][m["status"]["id"]] = m["status"]["options"][0]
        t["rows"].append(row)
        return t.get("name") or "Senza titolo", row["id"], len(t["rows"]), t.get("icon") or "📋"

    name, rid, n, icon = store.mutate(fn)
    push_undo(chat, ("row", tr_id, rid))
    return f"{esc(icon)} Aggiunto a <b>{esc(name)}</b>: {esc(body)}\n" + ("È la prima voce." if n == 1 else f"Ora ci sono {n} voci.")


def undo(chat):
    ops = UNDO.get(chat)
    if not ops:
        return "Non c'è niente da annullare."
    op = ops.pop()

    def fn(st):
        kind = op[0]
        if kind == "restore_expense":
            items = st["expenses"]["items"]
            if not any(x["id"] == op[1]["id"] for x in items):
                items.insert(min(op[2], len(items)), op[1])
                trash_take(st, op[1]["id"])
                return f"↩️ Ripristinata: {exp_label(op[1])}"
        if kind == "expenses":
            ids = set(op[1])
            before = len(st["expenses"]["items"])
            st["expenses"]["items"] = [x for x in st["expenses"]["items"] if x["id"] not in ids]
            n = before - len(st["expenses"]["items"])
            if n:
                return f"↩️ Annullate {n} spese."
        if kind == "expense":
            items = st["expenses"]["items"]
            it = next((x for x in items if x["id"] == op[1]), None)
            if it:
                items.remove(it)
                return f"↩️ Annullata: {eur(num(it.get('amount')))} · {esc(it.get('desc') or it.get('category'))}"
        elif kind == "habit":
            h = next((x for x in st["habits"]["list"] if x["id"] == op[1]), None)
            if h:
                (h.get("done") or {}).pop(op[2], None)
                return f"↩️ «{esc(h['name'])}» non è più segnata."
        elif kind == "row":
            t = next((x for x in st["trackers"] if x["id"] == op[1]), None)
            if t:
                t["rows"] = [r for r in t["rows"] if r["id"] != op[2]]
                return f"↩️ Voce rimossa da «{esc(t.get('name'))}»."
        elif kind == "shared":
            S = st["expenses"]["shared"]
            x = next((i for i in S["items"] if i["id"] == op[1]), None)
            if x:
                S["items"].remove(x)
                st["expenses"]["items"] = [e for e in st["expenses"]["items"] if e["id"] != op[2]]
                return f"↩️ Spesa condivisa annullata: {eur(num(x.get('amount')))} · {esc(x.get('desc') or '')}"
        elif kind == "settle":
            S = st["expenses"]["shared"]
            if any(z["id"] == op[1] for z in S["settlements"]):
                S["settlements"] = [z for z in S["settlements"] if z["id"] != op[1]]
                return "↩️ Saldo annullato."
        elif kind == "trnew":
            t = next((x for x in st["trackers"] if x["id"] == op[1]), None)
            if t:
                if t.get("rows"):
                    return f"«{esc(t.get('name'))}» contiene già delle voci: se vuoi eliminarlo scrivi «elimina progetto {esc(t.get('name'))}»."
                st["trackers"].remove(t)
                return f"↩️ Progetto «{esc(t.get('name'))}» rimosso."
        elif kind == "trdel":
            if not any(x["id"] == op[1]["id"] for x in st["trackers"]):
                st["trackers"].insert(min(op[2], len(st["trackers"])), op[1])
                trash_take(st, op[1]["id"])
                return f"↩️ Progetto «{esc(op[1].get('name'))}» recuperato con tutte le sue voci."
        elif kind == "rowdone":
            t = next((x for x in st["trackers"] if x["id"] == op[1]), None)
            r = next((x for x in (t or {}).get("rows", []) if x["id"] == op[2]), None)
            if r:
                r.setdefault("cells", {}).update(op[3])
                return f"↩️ Voce di «{esc(t.get('name'))}» di nuovo da fare."
        elif kind == "event":
            evs = st.get("calendar", {}).get("events", [])
            ev = next((x for x in evs if x["id"] == op[1]), None)
            if ev:
                evs.remove(ev)
                return f"↩️ Appuntamento «{esc(ev.get('title'))}» tolto dal calendario."
        elif kind == "restore_event":
            evs = st.setdefault("calendar", {}).setdefault("events", [])
            if not any(x["id"] == op[1]["id"] for x in evs):
                evs.insert(min(op[2], len(evs)), op[1])
                trash_take(st, op[1]["id"])
                return f"↩️ Appuntamento «{esc(op[1].get('title'))}» di nuovo in calendario."
        elif kind == "todos":
            ids = set(op[1])
            n = len(st.get("todos", []))
            st["todos"] = [x for x in st.get("todos", []) if x["id"] not in ids]
            if n != len(st["todos"]):
                return "↩️ Tolte dalle cose da fare."
        elif kind == "price":
            x = next((i for i in st["investments"]["items"] if i["id"] == op[1]), None)
            if x:
                x["price"], x["updated"] = op[2], op[3]
                return f"↩️ Prezzo di «{esc(x.get('name'))}» ripristinato."
        return "Quell'elemento non c'è più (forse l'hai già tolto dalla web app)."

    return store.mutate(fn)


def summary_today():
    st, d = store.read(), today()
    k = iso(d)
    lines = [f"📅 <b>{GIORNI[d.weekday()].capitalize()} {d.day} {MESI[d.month - 1].lower()}</b>"]
    H = st["habits"]["list"]
    if H:
        n = sum(1 for h in H if (h.get("done") or {}).get(k))
        lines += ["", f"✅ <b>Abitudini: {n}/{len(H)}</b>"]
        for h in H:
            s = streak(h)
            lines.append(f"{'✅' if (h.get('done') or {}).get(k) else '⬜️'} {esc(h['name'])}" + (f" · 🔥{s}" if s > 1 else ""))
    items, inc, out = month_totals(st, k[:7])
    todays = [x for x in items if x.get("date") == k and x.get("type") == "out"]
    lines += ["", f"💸 <b>Spese di oggi: {eur(sum(num(x.get('amount')) for x in todays))}</b>"]
    lines += [f"· {esc(x.get('desc') or x.get('category'))} — {eur(num(x.get('amount')))}" for x in todays]
    lines += ["", f"💶 {MESI[d.month - 1]}: uscite {eur(out)} · entrate {eur(inc)} · saldo {eur(inc - out)}"]
    due = todo_lines(due_only=True)
    if due:
        lines += ["", "⏰ <b>Scadenze vicine</b>"] + due
    lines += ["", "<i>Scrivi «da fare» per vedere tutto quello che ti resta.</i>"]
    return "\n".join(lines)


def summary_month():
    st, d = store.read(), today()
    items, inc, out = month_totals(st, iso(d)[:7])
    by = {}
    for x in items:
        if x.get("type") == "out":
            by[x.get("category", "Altro")] = by.get(x.get("category", "Altro"), 0) + num(x.get("amount"))
    lines = [f"💶 <b>{MESI[d.month - 1]} {d.year}</b>", "",
             f"Entrate: <b>{eur(inc)}</b>", f"Uscite: <b>{eur(out)}</b>", f"Saldo: <b>{eur(inc - out)}</b>"]
    if inc:
        lines.append(f"Risparmio: {round((inc - out) / inc * 100)}% delle entrate")
    if by:
        lines += ["", "<b>Uscite per categoria</b>"]
        for c, v in sorted(by.items(), key=lambda kv: -kv[1]):
            lines.append(f"{'▇' * max(1, round(v / out * 10))} {esc(c)} — {eur(v)} ({round(v / out * 100)}%)")
    return "\n".join(lines)


# ---------------------------------------------------------------- assistente: tracker personalizzati
DONE_WORDS = {"fatto", "fatta", "fatti", "completato", "completata", "completo", "done", "chiuso", "chiusa",
              "finito", "finita", "terminato", "concluso", "conclusa", "ok", "pagato", "risolto",
              "letto", "letta", "visto", "vista", "giocato", "consegnato", "provato", "provata", "preso", "presa"}
STATUS_ICONS = ["⚪️", "🟡", "🔵", "🟣", "🟠", "🟤"]
MAX_ITEMS = 25


def tracker_meta(t):
    cols = t.get("columns", [])
    title = next((c for c in cols if c.get("type") == "text"), None)
    status = next((c for c in cols if c.get("type") == "select"
                   and any(norm(o) in DONE_WORDS for o in c.get("options") or [])), None)
    done_val = next((o for o in (status or {}).get("options") or [] if norm(o) in DONE_WORDS), None)
    check = next((c for c in cols if c.get("type") == "checkbox"), None)
    dates = [c for c in cols if c.get("type") == "date"]
    due = next((c for c in dates if re.search(r"scad|entro|consegna|due|deadline", norm(c.get("name", "")))), dates[0] if dates else None)
    return {"title": title, "status": status, "done_val": done_val, "check": check, "due": due}


def row_done(r, m):
    cells = r.get("cells") or {}
    if m["status"] and cells.get(m["status"]["id"]) == m["done_val"]:
        return True
    if m["check"] and not m["status"] and cells.get(m["check"]["id"]) is True:
        return True
    return False


def due_of(r, m):
    if not m["due"]:
        return None
    try:
        return date.fromisoformat(str((r.get("cells") or {}).get(m["due"]["id"]) or ""))
    except ValueError:
        return None


def due_label(d):
    n = (d - today()).days
    if n < 0:
        return f"⚠️ scaduta da {-n} {'giorno' if n == -1 else 'giorni'}"
    if n == 0:
        return "‼️ scade oggi"
    if n == 1:
        return "⏰ scade domani"
    if n <= 7:
        return f"📅 tra {n} giorni ({GIORNI[d.weekday()][:3]} {d.day})"
    return f"📅 {d.day} {MESI[d.month - 1][:3].lower()}" + (f" {d.year}" if d.year != today().year else "")


def row_line(t, r, m):
    cells = r.get("cells") or {}
    name = str(cells.get(m["title"]["id"], "")).strip() if m["title"] else ""
    parts, icon = [f"<b>{esc(name or '(senza nome)')}</b>"], ""
    if m["status"]:
        v = cells.get(m["status"]["id"])
        if v:
            opts = m["status"].get("options") or []
            icon = (STATUS_ICONS[opts.index(v) % len(STATUS_ICONS)] if v in opts else "⚪️") + " "
            parts.append(esc(v))
    d = due_of(r, m)
    if d:
        parts.append(due_label(d))
    for c in t.get("columns", []):  # altri campi utili, brevi
        if c in (m["title"], m["status"], m["due"], m["check"]):
            continue
        v = cells.get(c["id"])
        if v in ("", None, False):
            continue
        if c.get("type") == "checkbox":
            v = "✓"
        elif c.get("type") == "date":
            try:
                dd = date.fromisoformat(str(v))
                v = f"{dd.day} {MESI[dd.month - 1][:3].lower()}"
            except ValueError:
                pass
        sv = str(v).strip()
        if sv:
            parts.append(f"{esc(c.get('name', ''))}: {esc(sv[:40])}")
    return icon + " · ".join(parts)


def open_rows(t):
    m = tracker_meta(t)
    rows = [r for r in t.get("rows", []) if not row_done(r, m)]
    rows.sort(key=lambda r: (due_of(r, m) or date.max, t["rows"].index(r)))
    return m, rows


def find_tracker(q):
    """Trova un tracker dal nome: esatto, inizio del nome, o singolare/plurale (progetto → Progetti)."""
    q = norm(q).strip(" ?!.")
    if len(q) < 3:
        return None
    stem = lambda x: x[:-1] if len(x) >= 5 and x[-1] in "aeio" else x
    trs = [(x, norm(x.get("name", ""))) for x in store.read()["trackers"]]
    for test in (lambda n: n == q, lambda n: n.startswith(q), lambda n: stem(n) == stem(q)):
        found = [x for x, n in trs if n and test(n)]
        if len(found) == 1 or (found and test is not None and found[0] is found[-1]):
            return found[0]
        if len(found) > 1:
            return None
    return None


def tracker_view(tr_id):
    st = store.read()
    t = next((x for x in st["trackers"] if x["id"] == tr_id), None)
    if not t:
        return "Questo progetto non esiste più."
    m, rows = open_rows(t)
    total, done = len(t.get("rows", [])), len(t.get("rows", [])) - len(rows)
    name = esc(t.get("name") or "Senza titolo")
    head = f"{esc(t.get('icon') or '📋')} <b>{name}</b>"
    if not total:
        return f"{head}\n\nÈ vuoto. Aggiungi una voce scrivendo:\n<b>{esc((t.get('name') or 'nome').lower())}: cosa fare</b>"
    if not rows:
        return f"{head}\n\n🎉 Tutto fatto! ({done} {'voce completata' if done == 1 else 'voci completate'})"
    can_done = bool(m["done_val"] or m["check"])
    late = sum(1 for r in rows if (due_of(r, m) or date.max) < today())
    lines = [f"{head} · <b>{len(rows)} da fare</b>" + (f" · {done} {'fatta' if done == 1 else 'fatte'}" if done else "")]
    if late:
        lines.append(f"⚠️ {late} in ritardo")
    lines.append("")
    for i, r in enumerate(rows[:MAX_ITEMS], 1):
        lines.append(f"{i}. {row_line(t, r, m)}")
    if len(rows) > MAX_ITEMS:
        lines.append(f"… e altre {len(rows) - MAX_ITEMS}: le vedi tutte nella web app.")
    kb = None
    if can_done:
        lines += ["", "Tocca un numero per segnarla come fatta:"]
        btns = [{"text": f"✓ {i}", "callback_data": f"r|{t['id']}|{r['id']}"} for i, r in enumerate(rows[:MAX_ITEMS], 1)]
        kb = [btns[i:i + 5] for i in range(0, len(btns), 5)]
    else:
        lines += ["", "<i>Per poterle spuntare da qui, aggiungi al progetto una colonna Casella "
                  "oppure una Selezione con l'opzione «Fatto».</i>"]
    return ("\n".join(lines), kb) if kb else "\n".join(lines)


def trackers_list():
    trs = store.read()["trackers"]
    if not trs:
        return "Non hai ancora progetti: creali dalla web app con il pulsante + o scrivi «crea progetto giochi»."
    lines = ["📋 <b>I tuoi progetti</b>", ""]
    kb = []
    for t in trs:
        _, rows = open_rows(t)
        nm = t.get("name") or "Senza titolo"
        lines.append(f"{esc(t.get('icon') or '📋')} <b>{esc(nm)}</b> — {len(rows)} da fare su {len(t.get('rows', []))}")
        kb.append([{"text": f"{t.get('icon') or '📋'} {nm}"[:60], "callback_data": f"t|{t['id']}"}])
    lines += ["", "Tocca per vedere cosa c'è da fare, oppure scrivi il nome del progetto."]
    return "\n".join(lines), kb


def todo_lines(limit_per=8, due_only=False):
    """Voci aperte di tutti i tracker. Con due_only, solo quelle in ritardo o in scadenza entro 3 giorni."""
    out = []
    for t in store.read()["trackers"]:
        m, rows = open_rows(t)
        if due_only:
            rows = [r for r in rows if due_of(r, m) and (due_of(r, m) - today()).days <= 3]
        if not rows:
            continue
        out.append(f"\n{esc(t.get('icon') or '📋')} <b>{esc(t.get('name') or 'Senza titolo')}</b> ({len(rows)})")
        out += [f"· {row_line(t, r, m)}" for r in rows[:limit_per]]
        if len(rows) > limit_per:
            out.append(f"  … e altre {len(rows) - limit_per} (scrivi «{esc((t.get('name') or '').lower())}»)")
    return out


def todo_all():
    st, k = store.read(), iso(today())
    lines = ["📝 <b>Cosa ti resta da fare</b>"]
    td = [x for x in todos_for(st, today()) if not x.get("done")]
    kb = [[{"text": f"✓ {x['text'][:40]}", "callback_data": f"td|{x['id']}"}] for x in td[:8]]
    if td:
        lines += ["", f"📝 <b>Da fare oggi</b> ({len(td)})"] + [f"⬜️ {esc(x['text'])}" for x in td]
    H = [h for h in st["habits"]["list"] if not (h.get("done") or {}).get(k)]
    if H:
        lines += ["", f"✅ <b>Abitudini di oggi</b> ({len(H)})"] + [f"⬜️ {esc(h['name'])}" for h in H]
    tl = todo_lines()
    lines += tl
    if not H and not tl and not td:
        return "🎉 Non hai niente in sospeso: abitudini di oggi fatte e nessuna voce aperta nei progetti!"
    if H:
        lines += ["", "<i>Scrivi «abitudini» per segnarle con un tocco.</i>"]
    return ("\n".join(lines), kb) if kb else "\n".join(lines)


def mark_row_done(tr_id, row_id):
    def fn(st):
        t = next((x for x in st["trackers"] if x["id"] == tr_id), None)
        r = next((x for x in (t or {}).get("rows", []) if x["id"] == row_id), None)
        if not r:
            return None
        m = tracker_meta(t)
        cells = r.setdefault("cells", {})
        old = {}
        if m["done_val"]:
            old[m["status"]["id"]] = cells.get(m["status"]["id"], "")
            cells[m["status"]["id"]] = m["done_val"]
        if m["check"]:
            old[m["check"]["id"]] = cells.get(m["check"]["id"], False)
            cells[m["check"]["id"]] = True
        name = str(cells.get(m["title"]["id"], "")) if m["title"] else ""
        return name or "voce", old
    return store.mutate(fn)


# ---------------------------------------------------------------- assistente: creare ed eliminare tracker
# (parole chiave, icona, colonna principale, stati, altre colonne)
TEMPLATES = [
    (("gioc", "videogioc", "game"), "🎮", "Titolo", ["Da giocare", "In corso", "Finito"],
     [("Piattaforma", "text"), ("Voto", "number")]),
    (("libr", "lettur", "book"), "📚", "Titolo", ["Da leggere", "In lettura", "Letto"],
     [("Autore", "text"), ("Voto", "number")]),
    (("film", "serie", "tv", "anime", "cinema", "netflix"), "🎬", "Titolo", ["Da vedere", "In visione", "Visto"],
     [("Voto", "number")]),
    (("viagg", "vacanz", "gite", "mete"), "✈️", "Meta", ["Da organizzare", "Prenotato", "Fatto"],
     [("Data", "date"), ("Budget", "number")]),
    (("regal",), "🎁", "Regalo", ["Idea", "Comprato", "Consegnato"],
     [("Per chi", "text"), ("Prezzo", "number")]),
    (("ricett", "cucina", "piatti"), "🍝", "Ricetta", ["Da provare", "Provata"], []),
    (("spesa", "acquist", "comprare", "wishlist", "desideri"), "🛒", "Cosa", None,
     [("Preso", "checkbox"), ("Prezzo", "number")]),
    (("palestra", "allenament", "sport", "corsa"), "🏋️", "Attività", None,
     [("Data", "date"), ("Durata (min)", "number"), ("Fatto", "checkbox")]),
    (("idee", "idea", "appunti", "note"), "💡", "Idea", None, [("Data", "date")]),
]
DEFAULT_TEMPLATE = ("📋", "Nome", ["Da fare", "In corso", "Fatto"], [("Scadenza", "date")])
TYPE_WORDS = {
    "text": ("testo", "text", "scritta"),
    "number": ("numero", "num", "#", "number", "numerico", "cifra", "importo", "euro", "€"),
    "date": ("data", "date", "giorno"),
    "checkbox": ("casella", "checkbox", "spunta", "si/no", "sino", "✓", "check", "flag"),
}
CREATE_RE = re.compile(r"^/?(?:crea(?:mi)?|nuovo|nuova|aggiungi|fai|apri)\s+(?:un\s+|il\s+)?(?:nuovo\s+)?"
                       r"(?:tracker|lista|elenco|tabella|progetto)\s+(?:chiamat[oa]\s+|(?:di|del|della|dello|dei|delle|degli)\s+|per\s+(?:i\s+|le\s+|gli\s+|il\s+|la\s+)?)?(.+)$",
                       re.I | re.S)
DELETE_RE = re.compile(r"^/?(?:elimina|cancella|rimuovi)\s+(?:il\s+)?(?:tracker|lista|elenco|tabella|progetto)\s+(.+)$", re.I | re.S)


def guess_type(name):
    n = norm(name)
    if re.search(r"\b(data|scadenza|uscita|quando|giorno|entro)\b", n):
        return "date"
    if re.search(r"\b(voto|ore|prezzo|costo|budget|pagine|numero|quantit\w*|importo|durata|km|peso|punteggio)\b", n):
        return "number"
    if re.search(r"\b(fatto|finito|letto|visto|preso|completato|comprato|pagato)\b", n):
        return "checkbox"
    return "text"


def parse_column(spec):
    """«voto (numero)», «stato: da fare/fatto», «uscita data», «finito» → colonna."""
    spec = spec.strip(" .;")
    if not spec:
        return None
    name, kind = spec, None
    m = re.match(r"^(.*?)\s*[(:]\s*(.*?)\)?$", spec)
    if m and m.group(2):
        name, kind = m.group(1).strip(), m.group(2).strip()
    else:
        parts = spec.rsplit(" ", 1)
        if len(parts) == 2 and any(norm(parts[1]) in w for w in TYPE_WORDS.values()):
            name, kind = parts[0], parts[1]
    name = (name[:1].upper() + name[1:])[:40]
    if not name:
        return None
    col = {"id": uid(), "name": name, "type": "text"}
    if kind and "/" in kind:
        col["type"] = "select"
        col["options"] = [o.strip()[:1].upper() + o.strip()[1:] for o in kind.split("/") if o.strip()][:12]
    elif kind:
        nk = norm(kind)
        col["type"] = next((t for t, ws in TYPE_WORDS.items() if nk in ws), guess_type(name))
    else:
        col["type"] = guess_type(name)
    return col


def tracker_template(name):
    n = norm(name)
    for keys, icon, title, states, extra in TEMPLATES:
        if any(re.search(r"\b" + re.escape(k), n) for k in keys):
            return icon, title, states, extra
    return DEFAULT_TEMPLATE


TYPE_LABEL = {"text": "testo", "number": "numero", "date": "data", "checkbox": "casella", "select": "selezione"}


def create_tracker(raw, chat):
    raw = raw.strip()
    name, cols_spec = raw, ""
    m = re.match(r"^(.*?)\s*(?::|\bcon\b(?:\s+(?:le\s+)?colonne)?)\s*(.+)$", raw, re.I | re.S)
    if m and m.group(1).strip():
        name, cols_spec = m.group(1), m.group(2)
    name = re.sub(r"\s+", " ", name).strip(" .!?\"'«»")
    if not name:
        raise Reply("Come lo chiamo? Esempio: <b>crea tracker giochi</b>")
    name = (name[:1].upper() + name[1:])[:50]
    existing = next((t for t in store.read()["trackers"] if norm(t.get("name", "")) == norm(name)), None)
    if existing:
        res = tracker_view(existing["id"])
        txt = f"Hai già un progetto «{esc(existing['name'])}». Eccolo:\n\n"
        return (txt + res[0], res[1]) if isinstance(res, tuple) else txt + res

    icon, title, states, extra = tracker_template(name)
    custom = [c for c in (parse_column(x) for x in re.split(r",|;|\be\b|\n", cols_spec)) if c] if cols_spec else []
    columns = [{"id": uid(), "name": title, "type": "text"}]
    if custom:
        if norm(custom[0]["name"]) in ("nome", "titolo") and custom[0]["type"] == "text":
            columns[0]["name"] = custom.pop(0)["name"]
        has_state = any(c["type"] == "select" for c in custom) or any(c["type"] == "checkbox" for c in custom)
        if states and not has_state:
            columns.append({"id": uid(), "name": "Stato", "type": "select", "options": list(states)})
        columns += custom
    else:
        if states:
            columns.append({"id": uid(), "name": "Stato", "type": "select", "options": list(states)})
        columns += [{"id": uid(), "name": n, "type": t} for n, t in extra]
    t = {"id": uid(), "icon": icon, "name": name, "columns": columns, "rows": []}
    store.mutate(lambda st: st["trackers"].append(t))
    push_undo(chat, ("trnew", t["id"]))

    def col_desc(c):
        d = TYPE_LABEL[c["type"]]
        if c["type"] == "select":
            d += ": " + " / ".join(c["options"])
        return f"· <b>{esc(c['name'])}</b> ({esc(d)})"

    ex = norm(name).split()[0]
    return "\n".join([
        f"{icon} Progetto <b>{esc(name)}</b> creato!", "",
        "Colonne:", *[col_desc(c) for c in columns], "",
        f"➕ Aggiungi voci scrivendo: <b>{esc(ex)}: …</b>",
        f"👀 Per vedere cosa c'è da fare: <b>{esc(ex)}</b>",
        "🖥 Colonne e dettagli si modificano dalla web app.",
        "<i>Scrivi «annulla» se ho sbagliato.</i>",
    ])


def delete_tracker_ask(q):
    t = find_tracker(q)
    if not t:
        names = ", ".join(esc(x.get("name") or "Senza titolo") for x in store.read()["trackers"]) or "nessuno"
        return f"Non trovo un progetto «{esc(q.strip())}». I tuoi progetti: {names}"
    n = len(t.get("rows", []))
    return (f"🗑 Vuoi eliminare il progetto <b>{esc(t.get('name'))}</b> con {n} {'voce' if n == 1 else 'voci'}?",
            [[{"text": "🗑 Sì, elimina", "callback_data": f"x|{t['id']}"}, {"text": "Annulla", "callback_data": "x|no"}]])


def delete_tracker(tr_id, chat):
    def fn(st):
        i = next((i for i, x in enumerate(st["trackers"]) if x["id"] == tr_id), None)
        if i is None:
            return None
        t = st["trackers"].pop(i)
        if st.get("active") == tr_id:
            st["active"] = st["trackers"][0]["id"] if st["trackers"] else None
        trash_put(st, "tracker", t, t.get("name") or "Senza titolo", f"Progetto · {len(t.get('rows', []))} voci", {"i": i})
        return t, i
    r = store.mutate(fn)
    if not r:
        return "Questo progetto non esiste più."
    push_undo(chat, ("trdel", json.loads(json.dumps(r[0])), r[1]))
    return f"🗑 Progetto <b>{esc(r[0].get('name'))}</b> spostato nel cestino.\n<i>Scrivi «annulla» o ripristinalo dal Cestino entro 30 giorni.</i>"

# ---------------------------------------------------------------- assistente: spese condivise
ME = "me"


def cents(x):
    return round(x + 1e-9, 2)


def sh_share(x, pid):
    split = x.get("split") or []
    if pid not in split:
        return 0.0
    w = x.get("weights")
    if not w:
        return num(x.get("amount")) / len(split)
    tot = sum(num(w.get(i)) for i in split)
    return num(x.get("amount")) * num(w.get(pid)) / tot if tot else 0.0


def parse_quota(tok):
    t = tok.strip().replace(",", ".").lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", t)
    if m and float(m.group(2)):
        return "frac", float(m.group(1)) / float(m.group(2))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", t)
    if m:
        return "frac", float(m.group(1)) / 100
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(€|eur|euro)", t)
    if m:
        return "eur", float(m.group(1))
    m = re.fullmatch(r"\d+(?:\.\d+)?", t)
    if m:
        return "part", float(t)
    return None


def compute_weights(amount, ids, quotas):
    """quotas: id → (tipo, valore). Chi non ha quota si divide il resto. Ritorna (weights|None, errore|None)."""
    if not quotas:
        return None, None
    kinds = {k for k, _ in quotas.values()}
    if len(kinds) > 1:
        return None, "Usa lo stesso tipo di quota per tutti (es. 1/3 e 2/3, oppure 40% e 60%, oppure 30€ e 60€)."
    kind = kinds.pop()
    w = {i: v for i, (_, v) in quotas.items()}
    free = [i for i in ids if i not in w]
    if kind == "part":
        w.update({i: 1.0 for i in free})
        return w, None
    total = amount if kind == "eur" else 1.0
    rest = total - sum(w.values())
    if rest < -(0.005 if kind == "eur" else 0.0001):
        return None, ("Gli importi superano il totale." if kind == "eur" else "Le quote superano il 100%.")
    if free:
        w.update({i: max(0.0, rest) / len(free) for i in free})
    elif abs(rest) > (0.01 if kind == "eur" else 0.001):
        return None, (f"Mancano {eur(rest)} per arrivare al totale." if kind == "eur"
                      else f"Le quote arrivano solo al {round((1 - rest) * 100)}%.")
    return w, None


def sh_balances(st):
    """persona → quanto ti deve (+) o quanto le devi (-)."""
    S = st["expenses"]["shared"]
    b = {p["id"]: 0.0 for p in S["people"]}
    for x in S["items"]:
        split = x.get("split") or []
        if not split:
            continue
        for pid in split:
            if pid == x.get("payer"):
                continue
            q = sh_share(x, pid)
            if x.get("payer") == ME:
                b[pid] = b.get(pid, 0) + q
            elif pid == ME:
                b[x["payer"]] = b.get(x["payer"], 0) - q
    for z in S["settlements"]:
        if z.get("to") == ME:
            b[z["from"]] = b.get(z["from"], 0) - num(z.get("amount"))
        if z.get("from") == ME:
            b[z["to"]] = b.get(z["to"], 0) + num(z.get("amount"))
    return {k: cents(v) for k, v in b.items()}


def sh_name(st, pid):
    if pid == ME:
        return st["expenses"]["shared"].get("myName") or "Tu"
    return next((p["name"] for p in st["expenses"]["shared"]["people"] if p["id"] == pid), "?")


def find_people(st, names, create=True):
    """Ritorna (ids, nuovi_nomi). Cerca per nome (anche solo l'inizio), crea chi non esiste."""
    people = st["expenses"]["shared"]["people"]
    ids, new = [], []
    for raw in names:
        n = norm(raw).strip(" .,!?")
        if not n or n in ("me", "io", "te", "tu", "mia", "mio"):
            continue
        cand = [p for p in people if norm(p["name"]) == n] or \
               [p for p in people if len(n) >= 3 and norm(p["name"]).startswith(n)]
        if cand:
            p = sorted(cand, key=lambda p: bool(p.get("archived")))[0]
            p["archived"] = False
        elif create:
            p = {"id": uid(), "name": raw.strip(" .,!?")[:1].upper() + raw.strip(" .,!?")[1:]}
            people.append(p)
            new.append(p["name"])
        else:
            continue
        if p["id"] not in ids:
            ids.append(p["id"])
    return ids, new


def split_names(txt):
    return [x for x in re.split(r",|\s+e\s+|\s+ed\s+|\s+&\s+", txt.strip()) if x.strip()]


def shared_add(text, chat, payer_name=None):
    t = text.strip()
    m = re.search(r"\bpagat[oa]\s+da\s+(\S+)", t, re.I)
    if m:
        payer_name = m.group(1)
        t = (t[:m.start()] + t[m.end():]).strip()
    with_names, quota_tokens = [], {}
    m = re.search(r"\bcon\s+(.+)$", t, re.I)
    if m:
        with_names = split_names(m.group(1))
        t = t[:m.start()].strip()
    else:  # "condivisa 90 cena tony 1/3 io 2/3": nomi con quota anche senza "con"
        pairs = re.findall(r"(?:^|\s)([^\s\d/%€]+)\s+(\d+(?:[.,]\d+)?\s*(?:/\s*\d+|%|€))(?=\s|$)", t)
        if pairs:
            for nm, q in pairs:
                with_names.append(f"{nm} {q}")
            t = re.sub(r"(?:^|\s)([^\s\d/%€]+)\s+(\d+(?:[.,]\d+)?\s*(?:/\s*\d+|%|€))(?=\s|$)", " ", t).strip()
    clean = []
    for entry in with_names:  # "tony 1/3", "2/3 denis", "io 60%"
        toks = entry.split()
        qt = [x for x in toks if parse_quota(x)]
        nm = " ".join(x for x in toks if not parse_quota(x))
        if nm:
            clean.append(nm)
            if qt:
                quota_tokens[norm(nm)] = parse_quota(qt[0])
    with_names = clean
    d, words = take_date(t.split())
    amt, desc = None, []
    for w in words:
        a = parse_amount(w) if amt is None else None
        if a is not None:
            amt = a
        elif norm(w) not in ("euro", "eur", "€", "di", "per"):
            desc.append(w)
    if not amt:
        raise Reply("Manca l'importo 🤔\nEsempio: <b>condivisa 60 cena con marco e giulia</b>")
    desc_s = " ".join(desc).strip()
    desc_s = desc_s[:1].upper() + desc_s[1:]

    def fn(st):
        S, E = st["expenses"]["shared"], st["expenses"]
        new = []
        payer = ME
        if payer_name and norm(payer_name) not in ("me", "io"):
            ids, n = find_people(st, [payer_name])
            payer, new = ids[0], new + n
        me_names = {"io", "me", "mia", "mio", "tu"} | ({norm(S["myName"])} if S.get("myName") else set())
        others = [n for n in with_names if norm(n) not in me_names]
        ids, n = find_people(st, others)
        new += n
        if not with_names:
            ids = [p["id"] for p in S["people"] if not p.get("archived")] if payer == ME else []
        split = [ME] + [i for i in ids if i != ME]
        if payer != ME and payer not in split:
            split.append(payer)
        if len(split) < 2:
            return None
        quotas = {}
        for nm, q in quota_tokens.items():
            if nm in me_names:
                quotas[ME] = q
            else:
                pid = find_people(st, [nm], create=False)[0]
                if pid:
                    quotas[pid[0]] = q
        weights, err = compute_weights(amt, split, quotas)
        if err:
            return {"error": err}
        x = {"id": uid(), "date": iso(d), "desc": desc_s, "amount": cents(amt), "payer": payer, "split": split}
        if weights:
            x["weights"] = weights
        quota = cents(sh_share(x, ME))
        cat, newcat, _ml = guess_category(st, desc_s, None, "out")
        if newcat:
            E["categories"].append(cat)
        e = {"id": uid(), "date": x["date"], "desc": (desc_s or "Spesa condivisa") + " (condivisa)",
             "category": cat, "type": "out", "amount": quota, "shared": x["id"]}
        E["items"].append(e)
        x["linked"] = e["id"]
        S["items"].append(x)
        bal = sh_balances(st)
        others = [i for i in split if i != ME]
        return x, quota, cat, new, [(sh_name(st, i), bal.get(i, 0)) for i in others], sh_name(st, payer)

    r = store.mutate(fn)
    if isinstance(r, dict):
        raise Reply("⚠️ " + r["error"] + "\nEsempio: <b>condivisa 90 cena con tony 1/3</b> (il resto è tuo)")
    if not r:
        raise Reply("Con chi la dividi? Esempio: <b>condivisa 60 cena con marco e giulia</b>\n"
                    "(oppure aggiungi prima le persone nella web app, sezione Spese → Condivise)")
    x, quota, cat, new, bals, payer = r
    push_undo(chat, ("shared", x["id"], x.get("linked")))
    st_now = store.read()
    split_txt = ", ".join(f"{esc(sh_name(st_now, i))} {eur(sh_share(x, i))}" for i in x["split"])
    lines = [f"👥 Spesa condivisa: <b>{eur(x['amount'])}</b>" + (f" · {esc(x['desc'])}" if x["desc"] else ""),
             ("Hai pagato <b>tu</b>" if x["payer"] == ME else f"Ha pagato <b>{esc(payer)}</b>") + f" · la tua quota: <b>{eur(quota)}</b>",
             ("⚖️ " if x.get("weights") else "➗ ") + split_txt,
             f"🏷 La tua quota è nelle spese personali ({esc(cat)}) · 📅 {day_label(date.fromisoformat(x['date']))}"]
    if new:
        lines.append(f"🆕 Persone aggiunte: {esc(', '.join(new))}")
    lines.append("")
    for n, v in bals:
        lines.append(f"· {esc(n)}: " + ("siete pari" if abs(v) < 0.005 else (f"ti deve {eur(v)}" if v > 0 else f"gli devi {eur(-v)}")))
    lines.append("<i>Scrivi «annulla» per annullarla.</i>")
    return "\n".join(lines)


def shared_summary():
    st = store.read()
    S = st["expenses"]["shared"]
    if not S["people"]:
        return ("👥 Non hai ancora persone con cui dividere le spese.\n"
                "Scrivi ad esempio <b>condivisa 60 cena con marco e giulia</b>: le persone vengono aggiunte da sole.")
    bal = sh_balances(st)
    owed = sum(v for v in bal.values() if v > 0)
    owe = -sum(v for v in bal.values() if v < 0)
    lines = ["👥 <b>Spese condivise</b>", ""]
    kb = []
    for p in S["people"]:
        v = bal.get(p["id"], 0)
        if p.get("archived") and abs(v) < 0.005:
            continue
        if abs(v) < 0.005:
            lines.append(f"⚪️ {esc(p['name'])}: siete pari")
        elif v > 0:
            lines.append(f"🟢 {esc(p['name'])} <b>ti deve {eur(v)}</b>")
        else:
            lines.append(f"🔴 <b>Devi {eur(-v)}</b> a {esc(p['name'])}")
        if abs(v) >= 0.005:
            kb.append([{"text": f"🤝 Salda con {p['name']} ({eur(abs(v))})"[:60], "callback_data": f"z|{p['id']}"}])
    lines += ["", f"Ti devono: <b>{eur(owed)}</b> · Devi: <b>{eur(owe)}</b>"]
    last = [x for _, x in sorted(enumerate(S["items"]), key=lambda ix: (ix[1].get("date", ""), ix[0]), reverse=True)][:5]
    if last:
        lines += ["", "<b>Ultime spese</b>"]
        lines += [f"· {esc(x.get('desc') or 'Spesa')} — {eur(num(x.get('amount')))} ({'hai pagato tu' if x['payer'] == ME else 'ha pagato ' + esc(sh_name(st, x['payer']))})" for x in last]
    return ("\n".join(lines), kb) if kb else "\n".join(lines)


def shared_settle(pid, amount, chat, direction=None):
    """direction: "in" = la persona ti ha dato soldi, "out" = tu li hai dati a lei, None = in base al saldo."""
    def fn(st):
        bal = sh_balances(st).get(pid)
        if bal is None:
            return None
        a = cents(abs(amount)) if amount else abs(bal)
        if not a:
            return sh_name(st, pid), 0, None, 0
        incoming = (bal > 0) if direction is None else (direction == "in")
        z = {"id": uid(), "date": iso(today()), "amount": a,
             "from": pid if incoming else ME, "to": ME if incoming else pid}
        st["expenses"]["shared"]["settlements"].append(z)
        return sh_name(st, pid), a, z, sh_balances(st).get(pid, 0)
    r = store.mutate(fn)
    if not r:
        return "Persona non trovata."
    name, a, z, left = r
    if not z:
        return f"Con {esc(name)} siete già pari 👌"
    push_undo(chat, ("settle", z["id"]))
    who = f"{esc(name)} ti ha dato" if z["to"] == ME else f"Hai dato a {esc(name)}"
    rest = "ora siete pari ✅" if abs(left) < 0.005 else (f"ti deve ancora {eur(left)}" if left > 0 else f"gli devi ancora {eur(-left)}")
    return f"🤝 {who} <b>{eur(a)}</b>: {rest}"


def shared_settle_cmd(text, chat, direction=None):
    words = text.split()
    amt = next((parse_amount(w) for w in words if parse_amount(w) is not None), None)
    names = [w for w in words if parse_amount(w) is None and norm(w) not in ("con", "a", "da", "mi", "ha", "dato", "ho", "€", "euro")]
    st = store.read()
    ids, _ = find_people(json.loads(json.dumps(st)), names, create=False)
    if len(ids) != 1:
        ppl = ", ".join(esc(p["name"]) for p in st["expenses"]["shared"]["people"] if not p.get("archived")) or "nessuna"
        return f"Con chi? Esempio: <b>saldato marco</b> oppure <b>saldato marco 20</b>\nPersone: {ppl}"
    return shared_settle(ids[0], amt, chat, direction)


# ---------------------------------------------------------------- assistente: cancellare spese
DEL_EXP_RE = re.compile(r"^/?(?:cancella|elimina|rimuovi|togli|annulla)(?:mi)?\s+(?:la\s+|le\s+|l'|lo\s+|il\s+)?"
                        r"(ultima\s+|ultime\s+\d+\s+)?(spesa|spese|entrata|entrate|uscita|uscite|movimento)?\s*(.*)$", re.I | re.S)


def exp_label(x):
    d = date.fromisoformat(x["date"])
    return f"{esc(x.get('desc') or x.get('category'))} · {eur(num(x['amount']))} · {day_label(d)}"


def find_expenses(text, typ_hint):
    """Spese che corrispondono a testo/importo/data (le più recenti prima)."""
    words = text.split()
    d = None
    if any(norm(w) in ("ieri", "altroieri", "oggi") or re.fullmatch(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", w) for w in words):
        d, words = take_date(words)
    amt = None
    rest = []
    for w in words:
        a = parse_amount(w)
        if a is not None and amt is None:
            amt = a
        elif norm(w) not in FILLER | {"da", "a", "del", "della", "di", "il", "la", "lo", "fatta", "fatto", "pagato"}:
            rest.append(norm(w))
    items = store.read()["expenses"]["items"]
    order = {x["id"]: i for i, x in enumerate(items)}
    res = []
    for x in items:
        if typ_hint and x.get("type") != typ_hint:
            continue
        if d and x.get("date") != iso(d):
            continue
        if amt is not None and abs(num(x["amount"]) - amt) > 0.005:
            continue
        hay = norm(f"{x.get('desc', '')} {x.get('category', '')}")
        if rest and not all(w in hay or (len(w) >= 4 and brain.fuzzy(w, hay.split(), 0.8)) for w in rest):
            continue
        res.append(x)
    res.sort(key=lambda x: (x.get("date", ""), order[x["id"]]), reverse=True)
    return res


def delete_expense_ask(m):
    last, kind, text = (m.group(1) or "").strip(), norm(m.group(2) or ""), (m.group(3) or "").strip(" ?!.")
    typ = "in" if kind.startswith("entrat") else "out" if kind.startswith(("spes", "uscit")) else None
    if last and not text:  # "cancella l'ultima spesa": l'ultima registrata
        items = [x for x in store.read()["expenses"]["items"] if not typ or x.get("type") == typ]
        found = items[-1:]
    else:
        found = find_expenses(text, typ)
    if not found:
        return ("Non trovo spese che corrispondono 🤔\nProva con <b>cancella ultima spesa</b>, <b>cancella pizza</b> "
                "o <b>cancella spesa di ieri da 12</b>.")
    if len(found) == 1:
        x = found[0]
        note = "\n<i>È la tua quota di una spesa condivisa: la spesa condivisa resta (eliminala da Spese → Condivise).</i>" if x.get("shared") else ""
        return (f"🗑 Vuoi eliminare questa {'entrata' if x.get('type') == 'in' else 'spesa'}?\n\n<b>{exp_label(x)}</b>"
                f"{(' · ' + esc(x['category'])) if x.get('desc') else ''}{note}",
                [[{"text": "🗑 Sì, elimina", "callback_data": f"dx|{x['id']}"}, {"text": "No", "callback_data": "dn"}]])
    top = found[:6]
    more = f"\n<i>…e altre {len(found) - 6}: aggiungi l'importo o la data per restringere.</i>" if len(found) > 6 else ""
    return (f"Ho trovato {len(found)} movimenti. Quale elimino?{more}",
            [[{"text": f"🗑 {re.sub(r'<[^>]+>', '', exp_label(x))}"[:60], "callback_data": f"dx|{x['id']}"}] for x in top]
            + [[{"text": "Nessuno", "callback_data": "dn"}]])


def delete_expense(item_id, chat):
    def fn(st):
        items = st["expenses"]["items"]
        i = next((i for i, x in enumerate(items) if x["id"] == item_id), None)
        if i is None:
            return None
        x = items.pop(i)
        trash_put(st, "expense", x, f"{x.get('desc') or x.get('category')} · {eur(num(x.get('amount')))}", f"Spesa · {x.get('category', '')} · {x.get('date', '')}", {"i": i})
        return x, i
    r = store.mutate(fn)
    if not r:
        return None
    push_undo(chat, ("restore_expense", r[0], r[1]))
    return r[0]


# ---------------------------------------------------------------- assistente: viaggi e meteo
def trip_when(t, d=None):
    d = d or today()
    s, e = date.fromisoformat(t["start"]), date.fromisoformat(t.get("end") or t["start"])
    if s <= d <= e:
        return "in corso", 0
    if d < s:
        n = (s - d).days
        return ("domani" if n == 1 else f"tra {n} giorni"), n
    return "concluso", -1


def trips_text():
    trips = [t for t in store.read().get("trips", []) if t.get("start")]
    nxt = sorted([t for t in trips if trip_when(t)[1] >= 0], key=lambda t: t["start"])
    if not trips:
        return "✈️ Non hai ancora viaggi. Creane uno dalla web app: sezione <b>✈️ Viaggi</b>."
    if not nxt:
        return "✈️ Nessun viaggio in programma. Li trovi tutti nella web app, sezione <b>✈️ Viaggi</b>."
    lines = ["✈️ <b>I tuoi prossimi viaggi</b>", ""]
    for t in nxt[:5]:
        when, n = trip_when(t)
        s = date.fromisoformat(t["start"])
        line = f"<b>{esc(t.get('name') or t['place']['name'])}</b> · {s.day} {MESI[s.month - 1][:3].lower()} · {when}"
        if 0 <= n <= 15:
            try:
                w = TRAVEL.weather(t["place"]["lat"], t["place"]["lon"], t["start"], t.get("end"))
                if w["days"]:
                    d0 = w["days"][0]
                    line += f"\n   {d0['icon']} {travel.fmt_t(d0['min'])} / {travel.fmt_t(d0['max'])} il primo giorno"
            except ValueError:
                pass
        todo = [c for c in t.get("checklist", []) if not c.get("done")]
        if todo and 0 <= n <= 7:
            line += f"\n   🧳 {len(todo)} cose ancora da preparare"
        lines.append(line)
    lines += ["", "Scrivi <b>meteo " + esc(nxt[0]["place"]["name"].lower()) + "</b> per le previsioni."]
    return "\n".join(lines)


def weather_cmd(q):
    q = q.strip(" ?!.")
    st = store.read()
    trip = None
    if not q or norm(q) in ("viaggio", "del viaggio", "prossimo viaggio"):
        nxt = sorted([t for t in st.get("trips", []) if t.get("start") and trip_when(t)[1] >= 0], key=lambda t: t["start"])
        if not nxt:
            return "Di quale città? Esempio: <b>meteo lisbona</b>"
        trip = nxt[0]
        place = trip["place"]
    else:
        q = re.sub(r"^(a|ad|in|di|per|su)\s+", "", q, flags=re.I)
        trip = next((t for t in st.get("trips", []) if norm(t["place"]["name"]) == norm(q) or norm(t.get("name", "")) == norm(q)), None)
        if trip:
            place = trip["place"]
        else:
            try:
                res = TRAVEL.geocode(q, 1)
            except ValueError as e:
                return f"⚠️ {esc(str(e))}"
            if not res:
                return f"Non trovo «{esc(q)}» 🤔 Prova con il nome della città."
            place = res[0]
    try:
        if trip and trip_when(trip)[1] >= 0:
            w = TRAVEL.weather(place["lat"], place["lon"], trip["start"], trip.get("end"))
            title = f"{place['name']} · viaggio {trip_when(trip)[0]}"
        else:
            w = TRAVEL.weather(place["lat"], place["lon"])
            w["days"] = w["days"][:5]
            title = place["name"] + (f", {place['country']}" if place.get("country") else "")
    except ValueError as e:
        return f"⚠️ {esc(str(e))}"
    return travel.weather_text(title, w, esc)


COMMAND_WORDS = ["meteo", "viaggi", "aiuto", "oggi", "mese", "annulla", "abitudini", "fatto", "spesa", "entrata", "prezzo", "condivisa",
                 "conti", "saldato", "tracker", "analisi", "riepilogo", "debiti"]


def process(text, chat, _retry=False):
    t = text.strip()
    if not t:
        return HELP
    m = re.match(r"^([+-])\s*(\d.*)$", t)
    if m:
        return add_expense(m.group(2).split(), "in" if m.group(1) == "+" else "out", chat)
    if re.fullmatch(r"/?(crea|nuovo|nuova)( un| il)?( nuovo)? (tracker|lista|elenco|tabella|progetto)", norm(t)):
        return "Come lo chiamo? Esempio: <b>crea tracker giochi</b>\nPuoi anche indicare le colonne: <b>crea tracker giochi: piattaforma, voto (numero)</b>"
    m = CREATE_RE.match(t)
    if m:
        return create_tracker(m.group(1), chat)
    m = DELETE_RE.match(t)
    if m:
        return delete_tracker_ask(m.group(1))
    m = re.match(r"^/?(?:cancella|elimina|rimuovi|togli|annulla|sposta)\s+(?:l'|il\s+|lo\s+|un\s+)?(?:appuntamento|evento|impegno)\s*(?:di\s+|del\s+|con\s+)?(.*)$", t, re.I)
    if m:
        return delete_event_ask(m.group(1).strip())
    m = DEL_EXP_RE.match(t)
    if m and (m.group(1) or m.group(2) or (m.group(3) or "").strip()) and not re.match(r"^/?annulla\s*$", t, re.I):
        return delete_expense_ask(m)
    m = TODO_RE.match(t)
    if m:
        r = todo_add(m.group(1), m.group(2), chat)
        if r:
            return r
    m = re.match(r"^/?(?:caro\s+)?diario\s*[:,]\s*(.+)$", t, re.I | re.S)
    if m:
        return journal_add(m.group(1), chat)
    if re.fullmatch(r"/?diario", norm(t)):
        return journal_show()
    if not brain.is_question(t) and agenda.looks_like_appointment(t):
        r = appointment_add(t, chat)
        if r:
            return r
    words = t.split()
    cmd = norm(words[0]).lstrip("/").split("@")[0]
    rest = words[1:]
    if cmd in ("start", "aiuto", "help", "comandi"):
        return HELP
    if cmd in ("oggi", "riepilogo"):
        return summary_today()
    if cmd in ("mese", "mensile"):
        return summary_month()
    if cmd == "annulla":
        return undo(chat)
    if cmd == "abitudini" or (cmd in ("fatto", "fatta", "done", "✅") and not rest):
        return habits_menu(take_date(rest)[0])
    if cmd in ("fatto", "fatta", "done", "✅"):
        return habit_done(rest, chat)
    if cmd in ("spesa", "speso", "uscita", "pagato", "s"):
        return add_expense(rest, "out", chat)
    if cmd in ("entrata", "incasso", "ricevuto", "guadagno", "e"):
        return add_expense(rest, "in", chat)
    if cmd == "prezzo":
        return update_price(rest, chat)
    if cmd in ("condivisa", "condiviso", "condivise", "dividi", "diviso", "divisa", "split") and rest:
        return shared_add(" ".join(rest), chat)
    if cmd in ("conti", "condivise", "debiti", "crediti", "condivisi") or norm(t).strip(" ?") in ("chi mi deve", "chi mi deve soldi", "a chi devo", "a chi devo soldi"):
        return shared_summary()
    if cmd in ("saldato", "saldata", "saldo", "salda", "pareggiato", "pari") and rest:
        return shared_settle_cmd(" ".join(rest), chat)
    m = re.match(r"^(\S+)\s+ha\s+pagato\s+(.+)$", t, re.I)
    if m and norm(m.group(1)) not in ("chi",):
        return shared_add(m.group(2), chat, payer_name=m.group(1))
    m = re.match(r"^(\S+)\s+mi\s+ha\s+(?:dato|restituito|ridato|pagato)\s+(.+)$", t, re.I)
    if m:
        return shared_settle_cmd(m.group(1) + " " + m.group(2), chat, "in")
    m = re.match(r"^ho\s+(?:dato|restituito|ridato|pagato)\s+(.+?)\s+a\s+(\S+)$", t, re.I)
    if m:
        return shared_settle_cmd(m.group(2) + " " + m.group(1), chat, "out")
    nt = re.sub(r"^/", "", norm(t)).split("@")[0].strip(" ?!.")
    if cmd == "meteo" or nt.startswith("che tempo fa"):
        return weather_cmd(re.sub(r"^(che tempo fa|meteo)\s*", "", nt))
    if nt in ("viaggi", "viaggio", "prossimo viaggio", "prossimi viaggi"):
        return trips_text()
    if nt in ("agenda", "calendario", "appuntamenti", "impegni", "prossimi appuntamenti", "i miei appuntamenti", "cosa ho in programma",
              "programma", "cosa ho questa settimana", "che impegni ho", "settimana"):
        return agenda_next(7)
    if re.match(r"^(che\s+)?(cosa|che)\s+(e|c'?e stato|e stato)\s+success|^com.?e\s+andat[ao]|^cosa\s+ho\s+fatto|^riepilogo\s+(di|del|dell)", nt):
        d, _ = agenda.find_day(nt, today(), future=False)
        return agenda_day(d or today(), past=True)
    if re.match(r"^(che\s+)?(cosa|che)\s+(ho|c'?e|devo fare|ho in programma|ho in agenda)|^impegni|^appuntamenti|^agenda", nt):
        d, _ = agenda.find_day(nt, today())
        if d:
            return agenda_day(d)
    if nt in ("analisi", "insight", "insights", "consigli", "previsione", "previsioni", "statistiche", "come sto andando"):
        return brain.insights_text(store.read())
    if brain.is_question(t):
        r = brain.answer_question(t, store.read())
        if r:
            if r[1]:
                CHAT_EXTRA.set({"chart": r[1]})
            return r[0]
    m = re.match(r"^(?:da fare|cosa devo fare|cose da fare|todo)\s+(oggi|domani|dopodomani|lunedi|martedi|mercoledi|giovedi|venerdi|sabato|domenica|\d{1,2}/\d{1,2})$", nt)
    if m:
        d, _ = agenda.find_day(m.group(1), today())
        return todos_text(d)
    if nt in ("da fare", "dafare", "todo", "cose da fare", "cosa devo fare", "cosa mi resta", "cosa resta da fare", "cosa devo fare oggi"):
        return todo_all()
    if nt in ("tracker", "trackers", "liste", "elenchi", "progetti?") and not find_tracker(nt):
        return trackers_list()
    if ":" in t:
        head, body = t.split(":", 1)
        nh, body = norm(head), body.strip()
        if nh and body:
            for tr in store.read()["trackers"]:
                nn = norm(tr.get("name", ""))
                if nn and (nn == nh or (len(nh) >= 3 and nn.startswith(nh))):
                    return tracker_add(tr["id"], body, chat)
    q = re.sub(r"^(mostra|lista|elenco|vedi|apri|cosa c'?e in|cosa c'?e da fare in)\s+", "", norm(t))
    tr = find_tracker(q)
    if tr:
        return tracker_view(tr["id"])
    if any(parse_amount(w) is not None for w in words):
        fw = norm(words[0]).lstrip("/")
        g = brain.fuzzy(fw, ["spesa", "entrata", "uscita", "incasso"], 0.8) if len(fw) > 3 and parse_amount(words[0]) is None else None
        if g and g != fw and rest:  # "spessa 15 sushi" → spesa
            res = add_expense(rest, "in" if g in ("entrata", "incasso") else "out", chat)
            txt, kb = res if isinstance(res, tuple) else (res, None)
            return f"<i>✏️ Ho capito «{g}»</i>\n" + txt, kb
        return add_expense(words, "out", chat, assumed=True)
    if not _retry:  # forse un refuso? ("abitudnii", "progeti", "spessa 12 pizza")
        names = [x.get("name", "") for x in store.read()["trackers"] if x.get("name")]
        first = norm(words[0]).lstrip("/")
        guess = brain.fuzzy(first, COMMAND_WORDS + names, 0.75) or (brain.fuzzy(norm(t), names, 0.75) if len(words) <= 3 else None)
        if guess and norm(guess) != first:
            fixed = guess if guess in names and brain.fuzzy(norm(t), names, 0.75) == guess else " ".join([guess] + rest)
            try:
                res = process(fixed, chat, _retry=True)
            except Reply as r:
                res = (r.text, r.kb)
            txt, kb = res if isinstance(res, tuple) else (res, None)
            if not txt.startswith("Non ho capito"):
                txt = f"<i>✏️ Ho capito «{esc(fixed)}»</i>\n" + txt
                return (txt, kb) if kb else txt
    return "Non ho capito 🤔\n\n" + HELP



# ---------------------------------------------------------------- calendario
REMIND_LABEL = {"": "nessun avviso", "0": "ti avviso all'ora esatta", "15": "ti avviso 15 minuti prima", "60": "ti avviso 1 ora prima",
                "1440": "ti avviso il giorno prima", "morning": "lo trovi nel riepilogo del mattino"}


def day_name(d):
    t = today()
    if d == t:
        return "oggi"
    if d == t + timedelta(days=1):
        return "domani"
    if d == t - timedelta(days=1):
        return "ieri"
    return f"{GIORNI[d.weekday()]} {d.day} {MESI[d.month - 1].lower()}" + (f" {d.year}" if d.year != t.year else "")


def event_line(ev, d=None):
    when = "tutto il giorno" if ev.get("allDay") or not ev.get("start") else esc(str(ev["start"])) + (f"–{esc(str(ev['end']))}" if ev.get("end") else "")
    extra = [esc(ev["place"])] if ev.get("place") else []
    if ev.get("repeat"):
        extra.append(agenda.REPEAT_LABEL.get(ev["repeat"], ""))
    return f"🕒 <b>{when}</b> {esc(ev.get('title') or 'Appuntamento')}" + (f" <i>· {' · '.join(extra)}</i>" if extra else "")


def events_on(st, d):
    return [ev for ev, _ in agenda.expand(st.get("calendar", {}).get("events", []), d, d, span=True)]


def appointment_add(text, chat):
    ev = agenda.parse_appointment(text, datetime.now())
    if not ev:
        return None
    ev["id"] = uid()
    ev["created"] = iso(today())

    def fn(st):
        st.setdefault("calendar", {}).setdefault("events", []).append(ev)
        return [x for x in events_on(st, date.fromisoformat(ev["date"])) if x["id"] != ev["id"]]
    others = store.mutate(fn)
    push_undo(chat, ("event", ev["id"]))
    d = date.fromisoformat(ev["date"])
    lines = [f"📅 Appuntamento salvato: <b>{esc(ev['title'])}</b>",
             f"🗓 {day_name(d)}" + ("" if ev["allDay"] else f" · {ev['start']}–{ev['end']}") + (f" · {agenda.REPEAT_LABEL[ev['repeat']]}" if ev["repeat"] else ""),
             f"🔔 {REMIND_LABEL.get(ev['remind'], '')}"]
    if others:
        lines += ["", f"Quel giorno hai anche:"] + [event_line(x) for x in others[:5]]
    lines.append("\n<i>Ora, luogo e avviso li cambi dal Calendario nella web app.</i>")
    return "\n".join(lines), [[{"text": "🗑 Annulla", "callback_data": f"ax|{ev['id']}"},
                               {"text": "🔕 Nessun avviso", "callback_data": f"an|{ev['id']}"}]]


def happened_lines(st, d):
    """Cosa è successo un giorno: spese, allenamenti, abitudini, viaggi, voci fatte."""
    k, out = iso(d), []
    exp = [x for x in st["expenses"]["items"] if x.get("date") == k]
    if exp:
        o = sum(num(x["amount"]) for x in exp if x.get("type") == "out")
        i = sum(num(x["amount"]) for x in exp if x.get("type") == "in")
        out.append("💸 <b>Spese</b>" + (f" · uscite {eur(o)}" if o else "") + (f" · entrate {eur(i)}" if i else ""))
        out += [f"   · {esc(x.get('desc') or x.get('category'))} — {'+' if x.get('type') == 'in' else '−'}{eur(num(x['amount']))}" for x in exp[:8]]
    sh = [x for x in st["expenses"]["shared"]["items"] if x.get("date") == k and not x.get("linked")]
    out += [f"👥 {esc(x.get('desc') or 'Spesa condivisa')} — {eur(num(x['amount']))} (pagata da {esc(sh_name(st, x['payer']))})" for x in sh]
    for s in st.get("gym", {}).get("sessions", []):
        try:
            sd = datetime.fromtimestamp(s["start"] / 1000).date()
        except (KeyError, TypeError, ValueError, OSError):
            continue
        if sd == d:
            mins = round(((s.get("end") or s["start"]) - s["start"]) / 60000)
            out.append(f"🏋️ <b>{esc(s.get('name') or 'Allenamento')}</b> · {len(s.get('items', []))} esercizi · {mins} min"
                       + (" · 🏆 record" if s.get("prs") else ""))
    H = st["habits"]["list"]
    done = [h["name"] for h in H if (h.get("done") or {}).get(k)]
    if H and (done or d <= today()):
        out.append(f"✅ Abitudini {len(done)}/{len(H)}" + (f": {esc(', '.join(done))}" if done else ""))
    for t in st.get("trips", []):
        if t.get("start") and t["start"] <= k <= (t.get("end") or t["start"]):
            out.append(f"✈️ {esc(t.get('name') or t['place']['name'])}")
    return out


def agenda_day(d, past=None):
    st = store.read()
    past = d < today() if past is None else past
    evs = events_on(st, d)
    lines = [f"📅 <b>{day_name(d)[:1].upper() + day_name(d)[1:]}</b>"]
    if evs:
        lines += [event_line(ev) for ev in evs]
    elif not past:
        lines.append("Nessun appuntamento.")
    dues = []
    for t in st["trackers"]:
        m = tracker_meta(t)
        for r in t.get("rows", []):
            if due_of(r, m) == d:
                dues.append(f"⏰ {esc(t.get('icon') or '📋')} {esc(str((r.get('cells') or {}).get((m['title'] or {}).get('id', ''), '') or 'Voce'))}"
                            + (" ✓" if row_done(r, m) else ""))
    lines += dues
    td = todos_for(st, d)
    if td:
        lines += ["", f"📝 <b>Da fare</b> · {sum(1 for x in td if x.get('done'))}/{len(td)}"] + [f"{'✅' if x.get('done') else '⬜️'} {esc(x['text'])}" for x in td]
    je = st.get("journal", {}).get("entries", {}).get(iso(d))
    if je and je.get("text"):
        lines += ["", f"📖 <i>{esc(je['text'][:140])}{'…' if len(je['text']) > 140 else ''}</i>"]
    if d <= today():
        h = happened_lines(st, d)
        if h:
            lines += ["", "<b>Cosa è successo</b>" if d < today() else "<b>Finora oggi</b>"] + h
    return "\n".join(lines)


def agenda_next(days=7):
    st, t = store.read(), today()
    occ = agenda.expand(st.get("calendar", {}).get("events", []), t, t + timedelta(days=days))
    if not occ:
        return (f"📅 Nessun appuntamento nei prossimi {days} giorni.\n\n"
                "Per aggiungerne uno scrivi per esempio <b>dentista venerdì alle 15:30</b>.")
    lines, last = [f"📅 <b>I prossimi {days} giorni</b>"], None
    for ev, d in occ[:25]:
        if d != last:
            lines += ["", f"<b>{day_name(d)[:1].upper() + day_name(d)[1:]}</b>"]
            last = d
        lines.append(event_line(ev))
    return "\n".join(lines)


def find_events(q):
    st, t = store.read(), today()
    nq = norm(q).strip()
    cands = [ev for ev, _ in agenda.expand(st.get("calendar", {}).get("events", []), t - timedelta(days=1), t + timedelta(days=400))]
    seen, out = set(), []
    for ev in cands:
        if ev["id"] in seen:
            continue
        seen.add(ev["id"])
        nt = norm(ev.get("title", ""))
        if not nq or nq in nt or brain.fuzzy(nq, [nt], 0.75):
            out.append(ev)
    return out


def delete_event_ask(q):
    found = find_events(q)
    if not found:
        return f"Non trovo appuntamenti{' con «' + esc(q) + '»' if q else ''} nei prossimi mesi."
    kb = [[{"text": f"🗑 {ev['title'][:28]} · {date.fromisoformat(ev['date']).day}/{date.fromisoformat(ev['date']).month}"
            + (f" {ev['start']}" if ev.get('start') else ""), "callback_data": f"ax|{ev['id']}"}] for ev in found[:6]]
    kb.append([{"text": "No, lascia stare", "callback_data": "dn"}])
    return ("Quale appuntamento elimino?" if len(found) > 1 else "Elimino questo appuntamento?"), kb


def delete_event(ev_id, chat):
    def fn(st):
        evs = st.get("calendar", {}).get("events", [])
        ev = next((x for x in evs if x["id"] == ev_id), None)
        if ev:
            i = evs.index(ev)
            evs.remove(ev)
            trash_put(st, "event", ev, ev.get("title") or "Appuntamento", f"Appuntamento · {ev.get('date', '')} {ev.get('start', '')}".strip(), {"i": i})
            return ev, i
        return None
    r = store.mutate(fn)
    if r:
        push_undo(chat, ("restore_event", r[0], r[1]))
    return r[0] if r else None


def event_reminders(now):
    """Avvisi prima degli appuntamenti (chiamata ogni minuto dallo scheduler dei promemoria).
    Un errore nei dati di un utente non deve fermare gli avvisi di tutti gli altri."""
    for u in db.q("SELECT * FROM users WHERE status='active'"):
        try:
            _event_reminders_user(dict(u), now)
        except Exception:
            log.exception("Avvisi degli appuntamenti: errore per un utente (gli altri proseguono)")


def _event_reminders_user(u, now):
    for _once in (1,):
        if not mailer.configured and not (PUSH.ok and push_subs(u)):
            continue
        CURRENT_USER.set(u["id"])
        try:
            st = store.read()
        except Exception:
            continue
        evs = st.get("calendar", {}).get("events", [])
        if not evs:
            continue
        for ev, d in agenda.expand(evs, now.date(), now.date() + timedelta(days=1)):
            r = str(ev.get("remind") or "")
            if not r.isdigit() or ev.get("allDay") or not ev.get("start"):
                continue
            try:
                at = datetime.strptime(f"{iso(d)} {ev['start']}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            fire = at - timedelta(minutes=int(r))
            if not (fire <= now < min(fire + timedelta(hours=2), at + timedelta(minutes=1))):
                continue
            key = f"ev:{ev['id']}"
            if db.one("SELECT 1 FROM reminder_log WHERE user_id=? AND kind=? AND day=?", (u["id"], key, iso(d))):
                continue
            db.run("INSERT OR IGNORE INTO reminder_log(user_id, kind, day) VALUES(?,?,?)", (u["id"], key, iso(d)))
            mins = int((at - now).total_seconds() // 60)
            when = f"tra {mins} minuti" if 0 < mins <= 90 and d == today() else (f"alle {ev['start']}" if d == today() else f"{day_name(d)} alle {ev['start']}")
            subj = noemoji(f"{ev.get('title') or 'Appuntamento'} {when}")
            pbody = noemoji(" · ".join(x for x in (ev["start"] + (f"–{ev['end']}" if ev.get("end") else ""), ev.get("place") or "", (ev.get("notes") or "")[:120]) if x))
            body = ev_short(ev) + (f'<div style="color:#55544f;margin-top:8px">{esc(ev["notes"])}</div>' if ev.get("notes") else "")
            try:
                if not push_user(u, subj, pbody, "/#/calendario", tag="ev" + str(ev["id"])[:20], urgency="high"):
                    mailer.send(u["email"], subj, notify.email_html(subj, body, app_url()))
            except Exception:
                log.exception("Avviso appuntamento")
            if ev.get("people") and ev.get("notifyPeople"):
                try:
                    notify_people(u, {**ev, "date": iso(d)}, "remind")
                except Exception:
                    log.exception("Avviso ai partecipanti")



# ---------------------------------------------------------------- da fare del giorno
TODO_RE = re.compile(r"^/?(?:da\s*fare|todo|to\s*do|cose\s+da\s+fare|aggiungi\s+da\s+fare)\s*(.*?)\s*:\s*(.+)$", re.I | re.S)


def todos_for(st, d):
    """Cose da fare di un giorno; oggi comprende anche quelle rimaste indietro."""
    k, t = iso(d), iso(today())
    out = [x for x in st.get("todos", []) if x.get("date") == k]
    if k == t:
        out += [x for x in st.get("todos", []) if x.get("date", k) < k and not x.get("done")]
    return out


def todo_add(when, text, chat):
    d, _ = agenda.find_day(when, today()) if when.strip() else (today(), "")
    d = d or today()
    parts = [p.strip(" .") for p in re.split(r"[;\n]|,(?!\d)", text) if p.strip(" .")]
    if not parts:
        return None
    new = [{"id": uid(), "text": p[:200], "date": iso(d), "done": False, "created": iso(today())} for p in parts[:15]]
    store.mutate(lambda st: st.setdefault("todos", []).extend(new))
    push_undo(chat, ("todos", [x["id"] for x in new]))
    res = todos_text(d)
    return (f"📝 Aggiunte {len(new)} cose da fare per {day_name(d)}.\n\n" if len(new) > 1 else f"📝 Aggiunto per {day_name(d)}: <b>{esc(new[0]['text'])}</b>\n\n") + res[0], res[1]


def todos_text(d=None):
    d = d or today()
    st = store.read()
    L = todos_for(st, d)
    if not L:
        return f"Nessuna cosa da fare per {day_name(d)}. Aggiungine con <b>da fare: comprare il latte</b>.", None
    done = sum(1 for x in L if x.get("done"))
    lines = [f"📝 <b>Da fare {day_name(d)}</b> · {done}/{len(L)}"]
    kb = []
    for x in L:
        late = x.get("date") and x["date"] < iso(d) and not x.get("done")
        lines.append(f"{'✅' if x.get('done') else '⬜️'} {esc(x['text'])}" + (" <i>(da prima)</i>" if late else ""))
        if not x.get("done"):
            kb.append([{"text": f"✓ {x['text'][:40]}", "callback_data": f"td|{x['id']}"}])
    return "\n".join(lines), (kb[:10] or None)


def todo_toggle(tid):
    def fn(st):
        x = next((y for y in st.get("todos", []) if y["id"] == tid), None)
        if not x:
            return None
        x["done"] = not x.get("done")
        x["doneOn"] = iso(today()) if x["done"] else None
        return dict(x)
    return store.mutate(fn)


# ---------------------------------------------------------------- diario
def journal_habit(st, k):
    h = next((x for x in st["habits"]["list"] if re.search(r"diario|journal", norm(x.get("name", "")))), None)
    if h:
        h.setdefault("done", {})[k] = True
    return h


def journal_add(text, chat):
    k = iso(today())

    def fn(st):
        e = st.setdefault("journal", {"entries": {}}).setdefault("entries", {}).setdefault(k, {"text": "", "mood": 0, "tags": []})
        e["text"] = (e.get("text", "").rstrip() + "\n\n" + text.strip()).strip()
        e["updated"] = int(time.time() * 1000)
        h = journal_habit(st, k)
        return len(e["text"].split()), h["name"] if h else None
    n, h = store.mutate(fn)
    return (f"📖 Aggiunto al diario di oggi ({n} parole in tutto)." + (f"\n✅ Segnata anche l'abitudine «{esc(h)}»." if h else "")
            + "\n<i>Nella web app puoi aggiungere l'umore e i tag.</i>")


def journal_show(d=None):
    d = d or today()
    e = store.read().get("journal", {}).get("entries", {}).get(iso(d))
    if not e or not e.get("text"):
        return f"📖 Nessuna pagina di diario per {day_name(d)}. Scrivi <b>diario: …</b> per iniziarla."
    mood = ["", "😞", "😕", "😐", "🙂", "😄"][int(e.get("mood") or 0)] if str(e.get("mood") or 0).isdigit() else ""
    return f"📖 <b>Diario · {day_name(d)}</b> {mood}\n\n{esc(e['text'][:3500])}"


# ---------------------------------------------------------------- partecipanti agli appuntamenti
NOTIFY_PER_HOUR, NOTIFY_PER_DAY = 15, 30  # inviti per utente: niente spam dal tuo account email
NOTIFY_PER_DAY_NEW = 10                  # account con meno di 7 giorni
NOTIFY_SERVER_DAY = int(os.getenv("INVITES_SERVER_PER_DAY", "400") or 400)  # tetto per tutto il server


def send_log_table():
    db.run("CREATE TABLE IF NOT EXISTS send_log (user_id TEXT NOT NULL, at REAL NOT NULL)")
    db.run("CREATE INDEX IF NOT EXISTS send_log_u ON send_log(user_id, at)")


def send_counts(user_id):
    now = time.time()
    db.run("DELETE FROM send_log WHERE at < ?", (now - 86400,))
    one = lambda q, a: db.one(q, a)["n"]
    return (one("SELECT COUNT(*) n FROM send_log WHERE user_id=? AND at>=?", (user_id, now - 3600)),
            one("SELECT COUNT(*) n FROM send_log WHERE user_id=?", (user_id,)),
            one("SELECT COUNT(*) n FROM send_log", ()))


def ics_event(ev, method="REQUEST", organizer=None):
    def dt(d, hm):
        return d.replace("-", "") + "T" + hm.replace(":", "") + "00"
    esc_i = lambda x: str(x or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Tracker//IT", f"METHOD:{method}", "BEGIN:VEVENT",
             f"UID:{ev['id']}@tracker", f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}", f"SEQUENCE:{int(ev.get('seq') or 0)}"]
    if ev.get("allDay") or not ev.get("start"):
        d0 = date.fromisoformat(ev["date"])
        last = date.fromisoformat(ev["until"]) if ev.get("until") else d0
        lines += [f"DTSTART;VALUE=DATE:{ev['date'].replace('-', '')}", f"DTEND;VALUE=DATE:{iso(last + timedelta(days=1)).replace('-', '')}"]
    else:
        lines += [f"DTSTART:{dt(ev['date'], ev['start'])}", f"DTEND:{dt(ev.get('until') or ev['date'], ev.get('end') or ev['start'])}"]
    rr = {"week": "WEEKLY", "month": "MONTHLY", "year": "YEARLY"}.get(ev.get("repeat") or "")
    if rr:
        lines.append(f"RRULE:FREQ={rr}")
    lines += [f"SUMMARY:{esc_i(ev.get('title'))}"]
    if ev.get("place"):
        lines.append(f"LOCATION:{esc_i(ev['place'])}")
    if ev.get("notes"):
        lines.append(f"DESCRIPTION:{esc_i(ev['notes'])}")
    if organizer:  # (nome, email) di chi invita: le risposte del calendario vanno a lui, non al sistema
        nome, email = organizer if isinstance(organizer, tuple) else (organizer, mailer.sender)
        if email:
            lines.append(f"ORGANIZER;CN={esc_i(nome)}:mailto:{email}")
    if method == "CANCEL":
        lines.append("STATUS:CANCELLED")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def sender_name(u):
    """Nome mostrato come mittente («Nome via Tacuìn»): corto e senza caratteri ingannevoli."""
    n = re.sub(r"[^\w .'\-àèéìòùÀÈÉÌÒÙ]", "", str(u.get("name") or ""))
    return re.sub(r"\s+", " ", n).strip()[:40] or "Un utente"


def notify_people(u, ev, kind):
    """Avvisa i partecipanti: email a tutti (con i pulsanti per il calendario e le risposte) e, a chi usa Tacuìn,
    anche una notifica. Se l'invitato ha già aggiunto l'appuntamento, modifiche e annullamento arrivano anche lì."""
    if iam.settings(u["id"]).get("_no_invites"):
        if kind == "remind":
            return [], ["invio disattivato dall'amministratore"]
        raise PermissionError("L'amministratore ha disattivato l'invio di inviti dal tuo account.")
    people = [p for p in ev.get("people") or [] if isinstance(p, dict)][:20]
    try:
        young = (datetime.now() - datetime.fromisoformat(str(u.get("created"))[:19])).days < 7
    except ValueError:
        young = True
    per_day = NOTIFY_PER_DAY_NEW if young else NOTIFY_PER_DAY
    sent, failed = [], []
    who = u["name"].split()[0]
    head = {"invite": f"{who} ti invita", "update": f"{who} ha cambiato un appuntamento", "cancel": f"{who} ha annullato",
            "remind": "Promemoria"}[kind]
    subj = f"{head}: {ev.get('title') or 'Appuntamento'} · {ev_when(ev)}"
    for p in people:
        name, contact = str(p.get("name") or "").strip()[:60], str(p.get("contact") or "").strip()[:120]
        hour, day, server_day = send_counts(u["id"])
        if hour >= NOTIFY_PER_HOUR or day >= per_day or server_day >= NOTIFY_SERVER_DAY:
            failed.append(f"{name}: troppi invii (massimo {NOTIFY_PER_HOUR} all'ora e {per_day} al giorno), riprova più tardi")
            continue
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact):
            failed.append(f"{name or contact}: manca l'email")
            continue
        other = iam.by_email(contact)
        is_user = bool(other and other.get("status") == "active" and other["id"] != u["id"]
                       and not iam.settings(other["id"]).get("_email_unverified"))
        tok = invite_for(u, ev, contact, name)
        ways = []
        if is_user:
            if kind in ("update", "cancel"):
                try:
                    sync_invitee(other["id"], tok, ev, kind)
                except Exception:
                    log.exception("Aggiornamento dell'invito nel calendario dell'invitato")
            if push_user(other, subj, f"{ev.get('title') or 'Appuntamento'} · {ev_when(ev)}", f"/#/invito/{tok}", tag="inv" + tok[:10]):
                ways.append("notifica")
        if mailer.configured:
            try:
                mailer.send(contact, subj, invite_email_html(kind, u, ev, tok, is_user),
                            attachments=None if kind == "remind" else [("invito.ics", ics_event(ev, "CANCEL" if kind == "cancel" else "REQUEST",
                                                                                                  (u["name"], u["email"])), "text/calendar")],
                            on_behalf=(sender_name(u), u["email"]))
                ways.insert(0, "email")
            except Exception as e:
                failed.append(f"{name or contact}: {e}")
                continue
        elif not ways:
            failed.append(f"{name or contact}: l'invio email non è configurato sul server")
            continue
        sent.append(f"{name or contact} ({' e '.join(ways)})")
        db.run("INSERT INTO send_log(user_id, at) VALUES(?,?)", (u["id"], time.time()))
    return sent, failed


# ---------------------------------------------------------------- inviti: email a tutti, risposte, «Aggiungi al mio Tacuìn»
RSVP_L = {"yes": "Partecipo", "maybe": "Forse", "no": "Non partecipo"}
RSVP_VERB = {"yes": "partecipa", "maybe": "forse partecipa", "no": "non partecipa"}
RSVP_SLUG = {"si": "yes", "forse": "maybe", "no": "no", "yes": "yes", "maybe": "maybe"}
RL_INVITE = RateLimit(120, 3600)  # pagine/risposte pubbliche degli inviti per IP all'ora


def invites_table():
    db.run("CREATE TABLE IF NOT EXISTS invites (token TEXT PRIMARY KEY, owner_id TEXT NOT NULL, event_id TEXT NOT NULL, "
           "email TEXT NOT NULL, name TEXT, rsvp TEXT, rsvp_at TEXT, event TEXT, created TEXT, updated TEXT)")
    db.run("CREATE INDEX IF NOT EXISTS invites_ev ON invites(owner_id, event_id)")


def invite_for(u, ev, email, name):
    """Token personale dell'invitato per questo appuntamento (lo stesso a ogni modifica)."""
    snap = json.dumps({k: ev.get(k) for k in ("id", "title", "date", "start", "end", "allDay", "place", "notes", "repeat", "seq")},
                      ensure_ascii=False)
    now = datetime.now().isoformat(timespec="seconds")
    r = db.one("SELECT token FROM invites WHERE owner_id=? AND event_id=? AND lower(email)=lower(?)", (u["id"], ev["id"], email))
    if r:
        db.run("UPDATE invites SET event=?, name=?, updated=? WHERE token=?", (snap, name, now, r["token"]))
        return r["token"]
    tok = secrets.token_urlsafe(18)
    db.run("INSERT INTO invites(token, owner_id, event_id, email, name, event, created, updated) VALUES(?,?,?,?,?,?,?,?)",
           (tok, u["id"], ev["id"], email, name, snap, now, now))
    return tok


def invite_get(tok):
    if not tok or not re.fullmatch(r"[\w-]{16,40}", str(tok)):
        return None
    r = db.one("SELECT * FROM invites WHERE token=?", (tok,))
    if not r:
        return None
    r = dict(r)
    r["ev"] = json.loads(r.get("event") or "{}")
    return r


HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def clean_event(ev):
    """Appuntamento ricevuto da un utente e destinato ad altri (inviti): solo campi noti, tipi e formati controllati."""
    s_ = lambda v, n: re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(v or ""))[:n]
    out = {"id": re.sub(r"[^\w-]", "", str(ev.get("id") or uid()))[:40] or uid(),
           "title": s_(ev.get("title"), 120), "date": str(ev.get("date") or ""),
           "start": "", "end": "", "allDay": bool(ev.get("allDay")),
           "place": s_(ev.get("place"), 160), "notes": s_(ev.get("notes"), 1000),
           "repeat": ev.get("repeat") if ev.get("repeat") in agenda.REPEAT_LABEL else "",
           "seq": int(num(ev.get("seq"))) if str(ev.get("seq") or "").isdigit() else 0}
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", out["date"]):
        raise HTTPError(400, "data non valida")
    u_ = str(ev.get("until") or "")
    if u_:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", u_) or not out["date"] < u_ or agenda.span_days({"date": out["date"], "until": u_}) > 365:
            raise HTTPError(400, "data di fine non valida")
        out["until"] = u_
    for k in ("start", "end"):
        v = ev.get(k) or ""
        if v and not (isinstance(v, str) and HHMM.fullmatch(v)):
            raise HTTPError(400, "ora non valida")
        out[k] = v
    ppl = ev.get("people") if isinstance(ev.get("people"), list) else []
    out["people"] = [{"name": s_(p.get("name"), 80), "contact": s_(p.get("contact"), 160)} for p in ppl[:50] if isinstance(p, dict)]
    return out


def ev_when(ev):
    d = date.fromisoformat(ev["date"])
    if ev.get("until"):
        e = date.fromisoformat(ev["until"])
        a = day_name(d) + (f", {ev['start']}" if ev.get("start") and not ev.get("allDay") else "")
        b = day_name(e) + (f", {ev['end']}" if ev.get("end") and not ev.get("allDay") else "")
        return f"da {a} a {b}"
    if ev.get("allDay") or not ev.get("start"):
        return day_name(d) + " · tutto il giorno"
    return day_name(d) + f", {ev['start']}" + (f"–{ev['end']}" if ev.get("end") else "")


def cal_links(ev, tok):
    """Pulsanti «aggiungi al calendario» per chi non usa Tacuìn: Google, Outlook e file .ics (Apple e tutti gli altri)."""
    title, d = ev.get("title") or "Appuntamento", ev["date"]
    if ev.get("allDay") or not ev.get("start"):
        d1 = (date.fromisoformat(ev.get("until") or d) + timedelta(days=1)).isoformat()
        g_dates, o = f"{d.replace('-', '')}/{d1.replace('-', '')}", {"startdt": d, "enddt": d1, "allday": "true"}
    else:
        st, en = f"{d}T{ev['start']}:00", f"{ev.get('until') or d}T{ev.get('end') or ev['start']}:00"
        g_dates = f"{st.replace('-', '').replace(':', '')}/{en.replace('-', '').replace(':', '')}"
        o = {"startdt": st, "enddt": en, "allday": "false"}
    q = urllib.parse.urlencode
    google = "https://calendar.google.com/calendar/render?" + q({"action": "TEMPLATE", "text": title, "dates": g_dates,
                                                                 "details": ev.get("notes") or "", "location": ev.get("place") or "",
                                                                 "ctz": os.getenv("TZ", "Europe/Rome")})
    outlook = "https://outlook.live.com/calendar/0/deeplink/compose?" + q({"path": "/calendar/action/compose", "rru": "addevent",
                                                                           "subject": title, "location": ev.get("place") or "",
                                                                           "body": ev.get("notes") or "", **o})
    return google, outlook, f"{app_url()}/api/invite/ics?t={tok}"


def _btn(url, text, primary=False):
    st = ("background:#c65a12;color:#ffffff;border:1px solid #c65a12" if primary else "background:#ffffff;color:#2f2f2d;border:1px solid #d9d8d3")
    return (f'<a href="{esc(url)}" style="{st};text-decoration:none;padding:11px 14px;border-radius:10px;font-weight:600;'
            f'display:inline-block;margin:4px 6px 4px 0;font-size:14px">{esc(text)}</a>')


def invite_email_html(kind, u, ev, tok, is_user):
    who = esc(u["name"])
    head = {"invite": f"<b>{who}</b> ti invita", "update": f"<b>{who}</b> ha cambiato l'appuntamento",
            "cancel": f"<b>{who}</b> ha annullato l'appuntamento", "remind": "Promemoria"}[kind]
    card = (f'<div style="border:1px solid #e6e5e1;border-radius:12px;padding:14px 16px;margin:12px 0 16px">'
            f'<div style="font-size:18px;font-weight:700;margin-bottom:6px{";text-decoration:line-through;color:#8a8984" if kind == "cancel" else ""}">'
            f'{esc(ev.get("title") or "Appuntamento")}</div>'
            f'<div><b>{esc(ev_when(ev))}</b></div>' + (f'<div style="color:#55554f">{esc(ev["place"])}</div>' if ev.get("place") else "")
            + (f'<div style="color:#55554f;margin-top:6px">{esc(ev["notes"])}</div>' if ev.get("notes") else "") + '</div>')
    base = app_url()
    acts = ""
    if kind == "cancel":
        acts = '<p style="margin:0">Se l\'avevi aggiunto al calendario, puoi toglierlo.</p>'
    elif is_user:
        r = {k: f"{base}/#/invito/{tok}/{v}" for k, v in (("yes", "si"), ("maybe", "forse"), ("no", "no"))}
        acts = (_btn(f"{base}/#/invito/{tok}", "Aggiungi al mio Tacuìn", True)
                + '<div style="margin:14px 0 4px;font-size:13px;color:#75746f">Rispondi:</div>'
                + _btn(r["yes"], "Partecipo") + _btn(r["maybe"], "Forse") + _btn(r["no"], "Non partecipo"))
    else:
        g, o, ics = cal_links(ev, tok)
        r = {k: f"{base}/invito?t={tok}&r={v}" for k, v in (("yes", "si"), ("maybe", "forse"), ("no", "no"))}
        acts = (_btn(g, "Aggiungi a Google Calendar", True) + _btn(o, "Outlook") + _btn(ics, "Apple Calendario")
                + '<div style="margin:14px 0 4px;font-size:13px;color:#75746f">Rispondi a ' + who + ':</div>'
                + _btn(r["yes"], "Partecipo") + _btn(r["maybe"], "Forse") + _btn(r["no"], "Non partecipo"))
    foot = ("Hai ricevuto questo messaggio perché sei tra i partecipanti. Rispondendo a questa email scrivi direttamente a "
            f"{u['name']}. Il file allegato aggiunge l'appuntamento a qualsiasi calendario.")
    li = legal_info()
    privacy = (f"{u['name']} ha inserito il tuo nome e la tua email tra i partecipanti. Titolare del trattamento: "
               f"{li['owner'] or 'il gestore di Tacuìn'}{' (' + li['email'] + ')' if li['email'] else ''}. Usiamo questi dati e la tua risposta "
               f"solo per recapitarti l'invito e i suoi aggiornamenti e per mostrare la risposta a chi ti ha invitato (art. 6.1.f GDPR); "
               f"li cancelliamo al più tardi {max(1, round(INVITE_RETENTION_DAYS / 30.4))} mesi dopo l'appuntamento. Puoi chiedere accesso, "
               f"cancellazione o opporti in ogni momento.")
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;background:#f4f4f2;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#2f2f2d">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">
<div style="font-weight:300;font-size:20px;letter-spacing:.01em;margin:0 0 14px 4px;color:#2a2a27">tacu<span style="color:#c65a12">ì</span>n</div>
<div style="background:#ffffff;border:1px solid #e6e5e1;border-radius:14px;padding:22px 24px;font-size:15px;line-height:1.6">
<div style="font-size:16px">{head}</div>{card}{acts}
<p style="margin:18px 0 0;font-size:12.5px;color:#8a8984">Inviato da {who} ({esc(u['email'])}) con Tacuìn. Se non conosci questa persona, ignora il messaggio.</p>
</div>
<p style="font-size:12px;color:#75746f;margin:14px 4px">{esc(foot)}</p>
<p style="font-size:11.5px;color:#8a8984;margin:8px 4px">{esc(privacy)} <a href="{esc(app_url())}/privacy" style="color:#c65a12">Informativa privacy</a></p>
</div></body></html>"""


def sync_invitee(other_id, tok, ev, kind):
    """Se l'invitato ha aggiunto l'appuntamento al suo Tacuìn, lo aggiorno anche lì."""
    if store.snapshot(other_id)[1] is None:
        return

    def fn(st):
        for e in st.get("calendar", {}).get("events", []):
            if (e.get("invite") or {}).get("t") == tok:
                if kind == "cancel":
                    e["title"] = "Annullato: " + str(e.get("title") or "").removeprefix("Annullato: ")
                    e["remind"] = ""
                    e["invite"]["cancelled"] = True
                else:
                    clean = clean_event(ev)
                    for k in ("title", "date", "start", "end", "allDay", "place", "notes", "repeat"):
                        e[k] = clean.get(k)
                return True
    store.mutate(fn, other_id)


def record_rsvp(inv, r):
    """Salva la risposta, la segna accanto al partecipante nell'appuntamento di chi invita e lo avvisa."""
    now = datetime.now().isoformat(timespec="seconds")
    prev = inv.get("rsvp")
    db.run("UPDATE invites SET rsvp=?, rsvp_at=? WHERE token=?", (r, now, inv["token"]))
    owner = iam.user(inv["owner_id"])
    if not owner:
        return
    title = inv["ev"].get("title") or "Appuntamento"
    if store.snapshot(owner["id"])[1] is not None:
        def fn(st):
            for e in st.get("calendar", {}).get("events", []):
                if e.get("id") == inv["event_id"]:
                    for pp in e.get("people") or []:
                        if str(pp.get("contact") or "").strip().lower() == inv["email"].lower():
                            pp["rsvp"], pp["rsvpAt"] = r, now
                    return e.get("title")
        title = store.mutate(fn, owner["id"]) or title
    if prev != r:
        who = inv.get("name") or inv["email"]
        subj = f"{who} {RSVP_VERB[r]}: {title}"
        body = f"{esc(who)} ha risposto <b>{RSVP_L[r]}</b> a «{esc(title)}» ({esc(ev_when(inv['ev']))})."
        if not push_user(owner, subj, body, f"/#/calendario/{inv['ev'].get('date', '')}", tag="rsvp" + inv["event_id"][:20]):
            email_user(owner, subj, body, "Apri il calendario", f"{app_url()}/#/calendario/{inv['ev'].get('date', '')}",
                       "Ricevi questa email perché hai invitato qualcuno con Tacuìn.")
        log.info("Risposta a un invito: %s", r)


INVITE_PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Invito · Tacuìn</title><style>
body{margin:0;background:#f4f4f2;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#2f2f2d;line-height:1.55}
.w{max-width:520px;margin:0 auto;padding:28px 16px}.brand{font-weight:300;font-size:22px;margin:0 0 16px 4px}.brand span{color:#c65a12}
.c{background:#fff;border:1px solid #e6e5e1;border-radius:16px;padding:22px}.t{font-size:21px;font-weight:700;margin:6px 0 8px}
.m{color:#55554f}.r{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 6px}
button,a.b{font:inherit;font-weight:600;border-radius:12px;padding:12px 16px;border:1px solid #d9d8d3;background:#fff;color:#2f2f2d;cursor:pointer;text-decoration:none;display:inline-block;font-size:15px}
button.on{background:#c65a12;border-color:#c65a12;color:#fff}button.pre{border:2px solid #c65a12;color:#c65a12}.ok{margin-top:14px;padding:12px 14px;border-radius:12px;background:#e6f1ee}
.small{font-size:13px;color:#75746f;margin-top:18px}
</style></head><body><div class="w"><div class="brand">tacu<span>ì</span>n</div><div class="c">
<div class="m">__WHO__ ti invita</div><div class="t">__TITLE__</div><div><b>__WHEN__</b></div>__PLACE__
__BODY__
</div><p class="small">Tacuìn è l'agenda personale di __WHO__. Non serve un account per rispondere. Usiamo il tuo nome, la tua email e la risposta solo per questo invito e li cancelliamo al più tardi 12 mesi dopo l'appuntamento. <a href="/privacy" style="color:#c65a12">Informativa privacy</a></p></div>
<script>
const T = __TOK__, R0 = __R__;
const L = {yes: 'Partecipo', maybe: 'Forse', no: 'Non partecipo'};
async function answer(r) {
  document.querySelectorAll('[data-r]').forEach(b => b.disabled = true);
  try {
    const res = await fetch('/api/invite/answer', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({t: T, r})});
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || 'Errore');
    document.querySelectorAll('[data-r]').forEach(b => { b.classList.toggle('on', b.dataset.r === r); b.classList.remove('pre'); });
    document.getElementById('intro')?.remove();
    const ok = document.getElementById('ok'); ok.hidden = false;
    ok.innerHTML = r === 'yes' ? 'Risposta inviata: <b>Partecipo</b>. Sto scaricando il file per il tuo calendario… <a class="b" style="margin-top:10px" href="/api/invite/ics?t=' + T + '">Scaricalo di nuovo</a>' : 'Risposta inviata: <b>' + L[r] + '</b>. Grazie!';
    if (r === 'yes') location.href = '/api/invite/ics?t=' + T;
  } catch (e) { alert(e.message); }
  document.querySelectorAll('[data-r]').forEach(b => b.disabled = false);
}
document.querySelectorAll('[data-r]').forEach(b => b.onclick = () => answer(b.dataset.r));
</script></body></html>"""


# ---------------------------------------------------------------- Garmin
GARMIN = garmin.Garmin(SSL_CTX)
GARMIN_EX = {"running": "corsa", "street_running": "corsa", "trail_running": "corsa", "track_running": "corsa", "treadmill_running": "tapis",
             "running_treadmill": "tapis", "cycling": "bici", "road_biking": "bici", "mountain_biking": "bici", "gravel_cycling": "bici",
             "indoor_cycling": "cyclette", "cycling_indoor_cycling": "cyclette", "virtual_ride": "cyclette", "lap_swimming": "nuoto",
             "open_water_swimming": "nuoto", "swimming": "nuoto", "swimming_lap_swimming": "nuoto", "indoor_rowing": "vogatore", "rowing": "vogatore",
             "rowing_indoor_rowing": "vogatore", "elliptical": "ellittica", "fitness_equipment_elliptical": "ellittica", "stair_climbing": "stepper",
             "fitness_equipment_stair_climbing": "stepper", "walking": "camminata", "hiking": "camminata", "yoga": "yoga", "training_yoga": "yoga",
             "hiit": "hiit", "training_hiit": "hiit", "breathwork": "stretching", "training_flexibility_training": "stretching"}
GARMIN_NAMES = {"strength_training": "Allenamento con i pesi", "training_strength_training": "Allenamento con i pesi", "indoor_climbing": "Arrampicata",
                "bouldering": "Boulder", "rock_climbing": "Arrampicata", "rock_climbing_bouldering": "Boulder", "rock_climbing_indoor_climbing": "Arrampicata",
                "pilates": "Pilates", "training_pilates": "Pilates", "cardio_training": "Cardio", "training_cardio_training": "Cardio",
                "boxing": "Boxe", "dance": "Danza", "fitness_equipment": "Attrezzi", "training": "Allenamento"}


def garmin_session(sid, typ, start, secs, meters, kcal, hr=None, maxhr=None, name=None):
    typ = (typ or "").lower()
    ex = GARMIN_EX.get(typ)
    ms = int(start.timestamp() * 1000)
    s = {"id": sid, "source": "garmin", "start": ms, "end": ms + int(secs * 1000), "tpl": None, "prs": [],
         "garmin": {"type": typ, "kcal": int(kcal or 0), "hr": int(hr) if hr else None, "maxhr": int(maxhr) if maxhr else None},
         "items": [{"id": uid(), "ex": ex, "c": {"min": str(round(secs / 60)), "km": (f"{meters / 1000:.2f}".rstrip("0").rstrip(".") if meters else "")}}] if ex else []}
    s["name"] = name or (GARMIN_NAMES.get(typ) or (typ.replace("_", " ").capitalize() if typ else "Attività")) if not ex else (name or "")
    if "climb" in typ or "boulder" in typ:
        s["kind"] = "climb"
    return s


def garmin_merge(sessions):
    def fn(st):
        g = st.setdefault("gym", {}).setdefault("sessions", [])
        have = {x.get("id") for x in g}
        add = [x for x in sessions if x["id"] not in have]
        g.extend(add)
        return len(add)
    return store.mutate(fn)


def garmin_sync(u, days=30):
    cfg = iam.settings(u["id"]).get("_garmin") or {}
    if not cfg.get("key"):
        raise garmin.GarminError("Garmin non è collegato.")
    tok, exp = cfg.get("token"), cfg.get("exp") or 0
    if not tok or exp < time.time() + 120:
        tok, exp = GARMIN.token(cfg["key"])
    since = date.fromisoformat(cfg["last_day"]) - timedelta(days=2) if cfg.get("last_day") else today() - timedelta(days=days)
    acts = GARMIN.activities(tok, iso(since))
    out = []
    for a in acts:
        st0 = garmin.parse_start(a)
        aid = a.get("activityId") or a.get("id")
        if not st0 or not aid:
            continue
        out.append(garmin_session(f"gm-{aid}", garmin.activity_type(a), st0, garmin._num(a.get("duration") or a.get("elapsedDuration") or a.get("movingDuration")),
                                  garmin._num(a.get("distance")), garmin._num(a.get("calories")), a.get("averageHR") or a.get("avgHr"),
                                  a.get("maxHR") or a.get("maxHr"), a.get("activityName")))
    CURRENT_USER.set(u["id"])
    n = garmin_merge(out)
    iam.set_settings(u["id"], {"_garmin": {**cfg, "token": tok, "exp": exp, "last_sync": int(time.time()), "last_day": iso(today()), "error": None,
                                           "count": (cfg.get("count") or 0) + n}})
    garmin_wellness(u, tok)
    return n, len(out)


def garmin_token(u):
    cfg = iam.settings(u["id"]).get("_garmin") or {}
    if not cfg.get("key"):
        raise garmin.GarminError("Garmin non è collegato.")
    tok, exp = cfg.get("token"), cfg.get("exp") or 0
    if not tok or exp < time.time() + 120:
        tok, exp = GARMIN.token(cfg["key"])
        iam.set_settings(u["id"], {"_garmin": {**cfg, "token": tok, "exp": exp}})
    return tok


def garmin_wellness(u, tok=None):
    """Passi e battito → state.garmin, conservati per sempre.
    Passi: dal giorno dopo l'ultimo salvato (2 giorni di margine) o, la prima volta, gli ultimi 60 giorni; a blocchi di 28 giorni.
    Battito: il dettaglio di oggi (ultimi 5 minuti, ultima ora) + riposo/min/max di ogni giorno in hrDays.
    Un dato che manca non blocca gli altri."""
    tok = tok or garmin_token(u)
    oggi = today()
    CURRENT_USER.set(u["id"])
    have = (store.read(u["id"]) or {}).get("garmin") or {}
    giorni = sorted((have.get("steps") or {}).keys())
    da = max(date.fromisoformat(giorni[-1]) - timedelta(days=2), oggi - timedelta(days=365)) if giorni else oggi - timedelta(days=59)
    w, errs, steps = {}, [], {}
    try:
        d0 = da
        while d0 <= oggi:
            d1 = min(d0 + timedelta(days=27), oggi)
            steps.update(GARMIN.daily_steps(tok, iso(d0), iso(d1)))
            d0 = d1 + timedelta(days=1)
    except garmin.GarminError as e:
        errs.append(f"passi: {e}")
    hr_days = {}
    try:
        hr = GARMIN.daily_heart_rate(tok, iso(oggi))
        if not hr.get("lastAt") and not hr.get("rest"):  # appena dopo mezzanotte oggi è ancora vuoto
            hr = GARMIN.daily_heart_rate(tok, iso(oggi - timedelta(days=1)))
        w["hr"] = hr
        if hr.get("date") and (hr.get("rest") or hr.get("max")):
            hr_days[hr["date"]] = {k: hr.get(k) for k in ("rest", "min", "max")}
    except garmin.GarminError as e:
        errs.append(f"battito: {e}")
    w.update(at=int(time.time()), err="; ".join(errs)[:300] or None)

    def fn(st):
        g = st.setdefault("garmin", {})
        g.update(w)
        g["steps"] = dict(sorted({**(g.get("steps") or {}), **steps}.items()))      # per sempre
        g["hrDays"] = dict(sorted({**(g.get("hrDays") or {}), **hr_days}.items()))   # per sempre
        return True
    store.mutate(fn)
    return w


def garmin_auto(now):
    """Ogni 3 ore sincronizza chi ha collegato Garmin (chiamata dallo scheduler)."""
    if now.minute % 15:
        return
    for u in db.q("SELECT * FROM users WHERE status='active' AND settings LIKE '%_garmin%'"):
        u = dict(u)
        cfg = iam.settings(u["id"]).get("_garmin") or {}
        if not cfg.get("key") or time.time() - (cfg.get("last_sync") or 0) < 3 * 3600:
            continue
        try:
            garmin_sync(u)
        except Exception as e:
            iam.set_settings(u["id"], {"_garmin": {**cfg, "last_sync": int(time.time()), "error": str(e)[:200]}})


# ---------------------------------------------------------------- prezzi di borsa e piani di accumulo
QUOTES_ENV_OFF = (os.getenv("QUOTES") or "").strip().lower() in ("off", "0", "no", "false")
QUOTES = quotes_mod.Quotes(DATA_DIR / "quotes-cache.json")


FOOD_ENV_OFF = (os.getenv("FOOD") or "").strip().lower() in ("off", "0", "no", "false")
FOODS = food_mod.Foods(DATA_DIR / "food-cache.json")


def food_on():
    return not FOOD_ENV_OFF and server_conf().get("food", True) is not False


def quotes_on():
    return not QUOTES_ENV_OFF and server_conf().get("quotes", True) is not False


def _need_quotes(handler):
    u = handler.current()
    if not quotes_on():
        raise HTTPError(503, "I prezzi automatici sono spenti su questo server: scrivi il prezzo a mano.")
    if not RL_QUOTES.hit(u["id"]):
        raise HTTPError(429, "Troppe richieste di prezzi: riprova tra qualche minuto.")
    return u


def _symbols(url, cap=40):
    raw = urllib.parse.parse_qs(url.query).get("s", [""])[0]
    out = []
    for x in raw.split(","):
        x = x.strip()
        if x and quotes_mod.SYMBOL_RE.fullmatch(x) and x not in out:
            out.append(x)
    if not out:
        raise HTTPError(400, "simbolo mancante")
    return out[:cap]


def refresh_prices(items):
    """Prezzi aggiornati per i titoli con un simbolo e «prezzo dal mercato» acceso. → {id: quote}"""
    got = {}
    for x in items:
        sym = x.get("symbol")
        if not sym or x.get("auto") is False:
            continue
        try:
            got[x["id"]] = QUOTES.quote(sym)
        except quotes_mod.QuoteError:
            continue
    return got


def apply_prices(items, got):
    for x in items:
        q = got.get(x.get("id"))
        if q and q.get("price"):
            x["price"] = round(q["price"], 4)
            x["chg"] = q.get("chg")
            x["updated"] = iso(today())
            x["qat"] = q.get("at")


def invest_daily(now):
    """Una volta al giorno (dalle 7): prezzi aggiornati, versamenti automatici dei PAC, foto del valore per il grafico,
    avvisi del giorno prima. Un errore con un utente non ferma gli altri."""
    if now.hour < 7 or now.minute % 10:
        return
    day = iso(now.date())
    for u in db.q("SELECT * FROM users WHERE status='active'"):
        u = dict(u)
        if db.one("SELECT 1 FROM reminder_log WHERE user_id=? AND kind='inv' AND day=?", (u["id"], day)):
            continue
        db.run("INSERT OR IGNORE INTO reminder_log(user_id, kind, day) VALUES(?,?,?)", (u["id"], "inv", day))
        try:
            _invest_daily_user(u, now.date())
        except Exception:
            log.exception("Investimenti del giorno: errore per un utente (gli altri proseguono)")
        finally:
            CURRENT_USER.set(None)


def _invest_daily_user(u, d):
    CURRENT_USER.set(u["id"])
    st = store.read()
    I = st.get("investments") or {}
    if not I.get("items"):
        return
    got = refresh_prices(I["items"]) if quotes_on() else {}

    def fn(st):
        inv = st["investments"]
        apply_prices(inv["items"], got)
        done = invest.run_plans(inv, d)
        invest.snapshot(inv, d)
        names = {x["id"]: x.get("name") or x.get("symbol") or "titolo" for x in inv["items"]}
        tomorrow, today_manual = [], []
        for p in inv.get("plans") or []:
            if p.get("paused") or p.get("item") not in names:
                continue
            nd = invest.next_date(p, d)
            if nd == d + timedelta(days=1):
                tomorrow.append((names[p["item"]], invest.num(p.get("amount")), p.get("auto")))
            if not p.get("auto") and nd == d:
                today_manual.append((names[p["item"]], invest.num(p.get("amount"))))
        return [(names.get(it["id"]), q, pr, p.get("amount")) for p, it, _d, q, pr in done], tomorrow, today_manual
    done, tomorrow, manual = store.mutate(fn)
    lines = [f"Registrato: {eur(a)} in {n} ({fmt_n(q)} quote a {eur(pr)})" for n, q, pr, a in done]
    lines += [f"Domani: {eur(a)} in {n}" + ("" if auto else " (da registrare tu)") for n, a, auto in tomorrow]
    lines += [f"Oggi: {eur(a)} in {n}, registralo quando hai comprato" for n, a in manual]
    if not lines:
        return
    subj = "Piano di accumulo" if len(lines) == 1 else f"Piani di accumulo: {len(lines)} novità"
    body = " · ".join(lines)
    if not push_user(u, subj, body, "/#/investimenti", tag="pac" + iso(d)):
        if mailer.configured:
            email_user(u, subj, "".join(f"<p style=\"margin:0 0 6px\">{esc(x)}</p>" for x in lines), "Apri gli investimenti",
                       f"{app_url()}/#/investimenti")


def eur(v):
    s = f"{invest.num(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s + " €"


def fmt_n(v):
    return (f"{v:.4f}".rstrip("0").rstrip(".")).replace(".", ",")



def nut_reminders(now):
    """Alimentazione, se l'utente l'ha chiesto: un'ora dopo l'orario di un momento della giornata, se il diario è vuoto,
    una notifica. Solo a chi ha le notifiche attive; una volta al giorno per momento."""
    if not PUSH.ok or now.minute % 5:
        return
    day, mins = iso(now.date()), now.hour * 60 + now.minute
    for u in db.q("SELECT * FROM users WHERE status='active'"):
        u = dict(u)
        if not push_subs(u):
            continue
        try:
            CURRENT_USER.set(u["id"])
            N = store.read().get("nutrition") or {}
            if not N.get("remind"):
                continue
            entries = ((N.get("days") or {}).get(day) or {}).get("e") or []
            for sl in N.get("slots") or []:
                t = str(sl.get("time") or "")
                if not re.fullmatch(r"\d{2}:\d{2}", t):
                    continue
                due = int(t[:2]) * 60 + int(t[3:]) + 60
                if not (due <= mins < due + 5) or any(e.get("slot") == sl.get("id") for e in entries):
                    continue
                kind = "nut:" + str(sl.get("id"))[:20]
                if db.one("SELECT 1 FROM reminder_log WHERE user_id=? AND kind=? AND day=?", (u["id"], kind, day)):
                    continue
                db.run("INSERT OR IGNORE INTO reminder_log(user_id, kind, day) VALUES(?,?,?)", (u["id"], kind, day))
                push_user(u, str(sl.get("name") or "Pasto")[:40], "Segna cosa hai mangiato, basta il nome.", "/#/alimentazione", tag=kind)
        except Exception:
            log.exception("Promemoria alimentazione: errore per un utente (gli altri proseguono)")
        finally:
            CURRENT_USER.set(None)


def every_minute(now):
    event_reminders(now)
    garmin_auto(now)
    invest_daily(now)
    nut_reminders(now)


# ---------------------------------------------------------------- scontrini (servizio vision)
_vision_cache = {"ok": False, "at": 0.0}
PENDING = {}  # token -> {"user": id, "draft": {...}, "at": time} (scontrini in attesa di conferma)


def vision_ok():
    if not VISION_URL:
        return False
    if time.time() - _vision_cache["at"] > (60 if _vision_cache["ok"] else 10):  # se non risponde, riprova presto
        try:
            with urllib.request.urlopen(VISION_URL + "/health", timeout=2) as r:
                _vision_cache["ok"] = r.status == 200
        except Exception:
            _vision_cache["ok"] = False
        _vision_cache["at"] = time.time()
    return _vision_cache["ok"]


def vision_analyze(image_bytes):
    if not VISION_URL:
        raise ValueError("La lettura degli scontrini non è attiva: serve il servizio «vision» (vedi README).")
    req = urllib.request.Request(VISION_URL + "/analyze", data=image_bytes,
                                 headers={"Content-Type": "application/octet-stream", "X-Vision-Token": VISION_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except Exception:
            msg = None
        raise ValueError(msg or "Il servizio scontrini ha risposto con un errore.")
    except Exception:
        _vision_cache.update(ok=False, at=time.time())
        raise ValueError("Il servizio scontrini non risponde: controlla che il container «vision» sia avviato.")


def receipt_draft(image_bytes):
    """Analizza la foto e prepara la spesa da confermare (categoria scelta dal modello ML)."""
    r = vision_analyze(image_bytes)
    st = store.read()
    E = st["expenses"]
    merchant = r.get("merchant") or ""
    words = merchant + " " + " ".join(i["name"] for i in (r.get("items") or [])[:8])
    ml = brain.CategoryModel(E["items"], E["categories"]).predict(merchant) if merchant else None
    if ml:
        cat = ml["category"]
    else:
        cat, _new = match_category(words, None, E["categories"], "out")
    return {"merchant": merchant, "total": r.get("total"), "date": r.get("date") or iso(today()), "time": r.get("time"),
            "category": cat, "ml": ml, "items": (r.get("items") or [])[:30], "confidence": r.get("confidence") or {},
            "receipt_likelihood": r.get("receipt_likelihood", 0), "ms": r.get("ms"), "date_found": bool(r.get("date"))}


def receipt_text(d):
    c = d["confidence"]
    flag = lambda k: "" if c.get(k, 0) >= 0.7 else " ❓"
    lines = ["🧾 <b>Scontrino letto</b>" + (f" in {d['ms'] / 1000:.1f} s" if d.get("ms") else ""), "",
             f"🏪 {esc(d['merchant'] or 'Negozio non riconosciuto')}{flag('merchant')}",
             f"💶 <b>{eur(d['total']) if d['total'] else 'Totale non trovato'}</b>{flag('total')}",
             f"📅 {day_label(date.fromisoformat(d['date']))}" + (f" alle {d['time']}" if d.get("time") else "") + ("" if d["date_found"] else " ❓"),
             f"🏷 {esc(d['category'])}" + (f" · <i>{ml_note(d['ml'])}</i>" if d.get("ml") else "")]
    if d["items"]:
        lines.append(f"<i>{len(d['items'])} articoli letti</i>")
    if d["receipt_likelihood"] < 0.4:
        lines += ["", "⚠️ <i>Non sembra uno scontrino: controlla bene i dati.</i>"]
    return "\n".join(lines)


def receipt_save(d, chat):
    if not d.get("total"):
        raise Reply("Non ho trovato il totale 🤔 Scrivimelo tu, ad esempio: <b>" + esc((d.get("merchant") or "spesa").lower()) + " 12,50</b>")

    def fn(st):
        item = {"id": uid(), "date": d["date"], "desc": d["merchant"] or "Scontrino", "category": d["category"],
                "type": "out", "amount": round(float(d["total"]), 2)}
        if d["category"] not in st["expenses"]["categories"]:
            st["expenses"]["categories"].append(d["category"])
        st["expenses"]["items"].append(item)
        return item
    item = store.mutate(fn)
    push_undo(chat, ("expense", item["id"]))
    return (f"✅ Registrata: <b>{eur(item['amount'])}</b> · {esc(item['desc'])}\n🏷 {esc(item['category'])} · 📅 "
            f"{day_label(date.fromisoformat(item['date']))}", expense_kb(item["id"]))


# ---------------------------------------------------------------- promemoria: contenuti
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⏩-⏺⌚⌛←-⇿✅❌]+")


def noemoji(t):
    """Notifiche sobrie: niente emoji, solo testo."""
    return re.sub(r"[ \t]{2,}", " ", EMOJI.sub("", str(t or ""))).strip()


def sec(label, rows):
    """Una sezione dell'email: etichetta piccola e righe; in grassetto solo orari e cifre."""
    rows = [noemoji(r) for r in rows if r]
    if not rows:
        return ""
    return (f'<div style="font-size:12px;font-weight:600;letter-spacing:.06em;color:#75746f;margin:18px 0 4px">{esc(label.upper())}</div>'
            + "<br>".join(rows))


def ev_short(ev):
    """«<b>18:30</b> Dentista · Via Roma 3» (per email e notifiche)."""
    when = "Tutto il giorno" if ev.get("allDay") or not ev.get("start") else esc(str(ev["start"])) + (f"–{esc(str(ev['end']))}" if ev.get("end") else "")
    return f"<b>{when}</b> {esc(ev.get('title') or 'Appuntamento')}" + (f' <span style="color:#75746f">· {esc(ev["place"])}</span>' if ev.get("place") else "")


def build_digest(kind, u):
    """Testo di un promemoria per l'utente u: dict(subject, body, push) oppure None se non c'è niente da dire.
    body è l'HTML dell'email (sezioni con etichette), push è una riga breve per la notifica."""
    CURRENT_USER.set(u["id"])
    st, d = store.read(), today()
    k = iso(d)
    H = st["habits"]["list"]
    undone = [h for h in H if not (h.get("done") or {}).get(k)]
    name = esc(u["name"].split()[0])
    date_t = f"{GIORNI[d.weekday()].capitalize()} {d.day} {MESI[d.month - 1].lower()}"
    if kind == "morning":
        parts, push = [], []
        evs = events_on(st, d)
        if evs:
            parts.append(sec("Oggi", [ev_short(ev) for ev in evs]))
            first = evs[0]
            push.append(f"{first.get('title') or 'Appuntamento'}" + (f" alle {first['start']}" if first.get("start") and not first.get("allDay") else "")
                        + (f" e altri {len(evs) - 1}" if len(evs) > 1 else ""))
        td = [x for x in todos_for(st, d) if not x.get("done")]
        due = []
        for t in st["trackers"]:
            m, rows = open_rows(t)
            for r in rows:
                dd = due_of(r, m)
                if dd and (dd - d).days <= 3:
                    nm = str((r.get("cells") or {}).get(m["title"]["id"], "") if m["title"] else "").strip() or "(senza nome)"
                    due.append(f"{esc(nm)} <span style=\"color:#75746f\">· {esc(due_label(dd))}</span>")
        todo_rows = [esc(x["text"]) + (' <span style="color:#75746f">· da prima</span>' if x.get("date", k) < k else "") for x in td] + due
        if todo_rows:
            parts.append(sec("Da fare", todo_rows))
            push.append(f"{len(todo_rows)} {'cosa' if len(todo_rows) == 1 else 'cose'} da fare")
        if H:
            parts.append(sec("Abitudini", [f"<b>{len(H) - len(undone)} di {len(H)}</b>" + (" · " + ", ".join(esc(h["name"]) for h in undone) if undone else " · tutte fatte")]))
            if undone:
                push.append(f"{len(undone)} {'abitudine' if len(undone) == 1 else 'abitudini'}")
        for t in sorted(st.get("trips", []), key=lambda t: t.get("start", "")):
            if not t.get("start"):
                continue
            when, n = trip_when(t, d)
            if 0 <= n <= 3:
                line = f"<b>{esc(t.get('name') or t['place']['name'])}</b> · {esc(when)}"
                try:
                    w = TRAVEL.weather(t["place"]["lat"], t["place"]["lon"], max(t["start"], k), t.get("end"))
                    if w["days"]:
                        line += f" · {travel.fmt_t(w['days'][0]['min'])} / {travel.fmt_t(w['days'][0]['max'])}"
                except ValueError:
                    pass
                todo = [c for c in t.get("checklist", []) if not c.get("done")]
                parts.append(sec("Viaggio", [line, f"{len(todo)} cose da preparare" if todo and n > 0 else ""]))
                break
        items, inc, out = month_totals(st, k[:7])
        fc = next((i for i in brain.insights(st, d) if i["kind"] == "forecast"), None)
        parts.append(sec(MESI[d.month - 1], [f"<b>{eur(out)}</b> di uscite" + (f' <span style="color:#75746f">· previsione {eur(fc["data"]["forecast"])}</span>' if fc else "")]))
        body = "".join(parts).replace("margin:18px 0 4px", "margin:2px 0 4px", 1) or "Niente in programma: giornata libera."
        return {"subject": date_t, "title": f"Buongiorno {u['name'].split()[0]}", "body": body,
                "push": " · ".join(push) or "Niente in programma: giornata libera"}
    if kind == "habits":
        if not undone:
            return None
        n = len(undone)
        rows = [esc(h["name"]) + (f' <span style="color:#75746f">· serie di {streak(h)} giorni</span>' if streak(h) > 1 else "") for h in undone]
        return {"subject": f"Abitudini: {'ne manca una' if n == 1 else f'ne mancano {n}'}",
                "body": f"Ciao {name}, oggi ti {'manca' if n == 1 else 'mancano'} ancora:" + sec("Da fare", rows),
                "push": ", ".join(h["name"] for h in undone)[:200]}
    if kind == "deadlines":
        rows, names = [], []
        for t in st["trackers"]:
            m, open_ = open_rows(t)
            for r in open_:
                dd = due_of(r, m)
                if dd and 0 <= (dd - d).days <= 1:
                    nm = str((r.get("cells") or {}).get(m["title"]["id"], "") if m["title"] else "").strip() or "(senza nome)"
                    rows.append(f"<b>{esc(nm)}</b> <span style=\"color:#75746f\">· {esc(t.get('name') or 'Progetto')} · {esc(due_label(dd))}</span>")
                    names.append(nm)
        if not rows:
            return None
        return {"subject": f"{len(rows)} {'scadenza' if len(rows) == 1 else 'scadenze'} tra oggi e domani", "body": sec("Scadenze", rows),
                "push": ", ".join(names)[:200]}
    if kind == "weekly":
        start, end = d - timedelta(days=7), d - timedelta(days=1)
        week = [x for x in st["expenses"]["items"] if brain.in_range(x, start, end)]
        out = sum(num(x["amount"]) for x in week if x.get("type") == "out")
        prev = sum(num(x["amount"]) for x in st["expenses"]["items"]
                   if x.get("type") == "out" and brain.in_range(x, start - timedelta(days=7), end - timedelta(days=7)))
        by = {}
        for x in week:
            if x.get("type") == "out":
                by[x["category"]] = by.get(x["category"], 0) + num(x["amount"])
        parts = [f"Dal {start.day}/{start.month} al {end.day}/{end.month}."]
        parts.append(sec("Uscite", [f"<b>{eur(out)}</b>" + (f' <span style="color:#75746f">· {"+" if out >= prev else "−"}{eur(abs(out - prev))} rispetto alla settimana prima</span>' if prev else "")]
                         + [f"{esc(c)} · {eur(v)}" for c, v in sorted(by.items(), key=lambda kv: -kv[1])[:3]]))
        if H:
            tot = sum(1 for h in H for j in range(7) if (h.get("done") or {}).get(iso(start + timedelta(days=j))))
            parts.append(sec("Abitudini", [f"<b>{tot} su {len(H) * 7}</b> · {tot / (len(H) * 7) * 100:.0f}%"]))
        ins = [i for i in brain.insights(st, d) if i["kind"] in ("anomaly", "recurring", "habits")][:2]
        if ins:
            parts.append(sec("Da tenere d'occhio", [i["text"] for i in ins]))
        return {"subject": f"La tua settimana: {eur(out)} di uscite", "body": "".join(parts),
                "push": f"{eur(out)} di uscite" + (f", {'+' if out >= prev else '−'}{eur(abs(out - prev))} sulla settimana prima" if prev else "")}
    if kind == "debts":
        bal = sh_balances(st)
        owed = [(sh_name(st, pid), v) for pid, v in bal.items() if v >= 0.01]
        if not owed:
            return None
        tot = sum(v for _, v in owed)
        owed.sort(key=lambda x: -x[1])
        rows = [f"{esc(n)} ti deve <b>{eur(v)}</b>" for n, v in owed]
        return {"subject": f"Ti devono ancora {eur(tot)}",
                "body": sec("Saldi aperti", rows) + '<div style="color:#75746f;margin-top:14px">Quando ti restituiscono i soldi premi «Salda» in Spese → Condivise.</div>',
                "push": ", ".join(f"{n} {eur(v)}" for n, v in owed[:4])}
    return None


scheduler = None


def callback_action(data, chat):
    """Un pulsante premuto nella chat dell'assistente. Ritorna dict(toast, edit, new): edit/new = testo o (testo, pulsanti)."""
    parts = (data or "").split("|")
    k = parts[0]
    if k == "z" and len(parts) == 2:
        res = shared_settle(parts[1], None, chat)
        return {"toast": re.sub(r"<[^>]+>", "", res)[:190], "edit": shared_summary()}
    if k == "x" and len(parts) == 2:
        return {"edit": "Ok, non ho eliminato niente." if parts[1] == "no" else delete_tracker(parts[1], chat)}
    if k == "t" and len(parts) == 2:
        return {"new": tracker_view(parts[1])}
    if k == "r" and len(parts) == 3:
        r = mark_row_done(parts[1], parts[2])
        if not r:
            return {"toast": "Questa voce non esiste più", "edit": tracker_view(parts[1])}
        push_undo(chat, ("rowdone", parts[1], parts[2], r[1]))
        return {"toast": f"✅ {r[0][:180]}", "edit": tracker_view(parts[1])}
    if k == "ax" and len(parts) == 2:
        ev = delete_event(parts[1], chat)
        if not ev:
            return {"toast": "Già eliminato", "edit": "Questo appuntamento non c'è più."}
        return {"toast": "Eliminato", "edit": f"🗑 Appuntamento eliminato: <b>{esc(ev.get('title'))}</b>\n<i>Scrivi «annulla» per rimetterlo.</i>"}
    if k == "an" and len(parts) == 2:
        def fn(st):
            ev = next((x for x in st.get("calendar", {}).get("events", []) if x["id"] == parts[1]), None)
            if ev:
                ev["remind"] = ""
            return ev
        ev = store.mutate(fn)
        return {"toast": "Nessun avviso per questo appuntamento" if ev else "Appuntamento non trovato"}
    if k == "td" and len(parts) == 2:
        x = todo_toggle(parts[1])
        if not x:
            return {"toast": "Non c'è più"}
        res = todos_text(today())
        return {"toast": ("✅ " if x["done"] else "↩️ ") + x["text"][:150], "edit": res if res[1] else res[0]}
    if k == "dn":
        return {"toast": "Ok, non ho eliminato niente", "edit": "👌 Non ho eliminato niente."}
    if k == "dx" and len(parts) == 2:
        x = delete_expense(parts[1], chat)
        if not x:
            return {"toast": "Già eliminata", "edit": "Questa spesa non c'è più."}
        _, _, out = month_totals(store.read(), x["date"][:7])
        return {"toast": "Eliminata", "edit": f"🗑 Eliminata: <b>{exp_label(x)}</b>\n"
                f"Uscite di {MESI[int(x['date'][5:7]) - 1].lower()}: {eur(out)}\n<i>Scrivi «annulla» per ripristinarla.</i>"}
    if k in ("rc", "rx") and len(parts) == 2:
        p = PENDING.get(parts[1])
        if not p or p["user"] != CURRENT_USER.get():
            return {"toast": "Scontrino scaduto: rimandami la foto", "edit": "🧾 Scontrino non più disponibile."}
        if k == "rx":
            PENDING.pop(parts[1], None)
            return {"toast": "Scartato", "edit": "🧾 Scontrino scartato."}
        try:
            res = receipt_save(p["draft"], chat)
        except Reply as r:
            return {"edit": r.text}
        PENDING.pop(parts[1], None)
        return {"toast": "Registrata", "edit": res}
    if k == "c" and len(parts) == 2:
        return {"edit": category_menu(parts[1])}
    if k == "cs" and len(parts) == 3:
        it = set_category(parts[1], int(parts[2]) if parts[2].isdigit() else -1)
        if not it:
            return {"toast": "Spesa non trovata"}
        return {"toast": f"🏷 {it['category']}: me lo ricorderò",
                "edit": (f"{'💸' if it['type'] == 'out' else '💰'} <b>{eur(num(it['amount']))}</b> · {esc(it['desc'] or '')}\n"
                         f"🏷 <b>{esc(it['category'])}</b> ✓ <i>categoria corretta, la prossima volta la sceglierò io</i>",
                         expense_kb(it["id"]))}
    if k == "u" and len(parts) == 2:
        def fn(st):
            items = st["expenses"]["items"]
            it = next((x for x in items if x["id"] == parts[1]), None)
            if it:
                items.remove(it)
            return it
        it = store.mutate(fn)
        return {"toast": "Annullata" if it else "Già annullata",
                "edit": f"↩️ Spesa annullata: {eur(num(it['amount']))} · {esc(it.get('desc') or '')}" if it else "↩️ Questa spesa non c'è più."}
    if k == "h" and len(parts) == 3:
        hid, day = parts[1], parts[2]

        def fn(st):
            h = next((x for x in st["habits"]["list"] if x["id"] == hid), None)
            if not h:
                return None
            done = h.setdefault("done", {})
            if done.get(day):
                done.pop(day)
                return h["name"], False
            done[day] = True
            if not h.get("created") or day < h["created"]:
                h["created"] = day
            return h["name"], True
        r = store.mutate(fn)
        if not r:
            return {"toast": "Abitudine non trovata"}
        return {"toast": ("✅ " if r[1] else "↩️ ") + r[0], "edit": habits_menu(date.fromisoformat(day))}
    return {}


# ---------------------------------------------------------------- avvio
class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Su Windows SO_REUSEADDR permetterebbe a due copie di usare la stessa porta
    allow_reuse_address = not WINDOWS

    def server_bind(self):
        if WINDOWS and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    bootstrap()
    if os.getenv("APP_PASSWORD"):
        log.warning("APP_PASSWORD non serve più: ora ogni persona accede con il proprio account")
    try:
        srv = Server((HOST, PORT), Handler)
    except OSError:
        log.error("La porta %d è già in uso: il tracker è forse già avviato (prova ad aprire http://localhost:%d). "
                  "Altrimenti cambia PORT nel file .env.", PORT, PORT)
        sys.exit(1)
    if ENV_FOUND:
        log.info("Configurazione letta da %s", ENV_FILE)
    else:
        extra = " Esiste però «.env.txt»: rinominalo in «.env»." if (BASE / ".env.txt").exists() else ""
        log.info("File .env non trovato in %s: uso i valori predefiniti.%s", BASE, extra)
    if os.getenv("TELEGRAM_TOKEN"):
        log.info("TELEGRAM_TOKEN non serve più: Tacuìn ora avvisa con email e notifiche dell'app. Puoi toglierlo dal file .env")
    log.info("Notifiche push: %s", "attive" if PUSH.ok else "spente (manca la libreria «cryptography»)")
    shown = "localhost" if HOST in ("127.0.0.1", "0.0.0.0") else HOST
    log.info("Tracker avviato: apri http://%s:%d  (Ctrl+C per fermarlo)", shown, PORT)
    if os.getenv("OPEN_BROWSER") == "1":
        import webbrowser
        threading.Timer(1.0, webbrowser.open, [f"http://localhost:{PORT}"]).start()
    log.info("Dati salvati in %s", db.path)
    if iam.count() == 0:
        log.warning("Primo avvio: apri http://localhost:%d da questo PC per creare l'account root", PORT)
    threading.Thread(target=maintenance, daemon=True, name="manutenzione").start()
    global scheduler
    scheduler = notify.Scheduler(db, iam, mailer, build_digest, push_user, lambda u: PUSH.ok and bool(push_subs(u)), app_url,
                                 on_tick=every_minute)
    scheduler.start()
    log.info("Scansione scontrini: %s", f"servizio vision {VISION_URL}" + (" (raggiungibile)" if vision_ok() else
             " (NON raggiungibile: apri Docker Desktop e nella cartella del progetto esegui «docker compose up -d vision»)")
             if VISION_URL else "non attivi (VISION_URL nel file .env)")
    log.info("Email: %s", f"attive tramite {mailer.host}" if mailer.configured else "non configurate (SMTP_HOST nel file .env)")
    STANDBY["status"] = keep_awake()
    log.info("Standby automatico del PC: %s", STANDBY["status"])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("Server fermato.")


if __name__ == "__main__":
    main()
