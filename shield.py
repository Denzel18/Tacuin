"""
Scudo: protezione da raffiche di richieste (DDoS) e da chi cerca punti deboli.

Per ogni indirizzo IP (quello vero, che Caddy mette in X-Forwarded-For):
- un «secchio» di richieste: se ne arrivano troppe di fila, le altre ricevono 429 per qualche secondo;
- un tetto alle richieste contemporanee;
- dei punti per i comportamenti sospetti: percorsi da scanner (/.env, /wp-login.php, /.git…), richieste
  malformate, accessi sbagliati, limiti superati, pagine inesistenti. Troppi punti in 10 minuti = blocco
  temporaneo (15 minuti, poi 6 ore, poi 24 ore se ricomincia).
I blocchi finiscono nel Registro attività e il root riceve un avviso (al massimo uno all'ora, con il riepilogo).
Tutto in memoria: un riavvio azzera i contatori (va bene, i blocchi sono brevi).
"""
import ipaddress
import re
import threading
import time
from collections import deque

# percorsi che solo uno scanner chiede a un'app come questa
SCAN_RE = re.compile(r"(?i)(^/\.(env|git|svn|hg|aws|ssh|htaccess|htpasswd|DS_Store|vscode|idea)\b|/wp-(admin|login|content|includes)|"
                     r"/xmlrpc\.php|\.php\d?$|\.aspx?$|\.jsp$|\.cgi$|/cgi-bin/|/phpmyadmin|/pma\b|/adminer|/actuator\b|"
                     r"/server-status|/console\b|/vendor/phpunit|/boaform|/HNAP1|/\.well-known/security\.txt\.php|/owa/|"
                     r"/(config|settings|database|db|backup|dump)\.(json|yml|yaml|sql|bak|zip|tar|gz)$|/etc/passwd|\.\./|%2e%2e)")

POINTS = {"scan": 5, "bad": 2, "auth": 1, "limit": 1, "missing": 1, "flood": 3}
WHY = {"scan": "percorsi da scanner", "bad": "richieste malformate", "auth": "accessi sbagliati", "limit": "limiti superati",
       "missing": "pagine inesistenti", "flood": "raffiche di richieste"}


INTERNAL = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                                              "::1/128", "fc00::/7", "fe80::/10")]


def exempt(ip):
    """Mai bloccare la macchina stessa o la rete interna (Docker, healthcheck, Caddy)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return any(a in n for n in INTERNAL)


class Shield:
    RATE = 8.0          # richieste al secondo in media per IP
    BURST = 240         # quante di fila (una pagina carica ~40 file)
    PARALLEL = 32       # richieste contemporanee per IP
    WINDOW = 600        # i punti contano per 10 minuti
    THRESHOLD = 20      # punti per un blocco
    BANS = (15 * 60, 6 * 3600, 24 * 3600)
    MAX_IPS = 50_000

    def __init__(self, on_ban=None, now=time.monotonic):
        self.lock = threading.Lock()
        self.now = now
        self.bucket = {}     # ip -> [token, ultimo aggiornamento]
        self.inflight = {}   # ip -> richieste in corso
        self.points = {}     # ip -> deque[(t, punti, motivo, dettaglio)]
        self.banned = {}     # ip -> (fino a, motivo)
        self.history = {}    # ip -> [istanti dei blocchi nelle ultime 24 ore]
        self.on_ban = on_ban
        self.stats = {"blocked": 0, "limited": 0}

    # ------------------------------------------------------------ prima della richiesta
    def check(self, ip):
        """→ None se la richiesta può passare, altrimenti (codice, secondi da attendere)."""
        if exempt(ip):
            return None
        t = self.now()
        with self.lock:
            b = self.banned.get(ip)
            if b:
                if b[0] > t:
                    self.stats["blocked"] += 1
                    return 429, int(b[0] - t) + 1
                del self.banned[ip]
            tok, last = self.bucket.get(ip, (self.BURST, t))
            tok = min(self.BURST, tok + (t - last) * self.RATE)
            if tok < 1:
                self.bucket[ip] = (tok, t)
                self.stats["limited"] += 1
                flood = True
            else:
                self.bucket[ip] = (tok - 1, t)
                flood = False
            if not flood and self.inflight.get(ip, 0) >= self.PARALLEL:
                self.stats["limited"] += 1
                flood = True
            if not flood:
                self.inflight[ip] = self.inflight.get(ip, 0) + 1
            self._gc(t)
        if flood:
            self.strike(ip, "flood", "")
            return 429, 5
        return None

    def done(self, ip):
        if exempt(ip):
            return
        with self.lock:
            n = self.inflight.get(ip, 0) - 1
            if n > 0:
                self.inflight[ip] = n
            else:
                self.inflight.pop(ip, None)

    # ------------------------------------------------------------ comportamenti sospetti
    def strike(self, ip, kind, detail=""):
        if exempt(ip) or kind not in POINTS:
            return False
        t = self.now()
        ban = None
        with self.lock:
            if ip in self.banned and self.banned[ip][0] > t:
                return True
            q = self.points.setdefault(ip, deque(maxlen=400))
            q.append((t, POINTS[kind], kind, str(detail)[:120]))
            while q and q[0][0] < t - self.WINDOW:
                q.popleft()
            total = sum(p for _, p, _, _ in q)
            if total >= self.THRESHOLD:
                h = [x for x in self.history.get(ip, []) if x > t - 86400]
                dur = self.BANS[min(len(h), len(self.BANS) - 1)]
                h.append(t)
                self.history[ip] = h
                kinds = {}
                for _, p, k, _ in q:
                    kinds[k] = kinds.get(k, 0) + 1
                main = max(kinds, key=lambda k: kinds[k] * POINTS[k])
                examples = [d for _, _, k, d in q if k == main and d][-3:]
                self.banned[ip] = (t + dur, main)
                self.points.pop(ip, None)
                ban = {"ip": ip, "seconds": dur, "why": WHY[main], "count": kinds[main], "examples": examples, "repeat": len(h)}
        if ban and self.on_ban:
            try:
                self.on_ban(ban)
            except Exception:
                pass
        return bool(ban)

    def path_is_scan(self, path):
        return bool(SCAN_RE.search(path or ""))

    def snapshot(self):
        t = self.now()
        with self.lock:
            return {"banned": [{"ip": ip, "left": int(u - t), "why": WHY.get(w, w)} for ip, (u, w) in self.banned.items() if u > t],
                    **self.stats}

    def _gc(self, t):
        if len(self.bucket) > self.MAX_IPS:
            for ip in [ip for ip, (_, last) in self.bucket.items() if t - last > 120][: self.MAX_IPS // 2]:
                self.bucket.pop(ip, None)
        if len(self.points) > self.MAX_IPS:
            for ip in [ip for ip, q in self.points.items() if not q or q[-1][0] < t - self.WINDOW]:
                self.points.pop(ip, None)
        if len(self.banned) > self.MAX_IPS:
            for ip in [ip for ip, (u, _) in self.banned.items() if u < t]:
                self.banned.pop(ip, None)
