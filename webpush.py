"""
Notifiche push del browser (Web Push, RFC 8030 / 8291 / 8292), senza servizi o account di terzi.

Tacuìn installato sul telefono (o aperto in Chrome, Edge, Firefox, Safari) si iscrive alle notifiche: il browser
ci dà un indirizzo del suo servizio push (Apple, Google, Mozilla, Microsoft) e due chiavi. Per avvisare l'utente
cifriamo il messaggio con quelle chiavi (solo il suo dispositivo può leggerlo) e lo consegniamo a quell'indirizzo,
firmato con la nostra chiave VAPID (generata qui al primo avvio, resta nella cartella dei dati).

Serve la libreria «cryptography» (curve ellittiche P-256 e AES-GCM): è nell'immagine Docker.
Senza, le notifiche push sono semplicemente spente e tutto il resto funziona.
"""
import base64
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("tracker")

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    AVAILABLE = True
except ImportError:  # pragma: no cover
    AVAILABLE = False

# Solo i servizi push dei browser: Tacuìn non manda richieste a indirizzi scelti da altri (niente SSRF).
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com",
              "push.services.mozilla.com", "web.push.apple.com", "push.apple.com", "notify.windows.com")


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def b64d(s):
    s = str(s or "")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Gone(Exception):
    """L'iscrizione non esiste più (app disinstallata, permesso tolto): va cancellata."""


def allowed_endpoint(endpoint, extra_hosts=()):
    try:
        u = urllib.parse.urlparse(str(endpoint))
    except Exception:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" and not (host in extra_hosts and u.scheme == "http"):
        return False
    return any(host == h or host.endswith("." + h) for h in PUSH_HOSTS + tuple(extra_hosts))


def encrypt(p256dh, auth, payload, _as_private=None, _salt=None):
    """Cifra il messaggio per un'iscrizione (RFC 8291, aes128gcm). _as_private/_salt solo per i test."""
    ua_pub = b64d(p256dh)
    secret = b64d(auth)
    if len(ua_pub) != 65 or len(secret) < 16:
        raise ValueError("chiavi dell'iscrizione non valide")
    as_priv = _as_private or ec.generate_private_key(ec.SECP256R1())
    as_pub = as_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    shared = as_priv.exchange(ec.ECDH(), ua_key)
    ikm = HKDF(hashes.SHA256(), 32, salt=secret, info=b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    salt = _salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_pub)) + as_pub + body


class WebPush:
    def __init__(self, key_file, subject, ssl_ctx=None, extra_hosts=()):
        self.ok = AVAILABLE
        self.subject = subject
        self.ssl_ctx = ssl_ctx
        self.extra_hosts = tuple(extra_hosts)  # solo per i test locali
        self.public_key = ""
        if not self.ok:
            log.warning("Notifiche push spente: manca la libreria «cryptography» (pip install cryptography)")
            return
        try:
            self._key = self._load_or_create(key_file)
            self.public_key = b64u(self._key.public_key().public_bytes(serialization.Encoding.X962,
                                                                       serialization.PublicFormat.UncompressedPoint))
        except Exception as e:
            log.error("Notifiche push spente: chiave VAPID non disponibile (%s)", e)
            self.ok = False

    @staticmethod
    def _load_or_create(path):
        try:
            return serialization.load_pem_private_key(path.read_bytes(), password=None)
        except FileNotFoundError:
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(pem)
            log.info("Creata la chiave per le notifiche push (%s)", path.name)
            return key

    def vapid_header(self, endpoint):
        u = urllib.parse.urlparse(endpoint)
        head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                                  "sub": self.subject}, separators=(",", ":")).encode())
        der = self._key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        token = f"{head}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        return f"vapid t={token}, k={self.public_key}"

    def send(self, sub, data, ttl=24 * 3600, urgency="normal", topic=None):
        """Consegna un messaggio (dict → JSON) a un'iscrizione. Solleva Gone se l'iscrizione non vale più."""
        if not self.ok:
            raise RuntimeError("notifiche push non disponibili")
        endpoint = sub.get("endpoint") or ""
        if not allowed_endpoint(endpoint, self.extra_hosts):
            raise Gone("servizio push non riconosciuto")
        keys = sub.get("keys") or {}
        body = encrypt(keys.get("p256dh"), keys.get("auth"), json.dumps(data, ensure_ascii=False).encode("utf-8")[:3000])
        headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(int(ttl)),
                   "Urgency": urgency, "Authorization": self.vapid_header(endpoint)}
        if topic:
            headers["Topic"] = "".join(c for c in topic if c.isalnum() or c in "-_")[:32]
        req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15, context=self.ssl_ctx) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise Gone(f"iscrizione scaduta ({e.code})")
            if e.code in (400, 403) and b"VapidPkHashMismatch" in (e.read() or b""):
                raise Gone("iscrizione fatta con un'altra chiave")
            raise
