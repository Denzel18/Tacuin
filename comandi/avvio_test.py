"""
Avvio per i test: aggiorna il codice, accende il servizio scontrini, avvia il Tracker e registra tutto.

    python comandi/avvio_test.py [--no-pull] [--no-vision] [--keep-vision]

1. Codice: guarda su GitHub se c'è una versione nuova e la scarica (git pull, solo se non hai modifiche locali).
2. Scansione scontrini: avvia Docker Desktop se serve, poi il servizio «vision» (docker compose), e aspetta che risponda.
3. Tracker: lo avvia, mostra i log nella finestra e li scrive su file mentre arrivano (niente si perde).
4. Alla chiusura (Ctrl+C): ferma tutto, scrive il riepilogo sessione.json, crea lo .zip della sessione e lo archivia
   (cartella locale o S3, vedi deploy/archivio.py). Se chiudi la finestra con la X, la sessione viene chiusa e
   archiviata al prossimo avvio.

Tutto finisce in logs/ (o TEST_LOG_DIR del .env):
    logs/sessioni/<data>_<commit>/   avvio.log  tracker.log  vision.log  docker.log  sessione.json
    logs/archivio/AAAA/MM/<sessione>.zip     (destinazione local)
    logs/da-caricare/                        (zip non ancora caricati: ritentati al prossimo avvio)
Token e password del .env vengono oscurati nei log.
"""
import argparse
import datetime as dt
import getpass
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "deploy"))  # archivio.py è lo stesso usato dai backup sul server
import archivio  # noqa: E402

WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if WIN else 0  # CREATE_NO_WINDOW: niente finestre nere per git e docker


# ------------------------------------------------------------------ configurazione
def leggi_env(path):
    vals = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v or k not in vals:
                vals[k] = v
    return {**vals, **{k: v for k, v in os.environ.items() if k in vals or k.startswith(("TEST_", "ARCHIVE_", "S3_"))}}


CFG = leggi_env(BASE / ".env")
LOG_ROOT = Path(CFG.get("TEST_LOG_DIR") or "logs")
LOG_ROOT = LOG_ROOT if LOG_ROOT.is_absolute() else BASE / LOG_ROOT
SESSIONI, CODA = LOG_ROOT / "sessioni", LOG_ROOT / "da-caricare"
PORT = int(CFG.get("PORT") or 8080)
VISION_PORT = int(CFG.get("TEST_VISION_PORT") or 8090)
KEEP_DAYS = int(CFG.get("TEST_KEEP_DAYS") or 30)

# valori da non scrivere mai nei log (token, password, chiavi)
SEGRETI = sorted({v for k, v in CFG.items() if len(v) >= 6 and re.search(r"TOKEN|PASSWORD|SECRET|_KEY|PASSPHRASE", k)},
                 key=len, reverse=True)
BOT_RE = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}")


def oscura(s):
    for v in SEGRETI:
        s = s.replace(v, "***")
    return BOT_RE.sub("bot***", s)


# ------------------------------------------------------------------ sessione e log
class Sessione:
    def __init__(self):
        self.inizio = dt.datetime.now()
        self.id = self.inizio.strftime("%Y-%m-%d_%H-%M-%S")
        self.dir = None
        self.info = {"sessione": self.id, "inizio": self.inizio.isoformat(timespec="seconds"), "fine": None,
                     "computer": platform.node(), "utente": _utente(), "sistema": f"{platform.system()} {platform.release()}",
                     "python": platform.python_version(), "git": {}, "vision": {}, "tracker": {}, "archivio": None}
        self._lock = threading.Lock()
        self._avvio = None
        self._buf = []  # righe scritte prima di sapere il commit (= nome della cartella)

    def apri(self, commit):
        self.id = f"{self.id}_{commit or 'senza-git'}"
        self.info["sessione"] = self.id
        self.dir = SESSIONI / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / ".in-corso").write_text(str(os.getpid()))
        self._avvio = open(self.dir / "avvio.log", "a", encoding="utf-8", buffering=1)
        for r in self._buf:
            self._avvio.write(r + "\n")
        self._buf = []
        self.salva()

    def log(self, msg, livello="INFO"):
        riga = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {livello:<7} {oscura(msg)}"
        with self._lock:
            print(riga, flush=True)
            if self._avvio:
                self._avvio.write(riga + "\n")
            else:
                self._buf.append(riga)

    def salva(self):
        if self.dir:
            tmp = self.dir / "sessione.json.tmp"
            tmp.write_text(json.dumps(self.info, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.dir / "sessione.json")

    def chiudi_file(self):
        if self._avvio:
            self._avvio.close()
            self._avvio = None


def _utente():
    try:
        return getpass.getuser()
    except Exception:
        return ""


S = Sessione()


def run(cmd, timeout=120, ok_codes=(0,)):
    """Esegue un comando e ne scrive l'output nel log di avvio. → (codice, testo)"""
    try:
        p = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=NO_WINDOW)
    except FileNotFoundError:
        return 127, f"{cmd[0]} non trovato"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd)}: tempo scaduto ({timeout} s)"
    out = (p.stdout + p.stderr).strip()
    return p.returncode, out


