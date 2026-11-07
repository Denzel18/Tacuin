"""
Archiviazione dei file (per ora: le sessioni di test). Un'unica interfaccia, più destinazioni:

    store = crea_archivio(config)          # config = dict letto dal .env
    dove = store.put(Path("sessione.zip"), "sessioni/2026/11/sessione.zip")

Destinazioni (ARCHIVE_BACKEND nel .env):
- local (predefinita): copia in una cartella (ARCHIVE_DIR, predefinita logs/archivio)
- s3: qualsiasi servizio compatibile S3 (AWS, MinIO, Cloudflare R2, Backblaze B2, Wasabi...).
      Solo libreria standard: firma AWS Signature V4 scritta qui, niente boto3 da installare.
- gcs: Google Cloud Storage dalla VM di Google, SENZA chiavi: usa l'identità della VM (account di servizio)
      chiedendo il token al metadata server. Basta il ruolo «Storage Object Creator» sul bucket.

Per aggiungere un'altra destinazione: una classe con .nome e .put(percorso, chiave) e una riga in crea_archivio().
"""
import datetime
import hashlib
import hmac
import json
import os
import shutil
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class ArchivioError(RuntimeError):
    pass


class ArchivioLocale:
    nome = "local"

    def __init__(self, cartella):
        self.root = Path(cartella)

    def put(self, percorso, chiave):
        dest = self.root / chiave
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copyfile(percorso, tmp)
        tmp.replace(dest)
        return str(dest.resolve())


class ArchivioS3:
    """Caricamento con una PUT firmata (Signature V4, URL «path-style»: va bene per tutti i compatibili S3)."""
    nome = "s3"

    def __init__(self, endpoint, bucket, access_key, secret_key, region="us-east-1", prefisso="", ssl_ctx=None):
        if not (endpoint and bucket and access_key and secret_key):
            raise ArchivioError("S3: servono S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY e S3_SECRET_KEY nel file .env")
        self.endpoint = endpoint.rstrip("/")
        if "://" not in self.endpoint:
            self.endpoint = "https://" + self.endpoint
        self.bucket, self.ak, self.sk = bucket, access_key, secret_key
        self.region = region or "us-east-1"
        self.prefisso = prefisso.strip("/")
        self.ctx = ssl_ctx or ssl.create_default_context()

    def _firma(self, metodo, url, headers, payload_hash, adesso):
        u = urllib.parse.urlsplit(url)
        data, ora = adesso.strftime("%Y%m%d"), adesso.strftime("%Y%m%dT%H%M%SZ")
        headers = {**{k.lower(): v for k, v in headers.items()}, "host": u.netloc, "x-amz-date": ora, "x-amz-content-sha256": payload_hash}
        nomi = sorted(headers)
        canonici = "".join(f"{k}:{str(headers[k]).strip()}\n" for k in nomi)
        firmati = ";".join(nomi)
        richiesta = "\n".join([metodo, u.path, u.query, canonici, firmati, payload_hash])  # percorso già codificato da put()
        ambito = f"{data}/{self.region}/s3/aws4_request"
        da_firmare = "\n".join(["AWS4-HMAC-SHA256", ora, ambito, hashlib.sha256(richiesta.encode()).hexdigest()])
        k = ("AWS4" + self.sk).encode()
        for parte in (data, self.region, "s3", "aws4_request"):
            k = hmac.new(k, parte.encode(), hashlib.sha256).digest()
        firma = hmac.new(k, da_firmare.encode(), hashlib.sha256).hexdigest()
        headers["Authorization"] = f"AWS4-HMAC-SHA256 Credential={self.ak}/{ambito}, SignedHeaders={firmati}, Signature={firma}"
        return headers

    def put(self, percorso, chiave):
        corpo = Path(percorso).read_bytes()
        chiave = "/".join(p for p in (self.prefisso, chiave.strip("/")) if p)
        url = f"{self.endpoint}/{self.bucket}/{urllib.parse.quote(chiave, safe='/~')}"
        h = self._firma("PUT", url, {"content-type": "application/zip" if chiave.endswith(".zip") else "application/octet-stream"},
                        hashlib.sha256(corpo).hexdigest(), datetime.datetime.now(datetime.timezone.utc))
        h.pop("host")
        req = urllib.request.Request(url, data=corpo, method="PUT", headers=h)
        try:
            with urllib.request.urlopen(req, timeout=120, context=self.ctx if url.startswith("https") else None) as r:
                r.read()
        except urllib.error.HTTPError as e:
            dettaglio = e.read()[:300].decode("utf-8", "replace")
            raise ArchivioError(f"S3 ha rifiutato il caricamento ({e.code}): {dettaglio}")
        except (urllib.error.URLError, OSError) as e:
            raise ArchivioError(f"S3 non raggiungibile: {getattr(e, 'reason', e)}")
        return f"s3://{self.bucket}/{chiave}"


