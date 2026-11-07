"""
Prezzi di borsa a partire dal codice ISIN.

Fonti gratuite e senza chiave:
- Yahoo Finance (le stesse richieste che fa il sito): ricerca per ISIN → simboli sulle varie borse, poi prezzo e storico;
- OpenFIGI (Bloomberg, gratuito): se Yahoo non trova l'ISIN, ricava ticker e borsa e prova con quelli.

Il server chiede solo codici ISIN e simboli, mai dati della persona. Le risposte restano in memoria (e in un piccolo
file) per non ripetere le stesse richieste: i prezzi per 15 minuti, le corrispondenze ISIN → simbolo per 30 giorni.
Yahoo non è un servizio ufficiale: se smette di rispondere i prezzi restano quelli scritti a mano.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (compatible; Tacuin/1.0; +https://github.com/Denzel18/tacuin)"
SEARCH_URL = os.getenv("QUOTES_SEARCH_URL") or "https://query2.finance.yahoo.com/v1/finance/search?"
CHART_URL = os.getenv("QUOTES_CHART_URL") or "https://query1.finance.yahoo.com/v8/finance/chart/{}?"
FIGI_URL = os.getenv("QUOTES_FIGI_URL") or "https://api.openfigi.com/v3/mapping"

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
SYMBOL_RE = re.compile(r"^[A-Za-z0-9^=._-]{1,24}$")
RANGES = {"1mo": "1d", "3mo": "1d", "6mo": "1d", "1y": "1d", "5y": "1wk", "max": "1mo"}

# borse in euro prima (chi usa Tacuìn compra soprattutto lì), Milano per prima
PREFERRED = ["MIL", "GER", "AMS", "PAR", "BRU", "MCE", "VIE", "LIS", "DUB", "HEL", "FRA", "STU", "MUN", "DUS", "BER", "HAM",
             "LSE", "EBS", "NYQ", "NMS", "NGM", "NCM", "ASE", "PCX", "BTS"]
TYPES = {"EQUITY": "Azione", "ETF": "ETF", "MUTUALFUND": "Fondo", "CRYPTOCURRENCY": "Crypto", "INDEX": "Indice",
         "BOND": "Obbligazione", "MONEYMARKET": "Fondo", "FUTURE": "Altro", "CURRENCY": "Altro"}
# codici borsa di OpenFIGI → suffisso dei simboli Yahoo
FIGI_SUFFIX = {"IM": ".MI", "GY": ".DE", "GR": ".DE", "GF": ".F", "NA": ".AS", "FP": ".PA", "BB": ".BR", "SM": ".MC",
               "AV": ".VI", "PL": ".LS", "ID": ".IR", "FH": ".HE", "LN": ".L", "SW": ".SW", "US": "", "UN": "", "UW": "",
               "UQ": "", "UA": "", "UP": ""}


def isin_ok(s):
    """Formato e cifra di controllo (Luhn sulle cifre ottenute convertendo le lettere: A=10 … Z=35)."""
    s = str(s or "").strip().upper()
    if not ISIN_RE.fullmatch(s):
        return False
    digits = "".join(str(int(c, 36)) for c in s)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class QuoteError(Exception):
    pass


class Quotes:
    """Cache + richieste verso Yahoo/OpenFIGI. `fetch(url, data=None) -> dict` si può sostituire nei test."""

    PRICE_TTL = 15 * 60
    HIST_TTL = 6 * 3600
    LOOKUP_TTL = 30 * 86400
    UPSTREAM_PER_MIN = 40  # in tutto, per non farsi bloccare

    def __init__(self, path=None, fetch=None):
        self.path = path
        self.fetch = fetch or self._http
        self.lock = threading.Lock()
        self.calls = []
        self.cache = {}
        self.enabled = True
        if path:
            try:
                self.cache = json.loads(path.read_text("utf-8"))
            except Exception:
                self.cache = {}

    # ---------------------------------------------------------------- rete
    def _http(self, url, data=None):
        headers = {"User-Agent": UA, "Accept": "application/json"}
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read(2_000_000).decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            raise QuoteError(f"il servizio dei prezzi ha risposto {e.code}")
        except Exception as e:
            raise QuoteError("il servizio dei prezzi non risponde") from e

    def _call(self, url, data=None):
        now = time.time()
        with self.lock:
            self.calls = [t for t in self.calls if t > now - 60]
            if len(self.calls) >= self.UPSTREAM_PER_MIN:
                raise QuoteError("troppe richieste di prezzi: riprova tra un minuto")
            self.calls.append(now)
        return self.fetch(url, data)

    # ---------------------------------------------------------------- cache
    def _get(self, key, ttl):
        with self.lock:
            v = self.cache.get(key)
        if v and time.time() - v.get("at", 0) < ttl:
            return v["v"]
        return None

    def _stale(self, key):
        with self.lock:
            v = self.cache.get(key)
        return v["v"] if v else None

    def _put(self, key, value):
        with self.lock:
            self.cache[key] = {"at": time.time(), "v": value}
            if len(self.cache) > 3000:  # tolgo le voci più vecchie
                for k, _ in sorted(self.cache.items(), key=lambda kv: kv[1].get("at", 0))[:500]:
                    self.cache.pop(k, None)
        self._save()

    def _save(self):
        if not self.path:
            return
        try:
            with self.lock:
                raw = json.dumps(self.cache, separators=(",", ":"))
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(raw, "utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    # ---------------------------------------------------------------- ISIN → simboli
    def lookup(self, isin):
        """Simboli che corrispondono all'ISIN, le borse in euro per prime: [{symbol, name, exchange, type}]."""
        isin = str(isin or "").strip().upper()
        if not isin_ok(isin):
            raise QuoteError("codice ISIN non valido")
        key = "isin:" + isin
        hit = self._get(key, self.LOOKUP_TTL)
        if hit is not None:
            return hit
        out = []
        try:
            r = self._call(SEARCH_URL + urllib.parse.urlencode({"q": isin, "quotesCount": 12, "newsCount": 0, "listsCount": 0}))
            for q in (r or {}).get("quotes") or []:
                sym = str(q.get("symbol") or "")
                if not SYMBOL_RE.fullmatch(sym) or not q.get("quoteType"):
                    continue
                out.append({"symbol": sym, "name": str(q.get("longname") or q.get("shortname") or sym)[:120],
                            "exchange": str(q.get("exchDisp") or q.get("exchange") or "")[:40], "ex": str(q.get("exchange") or ""),
                            "type": TYPES.get(str(q.get("quoteType")).upper(), "Altro")})
        except QuoteError:
            if self._stale(key) is not None:
                return self._stale(key)
            out = []
        if not out:
            out = self._figi(isin)
        out.sort(key=lambda x: PREFERRED.index(x["ex"]) if x["ex"] in PREFERRED else len(PREFERRED))
        out = out[:10]
        if out:
            self._put(key, out)
        return out

    def _figi(self, isin):
        """Strada di riserva: OpenFIGI dà ticker e borsa, da cui ricavo il simbolo Yahoo (senza nome della borsa preciso)."""
        try:
            r = self._call(FIGI_URL, [{"idType": "ID_ISIN", "idValue": isin}])
        except QuoteError:
            return []
        data = (r[0] if isinstance(r, list) and r else {}).get("data") or []
        out, seen = [], set()
        for d in data:
            suf = FIGI_SUFFIX.get(str(d.get("exchCode") or ""))
            tick = str(d.get("ticker") or "").replace("/", "-")
            if suf is None or not tick:
                continue
            sym = tick + suf
            if sym in seen or not SYMBOL_RE.fullmatch(sym):
                continue
            seen.add(sym)
            kind = str(d.get("securityType2") or d.get("marketSector") or "")
            out.append({"symbol": sym, "name": str(d.get("name") or tick).title()[:120], "exchange": str(d.get("exchCode")),
                        "ex": {".MI": "MIL", ".DE": "GER", ".AS": "AMS", ".PA": "PAR"}.get(suf, ""),
                        "type": "ETF" if "ETP" in kind or "Fund" in kind else "Azione" if "Common" in kind or "Equity" in kind else "Altro"})
        return out

    # ---------------------------------------------------------------- prezzi
    def _chart(self, symbol, rng, interval):
        r = self._call(CHART_URL.format(urllib.parse.quote(symbol, safe="=")) + urllib.parse.urlencode(
            {"range": rng, "interval": interval, "includePrePost": "false"}))
        res = ((r or {}).get("chart") or {}).get("result") or []
        if not res:
            raise QuoteError("simbolo non trovato")
        return res[0]

    def _fx(self, ccy):
        """Quanti euro vale 1 unità della valuta (1 per l'euro)."""
        if ccy in ("EUR", ""):
            return 1.0
        q = self.quote(f"{ccy}EUR=X", _fx=True)
        return q["native"] if q and q.get("native") else None

    def quote(self, symbol, _fx=False):
        """Prezzo in euro, variazione del giorno e chiusure dell'ultimo mese."""
        if not SYMBOL_RE.fullmatch(str(symbol or "")):
            raise QuoteError("simbolo non valido")
        key = "q:" + symbol
        hit = self._get(key, self.PRICE_TTL)
        if hit is not None:
            return hit
        try:
            res = self._chart(symbol, "1mo", "1d")
        except QuoteError:
            st = self._stale(key)
            if st is not None:
                return {**st, "stale": True}
            raise
        meta = res.get("meta") or {}
        closes = [c for c in ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [] if isinstance(c, (int, float))]
        price = meta.get("regularMarketPrice")
        if not isinstance(price, (int, float)):
            price = closes[-1] if closes else None
        if price is None:
            raise QuoteError("prezzo non disponibile")
        ccy = str(meta.get("currency") or "")
        div = 1.0
        if ccy in ("GBp", "GBX", "ZAc", "ILA"):  # pence e centesimi
            ccy, div = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}[ccy], 100.0
        prev = None
        t_last = meta.get("regularMarketTime")
        ts = [t for t, c in zip(res.get("timestamp") or [], ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
              if isinstance(c, (int, float))]
        if len(closes) >= 2:
            same_day = ts and t_last and _day(ts[-1]) == _day(t_last)
            prev = closes[-2] if same_day else closes[-1]
        out = {"symbol": symbol, "native": price / div, "ccy": ccy or "EUR",
               "chg": round((price / prev - 1) * 100, 2) if prev else None,
               "at": int(t_last or time.time()), "name": str(meta.get("longName") or meta.get("shortName") or "")[:120],
               "exchange": str(meta.get("fullExchangeName") or meta.get("exchangeName") or "")[:40],
               "type": TYPES.get(str(meta.get("instrumentType") or "").upper(), "")}
        if not _fx:
            rate = self._fx(out["ccy"])
            if rate is None:
                raise QuoteError(f"cambio {out['ccy']}/EUR non disponibile")
            out["price"] = round(out["native"] * rate, 6)
            out["spark"] = [round(c / div * rate, 4) for c in closes[-23:]]
        self._put(key, out)
        return out

    def history(self, symbol, rng="1y"):
        """[[giorno ISO, chiusura in euro], …] per il grafico (cambio di oggi, basta per l'andamento)."""
        if rng not in RANGES:
            raise QuoteError("periodo non valido")
        if not SYMBOL_RE.fullmatch(str(symbol or "")):
            raise QuoteError("simbolo non valido")
        key = f"h:{symbol}:{rng}"
        hit = self._get(key, self.HIST_TTL)
        if hit is not None:
            return hit
        try:
            res = self._chart(symbol, rng, RANGES[rng])
        except QuoteError:
            st = self._stale(key)
            if st is not None:
                return st
            raise
        meta = res.get("meta") or {}
        ccy, div = str(meta.get("currency") or "EUR"), 1.0
        if ccy in ("GBp", "GBX"):
            ccy, div = "GBP", 100.0
        rate = self._fx(ccy) or 1.0
        closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        out, seen = [], set()
        for t, c in zip(res.get("timestamp") or [], closes):
            if not isinstance(c, (int, float)):
                continue
            d = _day(t)
            if d in seen:
                out[-1][1] = round(c / div * rate, 4)
                continue
            seen.add(d)
            out.append([d, round(c / div * rate, 4)])
        self._put(key, out)
        return out


def _day(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
