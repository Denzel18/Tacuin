#!/usr/bin/env python3
"""
Servizio "vision": legge la foto di uno scontrino e ne estrae negozio, totale, data, ora, articoli.

Pipeline:
  1. OpenCV: trova lo scontrino nella foto (contorno a 4 lati), lo raddrizza (prospettiva) e lo
     ripulisce (scala di grigi, contrasto locale CLAHE, riduzione rumore, raddrizzamento dell'inclinazione).
  2. OCR con Tesseract (italiano + inglese), con la confidenza parola per parola.
  3. Regole per gli scontrini italiani: TOTALE / TOTALE COMPLESSIVO / IMPORTO PAGATO, date, P.IVA, articoli.
  4. Punteggio "è uno scontrino?" e confidenza per ogni campo.

API:
  GET  /health              -> {"ok": true, ...}
  POST /analyze  (immagine) -> {"ok": true, "merchant": ..., "total": ..., "date": ..., ...}
"""
import hmac
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("CV_IO_MAX_IMAGE_PIXELS", str(40_000_000))  # niente immagini «bomba»: max 40 megapixel
import cv2  # noqa: E402
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("vision")

PORT = int(os.getenv("PORT", "8090"))
TOKEN = os.getenv("VISION_TOKEN", "")
MAX_BYTES = 15 * 1024 * 1024
LANGS = os.getenv("OCR_LANGS", "ita+eng")

CHAINS = ["esselunga", "conad", "coop", "lidl", "carrefour", "eurospin", "pam", "despar", "aldi", "penny", "md ", "iper",
          "bennet", "tigre", "famila", "crai", "sigma", "todis", "ikea", "decathlon", "mediaworld", "unieuro", "euronics",
          "leroy merlin", "brico", "tigota", "acqua & sapone", "acqua e sapone", "douglas", "sephora", "zara", "h&m",
          "ovs", "primark", "mcdonald", "burger king", "autogrill", "eni", "q8", "ip ", "tamoil", "esso", "farmacia",
          "trony", "expert", "naturasi", "il gigante", "unes", "simply", "a&o", "gros", "basko", "dok", "interspar", "spar"]
NOT_MERCHANT = re.compile(r"(p\.?\s?iva|partita|c\.?f\.?|cod\.? ?fisc|via |viale|piazza|corso |tel\.?|telefono|www\.|http|"
                          r"documento|commerciale|vendita|scontrino|fiscale|cassa|cassiere|operatore|^\d)", re.I)
AMOUNT = r"(-?\d{1,5}(?:[.\s]\d{3})*[,.]\d{2})"


# ---------------------------------------------------------------- immagine
def order_points(pts):
    pts = pts.reshape(4, 2).astype("float32")
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")


def find_document(img):
    """Ritorna lo scontrino raddrizzato (se trovato un contorno a 4 lati abbastanza grande) e True/False."""
    h, w = img.shape[:2]
    scale = 800 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1 else img.copy()
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area_img = small.shape[0] * small.shape[1]
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:8]:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4 and cv2.contourArea(approx) > 0.15 * area_img:
            rect = order_points(approx / (scale if scale < 1 else 1))
            tl, tr, br, bl = rect
            wid = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
            hei = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
            if wid < 100 or hei < 100:
                continue
            dst = np.array([[0, 0], [wid - 1, 0], [wid - 1, hei - 1], [0, hei - 1]], dtype="float32")
            M = cv2.getPerspectiveTransform(rect, dst)
            return cv2.warpPerspective(img, M, (wid, hei)), True
    return img, False


def deskew(gray):
    """Corregge piccole inclinazioni (±15°) usando il rettangolo minimo che contiene il testo."""
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(inv > 0))
    if len(coords) < 500:
        return gray, 0.0
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    angle = angle - 90 if angle > 45 else angle
    if abs(angle) < 0.5 or abs(angle) > 15:
        return gray, 0.0
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE), float(angle)