# ------------------------------------------------------------------ 0. sessioni rimaste aperte e caricamenti in sospeso
def recupera_precedenti(store):
    for d in sorted(SESSIONI.glob("*/.in-corso")) if SESSIONI.exists() else []:
        sd = d.parent
        if sd == S.dir:
            continue
        try:
            pid = int(d.read_text() or 0)
        except ValueError:
            pid = 0
        if pid and _pid_vivo(pid):
            continue  # un'altra sessione di test è ancora accesa
        info = _leggi_json(sd / "sessione.json")
        info["fine"] = info.get("fine") or dt.datetime.fromtimestamp(_ultima_modifica(sd)).isoformat(timespec="seconds")
        info["chiusura"] = "finestra chiusa senza Ctrl+C: chiusa al prossimo avvio"
        (sd / "sessione.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        d.unlink()
        S.log(f"Sessione precedente {sd.name} non era stata chiusa: la chiudo e la archivio")
        archivia(sd, store)
    for z in sorted(CODA.glob("*.zip")) if CODA.exists() else []:
        try:
            dove = store.put(z, _chiave(z.name))
            z.unlink()
            S.log(f"Caricato ora l'archivio rimasto in sospeso: {dove}")
            sd = SESSIONI / z.name[len("sessione-"):-len(".zip")]
            if (sd / "sessione.json").exists():
                _segna_archivio(sd, {"destinazione": store.nome, "dove": dove, "caricato_dopo": True})
        except Exception as e:
            S.log(f"Archivio {z.name} ancora in sospeso: {e}", "WARNING")
            break


def _pid_vivo(pid):
    if pid == os.getpid():
        return True
    if WIN:
        c, out = run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=10)
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _ultima_modifica(d):
    return max((f.stat().st_mtime for f in d.iterdir()), default=time.time())


def _leggi_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"sessione": p.parent.name}


# ------------------------------------------------------------------ 1. codice
def aggiorna_codice(fai_pull):
    g = S.info["git"]
    if not (BASE / ".git").exists():
        S.log("Questa cartella non è un repository git: salto l'aggiornamento (scaricala con git clone per averlo)", "WARNING")
        return None, False
    _, head = run(["git", "rev-parse", "--short", "HEAD"])
    _, branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    g.update({"branch": branch, "commit_prima": head})
    if not fai_pull:
        S.log(f"Codice: {branch} @ {head} (aggiornamento saltato)")
        g["commit"] = head
        return head, False
    S.log("Controllo se su GitHub c'è una versione più recente…")
    c, out = run(["git", "fetch", "--quiet", "origin"], timeout=60)
    if c:
        S.log(f"Non riesco a raggiungere GitHub ({out.splitlines()[-1] if out else c}): uso la versione che hai", "WARNING")
        g.update({"commit": head, "aggiornamento": "GitHub non raggiungibile"})
        return head, False
    _, dietro = run(["git", "rev-list", "--count", "HEAD..@{u}"])
    _, avanti = run(["git", "rev-list", "--count", "@{u}..HEAD"])
    dietro, avanti = int(dietro or 0) if dietro.isdigit() else 0, int(avanti or 0) if avanti.isdigit() else 0
    _, modifiche = run(["git", "status", "--porcelain", "--untracked-files=no"])
    if dietro == 0:
        S.log(f"Codice già aggiornato: {branch} @ {head}")
        g.update({"commit": head, "aggiornamento": "già aggiornato"})
        return head, False
    _, novita = run(["git", "log", "--format=%h %s", "HEAD..@{u}"])
    S.log("C'è 1 aggiornamento nuovo:" if dietro == 1 else f"Ci sono {dietro} aggiornamenti nuovi:")
    for r in novita.splitlines()[:15]:
        S.log("   · " + r)
    if modifiche:
        S.log("Hai file modificati in questa cartella: non scarico gli aggiornamenti per non sovrascriverli.", "WARNING")
        S.log("   (per scaricarli: annulla le modifiche oppure esegui «git stash», poi riavvia)", "WARNING")
        g.update({"commit": head, "aggiornamento": f"saltato: {dietro} commit nuovi ma ci sono modifiche locali",
                  "modifiche_locali": modifiche.splitlines()})
        return head, False
    if avanti:
        S.log(f"Il tuo codice ha {avanti} commit non pubblicati: provo comunque ad allinearmi", "WARNING")
    c, out = run(["git", "pull", "--ff-only", "--quiet"], timeout=120)
    if c:
        S.log(f"Aggiornamento non riuscito: {out.splitlines()[-1] if out else c}. Uso la versione che hai", "ERROR")
        g.update({"commit": head, "aggiornamento": "pull non riuscito"})
        return head, False
    _, nuovo = run(["git", "rev-parse", "--short", "HEAD"])
    _, cambiati = run(["git", "diff", "--name-only", f"{head}..{nuovo}"])
    S.log(f"Codice aggiornato: {head} → {nuovo}")
    g.update({"commit": nuovo, "aggiornamento": f"{dietro} commit scaricati", "commit_scaricati": novita.splitlines()})
    rilancia = any(f.startswith("comandi/") or f == "deploy/archivio.py" for f in cambiati.splitlines())
    return nuovo, rilancia


