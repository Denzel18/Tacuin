"""
Database (SQLite) e gestione di identità e accessi: utenti, ruoli, sessioni, registro attività.

Un unico database sul server; ogni dato appartiene a un utente (user_id) e il server
filtra SEMPRE per l'utente collegato. Root e admin gestiscono gli account ma non leggono
i dati personali degli altri.

Solo libreria standard di Python.
"""
import contextvars
import hashlib
import struct
import base64
import hmac
import json
import logging
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("tracker")

ROLES = ("root", "admin", "user")
ROLE_LABEL = {"root": "Root", "admin": "Admin", "user": "Utente"}
STATUSES = ("pending", "active", "disabled", "rejected")

PBKDF2_ITER = 600_000
SESSION_DAYS_REMEMBER = 30
SESSION_HOURS_SHORT = 12
MAX_FAILS = 8          # tentativi falliti...
FAIL_WINDOW = 15 * 60  # ...in 15 minuti, poi blocco temporaneo

# utente "corrente" per la logica del bot e della chat (impostato a ogni richiesta)
CURRENT_USER = contextvars.ContextVar("current_user", default=None)


def now():
    return datetime.now(timezone.utc)


def ts():
    return now().strftime("%Y-%m-%dT%H:%M:%SZ")


def uid():
    return uuid.uuid4().hex[:12]


class TotpNeeded(Exception):
    """Password giusta, ma l'account ha la verifica in due passaggi: serve il codice dell'app."""


def _b32(secret):
    return base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))


def totp_code(secret, counter):
    """Codice a 6 cifre (RFC 6238: HMAC-SHA1, passi di 30 secondi), lo stesso di Google Authenticator, Authy, 1Password..."""
    d = hmac.new(_b32(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    o = d[-1] & 0x0F
    return f"{(struct.unpack('>I', d[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- password
def hash_password(pw):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITER).hex()
    return f"pbkdf2_sha256${PBKDF2_ITER}${salt}${h}"


def check_password(pw, stored):  # noqa: E302
    try:
        algo, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(it)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


def password_problem(pw):
    if not pw or len(pw) < 8:
        return "La password deve avere almeno 8 caratteri."
    if pw.isdigit() or pw.isalpha():
        return "Usa lettere e numeri insieme."
    return None


def valid_email(e):
    import re
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e or ""))


_DUMMY_HASH = hash_password(secrets.token_hex(16))  # per rispondere con gli stessi tempi a email inesistenti


# nome utente pubblico (es. @tonyr): serve agli altri utenti per trovarti; l'email resta nascosta
RESERVED_HANDLES = {"admin", "administrator", "amministratore", "root", "tacuin", "tacuìn", "support", "supporto", "assistenza",
                    "help", "aiuto", "privacy", "security", "sicurezza", "staff", "team", "info", "noreply", "no-reply", "system",
                    "sistema", "moderatore", "moderator", "official", "ufficiale", "api", "www", "mail", "postmaster", "abuse"}


def norm_handle(h):
    return str(h or "").strip().lstrip("@").lower()


def handle_problem(h):
    import re
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9._]{1,22}[a-z0-9])", h or ""):
        return "Nome utente: da 3 a 24 caratteri tra lettere minuscole, numeri, punto e trattino basso (non all'inizio o alla fine)."
    if ".." in h or "__" in h:
        return "Nome utente: niente punti o trattini bassi doppi."
    if h in RESERVED_HANDLES or h.replace(".", "").replace("_", "") in RESERVED_HANDLES:
        return "Questo nome utente è riservato: scegline un altro."
    return None