class ArchivioGCS:
    """Caricamento su Google Cloud Storage con il token dell'account di servizio della VM (nessuna chiave da custodire)."""
    nome = "gcs"
    META = os.getenv("GCE_METADATA_URL", "http://metadata.google.internal/computeMetadata/v1")
    API = os.getenv("GCS_API_URL", "https://storage.googleapis.com")

    def __init__(self, bucket, prefisso=""):
        if not bucket:
            raise ArchivioError("GCS: serve il nome del bucket")
        self.bucket, self.prefisso = bucket, prefisso.strip("/")

    def _token(self):
        req = urllib.request.Request(self.META + "/instance/service-accounts/default/token", headers={"Metadata-Flavor": "Google"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())["access_token"]
        except Exception as e:
            raise ArchivioError(f"GCS: token della VM non disponibile ({e}); gira su una VM di Google con un account di servizio?")

    def put(self, percorso, chiave):
        corpo = Path(percorso).read_bytes()
        chiave = "/".join(p for p in (self.prefisso, chiave.strip("/")) if p)
        url = (f"{self.API}/upload/storage/v1/b/{urllib.parse.quote(self.bucket, safe='')}/o?uploadType=media&name="
               + urllib.parse.quote(chiave, safe=""))
        req = urllib.request.Request(url, data=corpo, method="POST", headers={
            "Authorization": "Bearer " + self._token(), "Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=120, context=None if url.startswith("http:") else ssl.create_default_context()) as r:
                r.read()
        except urllib.error.HTTPError as e:
            raise ArchivioError(f"GCS ha rifiutato il caricamento ({e.code}): {e.read()[:300].decode('utf-8', 'replace')}")
        except (urllib.error.URLError, OSError) as e:
            raise ArchivioError(f"GCS non raggiungibile: {getattr(e, 'reason', e)}")
        return f"gs://{self.bucket}/{chiave}"


def crea_archivio(cfg, base):
    """cfg = valori del .env (dict). base = cartella del progetto, per i percorsi relativi."""
    tipo = (cfg.get("ARCHIVE_BACKEND") or "local").strip().lower()
    if tipo == "local":
        cartella = Path(cfg.get("ARCHIVE_DIR") or Path(cfg.get("TEST_LOG_DIR") or "logs") / "archivio")
        return ArchivioLocale(cartella if cartella.is_absolute() else base / cartella)
    if tipo == "s3":
        return ArchivioS3(cfg.get("S3_ENDPOINT", ""), cfg.get("S3_BUCKET", ""), cfg.get("S3_ACCESS_KEY", ""),
                          cfg.get("S3_SECRET_KEY", ""), cfg.get("S3_REGION", ""), cfg.get("S3_PREFIX", ""))
    if tipo == "gcs":
        return ArchivioGCS(cfg.get("GCS_BUCKET", ""), cfg.get("GCS_PREFIX", ""))
    raise ArchivioError(f"ARCHIVE_BACKEND={tipo} non riconosciuto (usa local, s3 oppure gcs)")