# ------------------------------------------------------------------ 2. servizio scontrini (vision)
def docker_attivo():
    c, _ = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=25)
    return c == 0


def avvia_docker_desktop():
    if not WIN:
        return False
    for p in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe",):
        if p.exists():
            S.log("Docker Desktop è spento: lo avvio (può servire un minuto)…")
            subprocess.Popen([str(p)], creationflags=NO_WINDOW)
            for _ in range(36):
                time.sleep(5)
                if docker_attivo():
                    return True
            return False
    return False


def health_vision():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{VISION_PORT}/health", timeout=3) as r:
            return json.loads(r.read() or b"{}")
    except Exception:
        return None


def avvia_vision():
    v = S.info["vision"]
    gia = health_vision()
    if gia:
        S.log(f"Servizio scontrini già acceso sulla porta {VISION_PORT}")
        v.update({"attivo": True, "avviato_da_questa_sessione": False, **{k: gia.get(k) for k in ("opencv", "tesseract")}})
        return True, False
    c, out = run(["docker", "--version"], timeout=15)
    if c:
        S.log("Docker non è installato: la scansione degli scontrini non sarà attiva in questa sessione", "WARNING")
        S.log("   (per attivarla installa Docker Desktop da docker.com)", "WARNING")
        v.update({"attivo": False, "motivo": "docker non installato"})
        return False, False
    if not docker_attivo() and not avvia_docker_desktop():
        S.log("Docker Desktop non è acceso: aprilo e riavvia, intanto proseguo senza scansione scontrini", "WARNING")
        v.update({"attivo": False, "motivo": "docker spento"})
        return False, False
    S.log("Avvio il servizio scontrini (la prima volta lo prepara: qualche minuto)…")
    c, out = run(["docker", "compose", "up", "-d", "--build", "vision"], timeout=1200)
    (S.dir / "docker.log").write_text(oscura(out) + "\n", encoding="utf-8")  # output completo
    for r in [r for r in out.splitlines() if r.strip()][-(8 if c else 3):]:
        S.log("   docker: " + r.strip())
    if c:
        S.log("Il servizio scontrini non è partito: proseguo senza (dettagli sopra e in docker.log)", "ERROR")
        v.update({"attivo": False, "motivo": "docker compose up non riuscito"})
        return False, False
    for _ in range(60):
        h = health_vision()
        if h:
            S.log(f"Servizio scontrini pronto (OCR: {h.get('tesseract', '?')})")
            v.update({"attivo": True, "avviato_da_questa_sessione": True, **{k: h.get(k) for k in ("opencv", "tesseract")}})
            return True, True
        time.sleep(1)
    S.log(f"Il servizio scontrini non risponde sulla porta {VISION_PORT}: proseguo senza", "ERROR")
    v.update({"attivo": False, "motivo": "non risponde"})
    return False, True