# ---------------------------------------------------------------- database
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'pending',
  created TEXT NOT NULL,
  last_login TEXT,
  telegram_id INTEGER UNIQUE,
  settings TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created TEXT NOT NULL,
  expires REAL NOT NULL,
  last_seen REAL NOT NULL,
  agent TEXT
);
CREATE TABLE IF NOT EXISTS docs (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  rev INTEGER NOT NULL,
  state TEXT NOT NULL,
  updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  target_id TEXT,
  detail TEXT,
  ip TEXT
);
CREATE TABLE IF NOT EXISTS login_fails (
  key TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
  token_hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL,          -- 'reset' | 'telegram'
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires REAL NOT NULL,
  extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_fails_key ON login_fails(key, at);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
"""


class DB:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(users)").fetchall()}
        if "handle" not in cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN handle TEXT")
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_handle ON users(handle COLLATE NOCASE) WHERE handle IS NOT NULL")

    def q(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def run(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args)

    def tx(self):
        return _Tx(self)

    def backup_daily(self, bdir):
        bdir = Path(bdir)
        bdir.mkdir(parents=True, exist_ok=True)
        target = bdir / f"tracker-{date.today().isoformat()}.db"
        if target.exists():
            return
        with self.lock:
            dst = sqlite3.connect(target)
            self.conn.backup(dst)
            dst.close()
        for old in sorted(bdir.glob("tracker-*.db"))[:-30]:
            old.unlink()


class _Tx:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, et, ev, tb):
        try:
            self.db.conn.execute("ROLLBACK" if et else "COMMIT")
        finally:
            self.db.lock.release()
        return False


# ---------------------------------------------------------------- dati dell'utente
class Store:
    """
    Lo "stato" (abitudini, spese, investimenti, tracker) di UN utente, con numero di revisione.
    Senza user_id esplicito usa l'utente corrente (CURRENT_USER): così la logica del bot
    lavora sempre e solo sui dati di chi sta scrivendo.
    """

    def __init__(self, db, migrate):
        self.db = db
        self.migrate = migrate

    def _uid(self, user_id):
        u = user_id or CURRENT_USER.get()
        if not u:
            raise RuntimeError("nessun utente corrente")
        return u

    def snapshot(self, user_id=None):
        r = self.db.one("SELECT rev, state FROM docs WHERE user_id=?", (self._uid(user_id),))
        return (r["rev"], json.loads(r["state"])) if r else (0, None)

    def read(self, user_id=None):
        rev, st = self.snapshot(user_id)
        if st is None:
            return self.mutate(lambda s: None, user_id) or self.snapshot(user_id)[1]
        return self.migrate(st)

    def replace(self, state, base_rev, user_id=None):
        u = self._uid(user_id)
        with self.db.tx() as c:
            r = c.execute("SELECT rev, state FROM docs WHERE user_id=?", (u,)).fetchone()
            if r and base_rev != r["rev"]:
                return False, r["rev"], json.loads(r["state"])
            rev = (r["rev"] if r else 0) + 1
            c.execute("INSERT INTO docs(user_id, rev, state, updated) VALUES(?,?,?,?) "
                      "ON CONFLICT(user_id) DO UPDATE SET rev=excluded.rev, state=excluded.state, updated=excluded.updated",
                      (u, rev, json.dumps(self.migrate(state), ensure_ascii=False), ts()))
            return True, rev, None

    def mutate(self, fn, user_id=None):
        u = self._uid(user_id)
        with self.db.tx() as c:
            r = c.execute("SELECT rev, state FROM docs WHERE user_id=?", (u,)).fetchone()
            st = self.migrate(json.loads(r["state"]) if r else None)
            result = fn(st)
            new_rev = (r["rev"] if r else 0) + 1
            c.execute("INSERT INTO docs(user_id, rev, state, updated) VALUES(?,?,?,?) "
                      "ON CONFLICT(user_id) DO UPDATE SET rev=excluded.rev, state=excluded.state, updated=excluded.updated",
                      (u, new_rev, json.dumps(st, ensure_ascii=False), ts()))
            return result


# ---------------------------------------------------------------- identità e accessi
class IAM:
    def __init__(self, db):
        self.db = db

    # --- utenti
    def user(self, user_id):
        r = self.db.one("SELECT * FROM users WHERE id=?", (user_id,))
        return dict(r) if r else None

    def by_email(self, email):
        r = self.db.one("SELECT * FROM users WHERE email=?", ((email or "").strip(),))
        return dict(r) if r else None

    def count(self):
        return self.db.one("SELECT COUNT(*) n FROM users")["n"]

    def by_handle(self, handle):
        r = self.db.one("SELECT * FROM users WHERE handle=? COLLATE NOCASE", (norm_handle(handle),))
        return dict(r) if r else None

    def handle_free(self, handle, except_id=None):
        u = self.by_handle(handle)
        return not u or u["id"] == except_id

    def set_handle(self, user_id, handle):
        h = norm_handle(handle)
        p = handle_problem(h)
        if p:
            raise ValueError(p)
        if not self.handle_free(h, user_id):
            raise ValueError("Questo nome utente è già preso.")
        old = (self.user(user_id) or {}).get("handle")
        if old == h:
            return h
        try:
            self.db.run("UPDATE users SET handle=? WHERE id=?", (h, user_id))
        except Exception:
            raise ValueError("Questo nome utente è già preso.")
        self.audit(user_id, "user.handle", user_id, f"@{old or '-'} → @{h}")
        return h

    def search_handles(self, q, exclude_id, limit=5):
        """Solo utenti attivi e trovabili; restituisce soltanto nome e nome utente (mai email, ruolo o date)."""
        import re
        q = re.sub(r"[^a-z0-9._]", "", norm_handle(q))[:24]
        if len(q) < 3:
            return []
        like = q.replace("\\", "").replace("%", "").replace("_", "\\_") + "%"
        out = []
        for r in self.db.q("SELECT id, name, handle, settings FROM users WHERE status='active' AND handle IS NOT NULL "
                           "AND handle LIKE ? ESCAPE '\\' AND id<>? ORDER BY length(handle), handle LIMIT 20", (like, exclude_id)):
            try:
                hidden = bool(json.loads(r["settings"] or "{}").get("hidden"))
            except ValueError:
                hidden = False
            if not hidden or r["handle"] == q:  # chi non vuole farsi trovare compare solo con il nome utente esatto
                out.append({"name": r["name"], "handle": r["handle"]})
            if len(out) >= limit:
                break
        return out

    def list_users(self):
        return [dict(r) for r in self.db.q("SELECT * FROM users ORDER BY "
                                            "CASE status WHEN 'pending' THEN 0 ELSE 1 END, "
                                            "CASE role WHEN 'root' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, created")]

    def admins(self):
        return [dict(r) for r in self.db.q("SELECT * FROM users WHERE role IN ('root','admin') AND status='active'")]

    def create_user(self, email, name, password, role="user", status="pending", actor=None, ip=None, handle=None):
        email, name = email.strip(), name.strip()
        if handle is not None:
            handle = norm_handle(handle)
            hp = handle_problem(handle)
            if hp:
                raise ValueError(hp)
            if not self.handle_free(handle):
                raise ValueError("Questo nome utente è già preso.")
        if not valid_email(email):
            raise ValueError("Email non valida.")
        if not name:
            raise ValueError("Scrivi il tuo nome.")
        p = password_problem(password)
        if p:
            raise ValueError(p)
        if self.by_email(email):
            raise ValueError("Esiste già un account con questa email.")
        u = {"id": uid(), "email": email, "name": name[:60], "pw_hash": hash_password(password),
             "role": role, "status": status, "created": ts()}
        self.db.run("INSERT INTO users(id,email,name,pw_hash,role,status,created,handle) VALUES(?,?,?,?,?,?,?,?)",
                    (u["id"], u["email"], u["name"], u["pw_hash"], role, status, u["created"], handle or None))
        self.audit(actor or u["id"], "user.create" if actor else "user.register", u["id"],
                   f"{email} · ruolo {role} · stato {status}", ip)
        return self.user(u["id"])

    def public(self, u):
        if not u:
            return None
        s = {k: v for k, v in json.loads(u.get("settings") or "{}").items() if not k.startswith("_")}  # "_…" = segreti (es. chiave Garmin)
        return {"id": u["id"], "email": u["email"], "name": u["name"], "handle": u.get("handle"), "role": u["role"],
                "status": u["status"], "created": u["created"], "last_login": u.get("last_login"),
                "settings": s}

    def update_profile(self, user_id, name=None, email=None):
        u = self.user(user_id)
        if email is not None and email.strip().lower() != u["email"].lower():
            if not valid_email(email):
                raise ValueError("Email non valida.")
            if self.by_email(email):
                raise ValueError("Esiste già un account con questa email.")
            self.db.run("UPDATE users SET email=? WHERE id=?", (email.strip(), user_id))
            self.audit(user_id, "user.email", user_id, f"{u['email']} → {email.strip()}")
        if name is not None and name.strip():
            self.db.run("UPDATE users SET name=? WHERE id=?", (name.strip()[:60], user_id))

    def settings(self, user_id):
        u = self.user(user_id)
        return json.loads(u.get("settings") or "{}") if u else {}

    def set_settings(self, user_id, patch):
        with self.db.tx() as c:
            r = c.execute("SELECT settings FROM users WHERE id=?", (user_id,)).fetchone()
            s = json.loads(r["settings"] or "{}")
            s.update(patch)
            c.execute("UPDATE users SET settings=? WHERE id=?", (json.dumps(s, ensure_ascii=False), user_id))
            return s

    def change_password(self, user_id, old, new, check_old=True):
        u = self.user(user_id)
        if check_old and not check_password(old or "", u["pw_hash"]):
            raise ValueError("La password attuale non è corretta.")
        p = password_problem(new)
        if p:
            raise ValueError(p)
        self.db.run("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(new), user_id))
        self.audit(user_id, "user.password", user_id)

    # --- accesso
    def _fails(self, key):
        self.db.run("DELETE FROM login_fails WHERE at < ?", (time.time() - FAIL_WINDOW,))
        return self.db.one("SELECT COUNT(*) n FROM login_fails WHERE key=?", (key,))["n"]

    # --- verifica in due passaggi
    def totp_on(self, user_id):
        return bool((self.settings(user_id).get("_totp") or {}).get("on"))

    def totp_check(self, user_id, code):
        """Codice dell'app (finestra ±30 s, mai lo stesso due volte) o uno dei codici di recupero (usa e getta)."""
        t = self.settings(user_id).get("_totp") or {}
        code = (code or "").strip().replace(" ", "")
        if not t.get("secret") or not code:
            return False
        if len(code) == 6 and code.isdigit():
            now = int(time.time() // 30)
            for c in (now - 1, now, now + 1):
                if c > (t.get("last") or 0) and hmac.compare_digest(totp_code(t["secret"], c), code):
                    self.set_settings(user_id, {"_totp": {**t, "last": c}})
                    return True
            return False
        h = sha256(code.lower().replace("-", ""))
        if h in (t.get("rc") or []):
            self.set_settings(user_id, {"_totp": {**t, "rc": [x for x in t["rc"] if x != h]}})
            self.audit(user_id, "user.totp_recovery", user_id, f"rimasti {len(t['rc']) - 1}")
            return True
        return False

    def totp_start(self, user_id):
        secret = new_totp_secret()
        t = self.settings(user_id).get("_totp") or {}
        self.set_settings(user_id, {"_totp": {**t, "pending": secret}})
        return secret

    def totp_confirm(self, user_id, code):
        t = self.settings(user_id).get("_totp") or {}
        sec = t.get("pending")
        now = int(time.time() // 30)
        if not sec or not any(hmac.compare_digest(totp_code(sec, c), (code or "").strip()) for c in (now - 1, now, now + 1)):
            raise ValueError("Codice non valido: controlla l'ora del telefono e riprova con il codice nuovo.")
        codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(8)]
        self.set_settings(user_id, {"_totp": {"on": True, "secret": sec, "last": now, "rc": [sha256(c.replace("-", "")) for c in codes]}})
        self.audit(user_id, "user.totp_on", user_id)
        return codes

    def totp_disable(self, user_id, actor_id=None):
        self.set_settings(user_id, {"_totp": {}})
        self.audit(actor_id or user_id, "user.totp_off", user_id)

    def login(self, email, password, ip, agent, remember=True, totp=None):
        """Ritorna (token, utente) oppure solleva ValueError con un messaggio per l'utente.
        Con la verifica in due passaggi attiva e senza codice solleva TotpNeeded (nessuna sessione creata)."""
        em = (email or '').strip().lower()
        # il blocco vale per email+rete e per rete: chi prova password sbagliate non blocca l'accesso del vero titolare
        keys = [f"ei:{em}|{ip}", f"i:{ip}", f"e:{em}"]
        if self._fails(keys[0]) >= MAX_FAILS or self._fails(keys[1]) >= MAX_FAILS or self._fails(keys[2]) >= MAX_FAILS * 10:
            raise PermissionError("Troppi tentativi falliti. Riprova tra qualche minuto.")
        u = self.by_email(email)
        ok = check_password(password or "", u["pw_hash"] if u else _DUMMY_HASH)  # stessi tempi anche se l'email non esiste
        if not u or not ok:
            for k in keys:
                self.db.run("INSERT INTO login_fails(key, at) VALUES(?,?)", (k, time.time()))
            self.audit(u["id"] if u else None, "login.fail", u["id"] if u else None, (email or "")[:80], ip)
            raise ValueError("Email o password non corrette.")
        if u["status"] == "pending":
            raise ValueError("Il tuo account è in attesa di approvazione da parte dell'amministratore.")
        if u["status"] in ("disabled", "rejected"):
            raise ValueError("Questo account non è attivo. Contatta l'amministratore.")
        if self.totp_on(u["id"]):
            if not totp:
                raise TotpNeeded("Inserisci il codice dell'app di autenticazione.")
            if not self.totp_check(u["id"], totp):
                for k in keys:
                    self.db.run("INSERT INTO login_fails(key, at) VALUES(?,?)", (k, time.time()))
                self.audit(u["id"], "login.totp_fail", u["id"], None, ip)
                raise ValueError("Codice non valido o già usato.")
        token = secrets.token_urlsafe(32)
        life = SESSION_DAYS_REMEMBER * 86400 if remember else SESSION_HOURS_SHORT * 3600
        self.db.run("INSERT INTO sessions(token_hash,user_id,created,expires,last_seen,agent) VALUES(?,?,?,?,?,?)",
                    (sha256(token), u["id"], ts(), time.time() + life, time.time(), (agent or "")[:200]))
        self.db.run("UPDATE users SET last_login=? WHERE id=?", (ts(), u["id"]))
        self.db.run("DELETE FROM login_fails WHERE key IN (?,?)", (keys[0], keys[2]))
        self.audit(u["id"], "login", u["id"], None, ip)
        return token, self.user(u["id"]), life

    def session_user(self, token):
        if not token:
            return None
        th = sha256(token)
        r = self.db.one("SELECT s.expires, u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (th,))
        if not r:
            return None
        if r["expires"] < time.time() or r["status"] != "active":
            self.db.run("DELETE FROM sessions WHERE token_hash=?", (th,))
            return None
        self.db.run("UPDATE sessions SET last_seen=? WHERE token_hash=?", (time.time(), th))
        u = dict(r)
        u.pop("expires", None)
        return u

    def logout(self, token):
        if token:
            self.db.run("DELETE FROM sessions WHERE token_hash=?", (sha256(token),))

    def logout_others(self, user_id, keep_token):
        self.db.run("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user_id, sha256(keep_token or "")))

    def peek_token(self, kind, t):
        """Come use_token ma restituisce (user_id, extra) e consuma il token."""
        th = sha256(t or "")
        r = self.db.one("SELECT * FROM tokens WHERE token_hash=? AND kind=?", (th, kind))
        if not r:
            return None, None
        self.db.run("DELETE FROM tokens WHERE token_hash=?", (th,))
        return (r["user_id"], r["extra"]) if r["expires"] >= time.time() else (None, None)

    def logout_all(self, user_id, actor=None, ip=None):
        self.db.run("DELETE FROM sessions WHERE user_id=?", (user_id,))
        self.audit(actor or user_id, "sessions.revoke", user_id, None, ip)

    # --- token monouso (reimpostazione password)
    def make_token(self, kind, user_id, minutes, extra=None, code=None):
        t = code or secrets.token_urlsafe(24)
        self.db.run("DELETE FROM tokens WHERE kind=? AND user_id=?", (kind, user_id))
        self.db.run("INSERT INTO tokens(token_hash,kind,user_id,expires,extra) VALUES(?,?,?,?,?)",
                    (sha256(t), kind, user_id, time.time() + minutes * 60, extra))
        return t

    def use_token(self, kind, t):
        th = sha256(t or "")
        r = self.db.one("SELECT * FROM tokens WHERE token_hash=? AND kind=?", (th, kind))
        if not r:
            return None
        self.db.run("DELETE FROM tokens WHERE token_hash=?", (th,))
        return r["user_id"] if r["expires"] >= time.time() else None

    def admin_action(self, actor, target_id, action, value=None, ip=None):
        """Azioni di root/admin su un account. Ritorna un messaggio; solleva PermissionError/ValueError."""
        if actor["role"] not in ("root", "admin"):
            raise PermissionError("Non hai i permessi per questa operazione.")
        t = self.user(target_id)
        if not t:
            raise ValueError("Utente non trovato.")
        if t["role"] == "root" and action not in ():
            raise PermissionError("L'account root non si può modificare da qui.")
        if t["role"] == "admin" and actor["role"] != "root":
            raise PermissionError("Solo root può gestire gli amministratori.")
        if t["id"] == actor["id"]:
            raise PermissionError("Non puoi modificare il tuo stesso account da qui: usa il Profilo.")

        if action == "approve":
            role = value if value in ("user", "admin") else "user"
            if role == "admin" and actor["role"] != "root":
                raise PermissionError("Solo root può creare amministratori.")
            self.db.run("UPDATE users SET status='active', role=? WHERE id=?", (role, target_id))
            msg = f"{t['name']} approvato come {ROLE_LABEL[role]}."
        elif action == "reject":
            if t["status"] != "pending":
                raise ValueError("Si possono rifiutare solo le richieste in attesa.")
            self.db.run("UPDATE users SET status='rejected' WHERE id=?", (target_id,))
            msg = f"Richiesta di {t['name']} rifiutata."
        elif action == "disable":
            self.db.run("UPDATE users SET status='disabled' WHERE id=?", (target_id,))
            self.db.run("DELETE FROM sessions WHERE user_id=?", (target_id,))
            msg = f"{t['name']} disattivato."
        elif action == "enable":
            self.db.run("UPDATE users SET status='active' WHERE id=?", (target_id,))
            msg = f"{t['name']} riattivato."
        elif action == "role":
            if value not in ("user", "admin"):
                raise ValueError("Ruolo non valido.")
            if actor["role"] != "root":
                raise PermissionError("Solo root può cambiare i ruoli.")
            self.db.run("UPDATE users SET role=? WHERE id=?", (value, target_id))
            msg = f"{t['name']} ora è {ROLE_LABEL[value]}."
        elif action == "totp_reset":  # telefono perso: l'utente rientra con la sola password e la riattiva
            self.totp_disable(target_id, actor["id"])
            self.db.run("DELETE FROM sessions WHERE user_id=?", (target_id,))
            msg = f"Verifica in due passaggi disattivata per {t['name']}: al prossimo accesso basta la password."
        elif action in ("invites_off", "invites_on"):
            self.set_settings(target_id, {"_no_invites": action == "invites_off"})
            msg = f"Inviti {'disattivati' if action == 'invites_off' else 'riattivati'} per {t['name']}."
        elif action == "logout_all":
            self.db.run("DELETE FROM sessions WHERE user_id=?", (target_id,))
            msg = f"Tutte le sessioni di {t['name']} sono state chiuse."
        elif action == "reset_link":
            tok = self.make_token("reset", target_id, 60 * 24)
            self.audit(actor["id"], "admin.reset_link", target_id, None, ip)
            return {"message": f"Link valido 24 ore per {t['name']}.", "token": tok}
        elif action == "delete":
            self.db.run("DELETE FROM users WHERE id=?", (target_id,))
            self.db.run("DELETE FROM invites WHERE owner_id=?", (target_id,))  # inviti creati da questo account
            msg = f"Account e dati di {t['name']} eliminati."
        else:
            raise ValueError("Azione sconosciuta.")
        self.audit(actor["id"], f"admin.{action}", target_id, f"{t['email']}{' · ' + value if value else ''}", ip)
        return {"message": msg}

    # --- registro attività
    def audit(self, actor_id, action, target_id=None, detail=None, ip=None):
        try:
            self.db.run("INSERT INTO audit(ts,actor_id,action,target_id,detail,ip) VALUES(?,?,?,?,?,?)",
                        (ts(), actor_id, action, target_id, detail, ip))
        except Exception:
            log.exception("audit")

    def audit_list(self, limit=200):
        rows = self.db.q("SELECT a.*, ua.name actor_name, ut.name target_name FROM audit a "
                         "LEFT JOIN users ua ON ua.id=a.actor_id LEFT JOIN users ut ON ut.id=a.target_id "
                         "ORDER BY a.id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def stats(self):
        r = {s: 0 for s in STATUSES}
        for row in self.db.q("SELECT status, COUNT(*) n FROM users GROUP BY status"):
            r[row["status"]] = row["n"]
        r["fails24"] = self.db.one("SELECT COUNT(*) n FROM audit WHERE action='login.fail' AND ts >= ?",
                                   ((now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),))["n"]
        return r