def preprocess(img):
    doc, found = find_document(img)
    gray = cv2.cvtColor(doc, cv2.COLOR_BGR2GRAY) if doc.ndim == 3 else doc
    # porta la larghezza a ~1200 px: Tesseract rende meglio con caratteri di 20-30 px
    h, w = gray.shape
    target = 1200
    if w < target * 0.8 or w > target * 1.6:
        f = target / w
        gray = cv2.resize(gray, (target, int(h * f)), interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    gray, angle = deskew(gray)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    return bw, {"document_found": found, "deskew": round(angle, 1)}


# ---------------------------------------------------------------- OCR
def ocr(bw, psm=6):
    """Tesseract in formato TSV: righe di testo con confidenza media."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        cv2.imwrite(path, bw)
        out = subprocess.run(["tesseract", path, "stdout", "-l", LANGS, "--psm", str(psm), "tsv"],
                             capture_output=True, text=True, timeout=60).stdout
    finally:
        os.unlink(path)
    lines = {}
    for row in out.splitlines()[1:]:
        p = row.split("\t")
        if len(p) < 12 or not p[11].strip():
            continue
        key = (int(p[2]), int(p[3]), int(p[4]))  # blocco, paragrafo, riga
        conf = float(p[10])
        ln = lines.setdefault(key, {"words": [], "confs": [], "top": int(p[7])})
        ln["words"].append(p[11])
        if conf >= 0:
            ln["confs"].append(conf)
    res = []
    for k in sorted(lines, key=lambda k: (lines[k]["top"], k)):
        ln = lines[k]
        res.append({"text": " ".join(ln["words"]), "conf": sum(ln["confs"]) / len(ln["confs"]) if ln["confs"] else 0})
    return res


# ---------------------------------------------------------------- lettura dello scontrino
def to_float(s):
    s = s.replace(" ", "")
    if re.search(r"[.,]\d{2}$", s):
        s = re.sub(r"[.,](?=\d{3}(?:[.,]|$))", "", s[:-3]) + "." + s[-2:]
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def fix_ocr_digits(t):
    """Errori tipici dell'OCR nei numeri: O→0, l/I→1, S→5 quando sono in mezzo a cifre."""
    return re.sub(r"(?<=\d)[Oo](?=\d)|(?<=\d[.,])[Oo]|(?<=[.,]\d)[Oo]", "0", t)


def parse(lines):
    texts = [fix_ocr_digits(l["text"]) for l in lines]
    up = [t.upper() for t in texts]
    out = {"merchant": None, "total": None, "date": None, "time": None, "vat": None, "items": [],
           "confidence": {"merchant": 0.0, "total": 0.0, "date": 0.0}}

    # totale: la riga con la parola chiave più affidabile
    best = None
    prio = [(r"TOTALE\s+COMPLESSIVO", 1.0), (r"IMPORTO\s+PAGATO", 0.97), (r"TOTALE\s+(EURO|EUR|€)", 0.95),
            (r"\bTOTALE\b", 0.9), (r"\bTOT\b\.?", 0.75), (r"PAGAMENTO\s+(ELETTRONICO|CONTANTE|CARTA)", 0.7),
            (r"\b(CONTANTE|BANCOMAT|CARTA|POS)\b", 0.5)]
    bad = re.compile(r"SUB\s*TOTALE|SUBTOT|IVA|SCONTO|RESTO|RISPARMI|PUNTI|NON\s+RISCOSSO|ARTICOLI|PEZZI", re.I)
    for i, t in enumerate(up):
        if bad.search(t):
            continue
        for pat, w in prio:
            if re.search(pat, t):
                amts = re.findall(AMOUNT, t) or (re.findall(AMOUNT, up[i + 1]) if i + 1 < len(up) else [])
                if amts:
                    v = to_float(amts[-1])
                    if v and v > 0:
                        score = w * min(1.0, lines[i]["conf"] / 85 + 0.2)
                        if not best or score > best[1] + 0.05:
                            best = (v, score, i)
                break
    if not best:  # nessuna parola chiave: l'importo più alto nella metà bassa
        cands = [(to_float(a), i) for i, t in enumerate(up) if i >= len(up) // 3 for a in re.findall(AMOUNT, t)]
        cands = [(v, i) for v, i in cands if v and 0 < v < 100000]
        if cands:
            v, i = max(cands)
            best = (v, 0.35, i)
    if best:
        out["total"], out["confidence"]["total"] = round(best[0], 2), round(best[1], 2)
    total_line = best[2] if best else len(up)

    # data e ora
    for i, t in enumerate(texts):
        m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b", t)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y = y + 2000 if y < 100 else y
            try:
                dt = date(y, mo, d)
                if 2000 <= y <= date.today().year + 1:
                    out["date"] = dt.isoformat()
                    out["confidence"]["date"] = round(min(1.0, lines[i]["conf"] / 90), 2)
                    tm = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", t[m.end():] + " " + (texts[i + 1] if i + 1 < len(texts) else ""))
                    if tm:
                        out["time"] = f"{int(tm.group(1)):02d}:{tm.group(2)}"
                    break
            except ValueError:
                pass

    # P.IVA
    for t in up:
        m = re.search(r"P\.?\s?IVA[^\d]{0,6}(\d{11})", t)
        if m:
            out["vat"] = m.group(1)
            break

    # negozio: una catena nota nelle prime righe, altrimenti la prima riga "sensata"
    head = texts[:8]
    for i, t in enumerate(head):
        low = " " + t.lower() + " "
        for c in CHAINS:
            if c.strip() in low and re.search(r"\b" + re.escape(c.strip()) + r"\b", low):
                out["merchant"] = c.strip().title() if len(c.strip()) > 3 else c.strip().upper()
                out["confidence"]["merchant"] = 0.99
                break
        if out["merchant"]:
            break
    if not out["merchant"]:
        for i, t in enumerate(head):
            letters = sum(ch.isalpha() for ch in t)
            if letters >= 3 and letters / max(1, len(t)) > 0.5 and not NOT_MERCHANT.search(t) and lines[i]["conf"] > 45:
                name = re.sub(r"[^\w&'’\s.-]", "", t).strip(" .-")
                if len(name) >= 3:
                    out["merchant"] = name.title()[:40]
                    out["confidence"]["merchant"] = round(min(0.9, lines[i]["conf"] / 100), 2)
                    break

    # articoli: "DESCRIZIONE ... 1,29" prima del totale
    skip = re.compile(r"TOTALE|SUBTOT|IVA|SCONTO|RESTO|CONTANTE|BANCOMAT|CARTA|PAGAMENTO|RESO|P\.?IVA|DOCUMENTO|EURO$|^EUR", re.I)
    for i, t in enumerate(texts[:total_line]):
        m = re.match(r"^(.{3,40}?)\s+" + AMOUNT + r"\s*[A-Z*]{0,2}\s*$", t.strip())
        if m and not skip.search(t) and sum(ch.isalpha() for ch in m.group(1)) >= 2:
            v = to_float(m.group(2))
            if v is not None and abs(v) < 10000:
                out["items"].append({"name": re.sub(r"\s+", " ", m.group(1)).strip(" .*").title(), "amount": round(v, 2)})
    out["items"] = out["items"][:60]

    # è uno scontrino?
    score = 0.0
    score += 0.35 if best and best[1] >= 0.5 else 0.1 if best else 0
    score += 0.2 if out["date"] else 0
    score += 0.15 if out["vat"] else 0
    score += 0.15 if any(re.search(r"DOCUMENTO\s+COMMERCIALE|SCONTRINO|FISCALE", t) for t in up) else 0
    score += 0.15 if len(out["items"]) >= 2 else 0
    out["receipt_likelihood"] = round(min(1.0, score), 2)
    return out


def analyze(data):
    t0 = time.time()
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Immagine non leggibile: usa una foto JPG o PNG.")
    bw, info = preprocess(img)
    # psm 6 (blocco unico) tiene insieme descrizione e prezzo sulla stessa riga; se non basta, riprovo con psm 4
    best = None
    for psm in (6, 4):
        lines = ocr(bw, psm)
        res = parse(lines)
        score = res["receipt_likelihood"] + res["confidence"]["total"] + (0.3 if res["date"] else 0)
        if not best or score > best[0]:
            best = (score, res, lines, psm)
        if res["confidence"]["total"] >= 0.7 and res["date"]:
            break
    _, res, lines, psm = best
    info["psm"] = psm
    res.update({"ok": True, "ms": int((time.time() - t0) * 1000), "lines": len(lines), **info,
                "text": "\n".join(l["text"] for l in lines)[:4000]})
    return res


# ---------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    server_version = "TrackerVision"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            v = subprocess.run(["tesseract", "--version"], capture_output=True, text=True).stdout.split("\n")[0]
            return self._json(200, {"ok": True, "opencv": cv2.__version__, "tesseract": v, "langs": LANGS})
        self._json(404, {"error": "non trovato"})

    def do_POST(self):
        if self.path != "/analyze":
            return self._json(404, {"error": "non trovato"})
        if TOKEN and not hmac.compare_digest(self.headers.get("X-Vision-Token") or "", TOKEN):
            return self._json(401, {"error": "non autorizzato"})
        n = int(self.headers.get("Content-Length", "0"))
        if not n or n > MAX_BYTES:
            return self._json(413, {"error": "immagine mancante o troppo grande (max 15 MB)"})
        try:
            res = analyze(self.rfile.read(n))
            log.info("Scontrino analizzato in %d ms", res["ms"])
            self._json(200, res)
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception:
            log.exception("analisi")
            self._json(500, {"error": "analisi non riuscita"})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log.info("Servizio vision in ascolto sulla porta %d (OCR: %s)", PORT, LANGS)
    srv.serve_forever()