def segui_log_vision(stop):
    """docker compose logs -f vision → vision.log (fino allo spegnimento)."""
    since = S.inizio.astimezone().isoformat(timespec="seconds")
    try:
        p = subprocess.Popen(["docker", "compose", "logs", "-f", "--no-color", "--timestamps", "--since", since, "vision"],
                             cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", creationflags=NO_WINDOW)
    except OSError:
        return None

    def pompa():
        with open(S.dir / "vision.log", "a", encoding="utf-8", buffering=1) as f:
            for riga in p.stdout:
                f.write(oscura(riga))
                if stop.is_set():
                    break
    threading.Thread(target=pompa, daemon=True).start()
    return p


# ------------------------------------------------------------------ 3. Tracker
def porta_occupata(porta):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def avvia_tracker(vision_ok):
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "OPEN_BROWSER": CFG.get("TEST_OPEN_BROWSER", "1")}
    if vision_ok:
        env["VISION_URL"] = f"http://localhost:{VISION_PORT}"
    p = subprocess.Popen([sys.executable, "-u", "server.py"], cwd=BASE, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    t = S.info["tracker"]
    t.update({"avvio": dt.datetime.now().isoformat(timespec="seconds"), "warning": 0, "errori": 0, "porta": PORT})

    def pompa():
        with open(S.dir / "tracker.log", "a", encoding="utf-8", buffering=1) as f:
            for riga in p.stdout:
                riga = oscura(riga)
                f.write(riga)
                print(riga, end="", flush=True)
                if " WARNING " in riga:
                    t["warning"] += 1
                elif " ERROR " in riga or "Traceback" in riga or " CRITICAL " in riga:
                    t["errori"] += 1
    th = threading.Thread(target=pompa, daemon=True)
    th.start()
    return p, th


# ------------------------------------------------------------------ 4. chiusura e archivio
def _chiave(nome_zip):
    d = nome_zip.replace("sessione-", "")[:10]  # AAAA-MM-GG
    return f"sessioni/{d[:4]}/{d[5:7]}/{nome_zip}"


def archivia(sd, store):
    nome = f"sessione-{sd.name}.zip"
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = LOG_ROOT / (nome + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(sd.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                z.write(f, f"{sd.name}/{f.name}")
    try:
        dove = store.put(tmp, _chiave(nome))
        tmp.unlink()
        _segna_archivio(sd, {"destinazione": store.nome, "dove": dove})
        return dove
    except Exception as e:
        CODA.mkdir(parents=True, exist_ok=True)
        tmp.replace(CODA / nome)
        _segna_archivio(sd, {"destinazione": store.nome, "in_sospeso": str(CODA / nome), "errore": str(e)})
        S.log(f"Archiviazione non riuscita ({e}): lo zip resta in {CODA} e lo ricarico al prossimo avvio", "WARNING")
        return None


def _segna_archivio(sd, dati):
    p = sd / "sessione.json"
    info = _leggi_json(p)
    info["archivio"] = dati
    p.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    if sd == S.dir:
        S.info["archivio"] = dati


def pulisci_vecchie():
    """Le cartelle di sessione già archiviate e più vecchie di TEST_KEEP_DAYS giorni si cancellano (lo zip resta)."""
    if not SESSIONI.exists() or KEEP_DAYS <= 0:
        return
    limite = time.time() - KEEP_DAYS * 86400
    for sd in SESSIONI.iterdir():
        if sd.is_dir() and sd != S.dir and not (sd / ".in-corso").exists() and _ultima_modifica(sd) < limite:
            if (_leggi_json(sd / "sessione.json").get("archivio") or {}).get("dove"):
                shutil.rmtree(sd, ignore_errors=True)


# ------------------------------------------------------------------ programma
def main():
    ap = argparse.ArgumentParser(description="Avvio del Tracker per i test, con log archiviati")
    ap.add_argument("--no-pull", action="store_true", help="non scaricare aggiornamenti")
    ap.add_argument("--no-vision", action="store_true", help="non avviare il servizio scontrini")
    ap.add_argument("--keep-vision", action="store_true", help="lascia acceso il servizio scontrini alla chiusura")
    a = ap.parse_args()
    signal.signal(signal.SIGINT, signal.default_int_handler)  # Ctrl+C sempre gestito, anche se ereditato «ignorato»
    if WIN:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    S.log("=" * 64)
    S.log("Tracker · avvio per i test")
    S.log(f"Log di questa sessione in: {SESSIONI}")
    try:
        store = archivio.crea_archivio(CFG, BASE)
    except archivio.ArchivioError as e:
        S.log(f"{e}: uso l'archivio locale", "WARNING")
        store = archivio.ArchivioLocale(LOG_ROOT / "archivio")

    commit, rilancia = aggiorna_codice(not a.no_pull and os.environ.get("TRACKER_TEST_RILANCIATO") != "1")
    if rilancia:
        S.log("Anche lo script di avvio è stato aggiornato: riparto con la versione nuova…")
        env = {**os.environ, "TRACKER_TEST_RILANCIATO": "1", "TRACKER_TEST_GIT": json.dumps(S.info["git"])}
        return subprocess.call([sys.executable, str(Path(__file__).resolve())] + sys.argv[1:], env=env)
    if os.environ.get("TRACKER_TEST_GIT"):  # rilanciato dopo l'aggiornamento: teniamo quello che era stato scaricato
        S.info["git"] = {**json.loads(os.environ["TRACKER_TEST_GIT"]), "commit": commit}
    S.apri(commit)
    recupera_precedenti(store)
    pulisci_vecchie()

    stop = threading.Event()
    vision_ok, vision_nostro, seguilog = False, False, None
    occupata = porta_occupata(PORT)  # controllata subito: se il Tracker è già acceso non serve avviare altro
    if occupata:
        pass
    elif a.no_vision:
        S.info["vision"] = {"attivo": False, "motivo": "--no-vision"}
    else:
        vision_ok, vision_nostro = avvia_vision()
        if vision_ok:
            seguilog = segui_log_vision(stop)

    codice = 0
    if occupata:
        S.log(f"La porta {PORT} è già usata: probabilmente il Tracker è già acceso (avvio automatico?).", "ERROR")
        S.log("   Chiudilo (o esegui «comandi/windows/Disattiva avvio automatico.bat») e riavvia questo file.", "ERROR")
        S.info["tracker"] = {"errore": f"porta {PORT} occupata"}
        codice = 2
    else:
        S.log(f"Avvio il Tracker su http://localhost:{PORT} — per fermare tutto premi Ctrl+C")
        S.log("-" * 64)
        S.salva()
        p, th = avvia_tracker(vision_ok)
        _gestisci_chiusura_finestra(p)
        try:
            while p.poll() is None:
                time.sleep(0.5)
        except KeyboardInterrupt:
            S.log("Ctrl+C: chiudo il Tracker…")
            _ferma(p)
        th.join(timeout=5)
        codice = p.returncode if p.returncode is not None else -1
        S.info["tracker"]["fine"] = dt.datetime.now().isoformat(timespec="seconds")
        S.info["tracker"]["codice_uscita"] = codice
        S.log("-" * 64)
        S.log(f"Tracker fermato (codice {codice})")

    chiudi(stop, seguilog, vision_nostro and not a.keep_vision, store)
    return codice


def _ferma(p):
    if p.poll() is not None:
        return
    try:
        # su Windows Ctrl+C arriva già anche al Tracker (stessa finestra): gli diamo il tempo di chiudersi da solo
        p.wait(timeout=8)
        return
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        pass
    p.terminate()
    try:
        p.wait(timeout=8)
    except subprocess.TimeoutExpired:
        p.kill()


def _gestisci_chiusura_finestra(p):
    """Chiusura con la X: Windows concede pochi secondi. Scriviamo la fine e lasciamo l'archivio al prossimo avvio."""
    if not WIN:
        def da_terminale(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, da_terminale)
        return
    try:
        import ctypes
        HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

        def gestore(evento):
            if evento in (2, 5, 6):  # chiusura finestra, logoff, spegnimento
                S.info["fine"] = dt.datetime.now().isoformat(timespec="seconds")
                S.info["chiusura"] = "finestra chiusa"
                S.salva()
                try:
                    p.terminate()
                except Exception:
                    pass
            return 0
        _gestisci_chiusura_finestra.h = HANDLER(gestore)  # il riferimento deve restare vivo
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_gestisci_chiusura_finestra.h, True)
    except Exception:
        pass


def chiudi(stop, seguilog, ferma_vision, store):
    stop.set()
    if ferma_vision:
        S.log("Spengo il servizio scontrini…")
        c, out = run(["docker", "compose", "stop", "vision"], timeout=60)
        if c:
            S.log(f"   non riuscito: {out[-200:]}", "WARNING")
    if seguilog:
        time.sleep(1)  # le ultime righe di vision
        seguilog.terminate()
    S.info["fine"] = dt.datetime.now().isoformat(timespec="seconds")
    S.info.setdefault("chiusura", "normale")
    secondi = int((dt.datetime.now() - S.inizio).total_seconds())
    S.info["durata"] = f"{secondi // 3600}h {secondi % 3600 // 60}m {secondi % 60}s"
    t = S.info.get("tracker") or {}
    S.log(f"Riepilogo: durata {S.info['durata']}, {t.get('errori', 0)} errori e {t.get('warning', 0)} avvisi nel Tracker")
    S.salva()
    (S.dir / ".in-corso").unlink(missing_ok=True)
    S.chiudi_file()
    dove = archivia(S.dir, store)
    S.chiudi_file()
    print(f"\n  Log della sessione: {S.dir}")
    if dove:
        print(f"  Archivio:           {dove}\n")


if __name__ == "__main__":
    sys.exit(main())
