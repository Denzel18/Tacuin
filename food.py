"""
Prodotti confezionati da Open Food Facts (banca dati libera, licenza ODbL, senza chiave).

Il server cerca per nome o codice a barre e restituisce solo nome, marca e valori ogni 100 g. Le risposte restano in cache
(ricerche 7 giorni, prodotti 30 giorni) per rispettare i limiti di Open Food Facts (10 ricerche e 100 prodotti al minuto).
Gli alimenti generici (banana, pasta…) non passano di qui: sono in una tabella dentro l'app.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Tacuin/1.0 (+https://github.com/Denzel18/tacuin)"
SEARCH_URL = os.getenv("FOOD_SEARCH_URL") or "https://it.openfoodfacts.org/cgi/search.pl?"
PRODUCT_URL = os.getenv("FOOD_PRODUCT_URL") or "https://world.openfoodfacts.org/api/v2/product/{}.json?"
FIELDS = "code,product_name,product_name_it,brands,nutriments,serving_quantity,categories_tags"
CODE_RE = re.compile(r"^\d{8,14}$")
SWEET = ("en:sweet", "en:sugary", "en:desserts", "en:confectioner", "en:biscuits", "en:cakes", "en:chocolates", "en:sweetened-beverages",
         "en:sodas", "en:ice-creams", "en:candies", "en:jams", "en:breakfast-cereals-with-sugar")
FRIED = ("en:chips", "en:crisps", "en:fries", "en:fried")
ALCOHOL = ("en:alcoholic-beverages",)
FV = ("en:fresh-fruits", "en:fresh-vegetables", "en:fruits", "en:vegetables")


class FoodError(Exception):
    pass


def _n(v):
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def normalize(p):
    """Prodotto Open Food Facts → {code, name, brand, k, p, c, f, tags, portions} (valori ogni 100 g)."""
    name = str(p.get("product_name_it") or p.get("product_name") or "").strip()[:100]
    if not name:
        return None
    n = p.get("nutriments") or {}
    k = _n(n.get("energy-kcal_100g"))
    if k is None and _n(n.get("energy_100g")) is not None:
        k = round(_n(n.get("energy_100g")) / 4.184, 1)
    cats = [str(c) for c in p.get("categories_tags") or []]
    tags = []
    sugars = _n(n.get("sugars_100g"))
    if any(c.startswith(SWEET) for c in cats) or (sugars is not None and sugars >= 15):
        tags.append("zu")
    if any(c.startswith(ALCOHOL) for c in cats) or (_n(n.get("alcohol_100g")) or 0) > 0.5:
        tags.append("al")
    if any(c.startswith(FRIED) for c in cats):
        tags.append("fr")
    if any(c in FV for c in cats) and "zu" not in tags:
        tags.append("fv")
    portions = []
    sq = _n(p.get("serving_quantity"))
    if sq and 0 < sq < 2000:
        portions.append(["porzione", sq])
    return {"code": str(p.get("code") or "")[:14], "name": name, "brand": str(p.get("brands") or "").split(",")[0].strip()[:60],
            "k": k, "p": _n(n.get("proteins_100g")), "c": _n(n.get("carbohydrates_100g")), "f": _n(n.get("fat_100g")),
            "tags": tags, "portions": portions, "src": "off"}


class Foods:
    SEARCH_TTL = 7 * 86400
    PRODUCT_TTL = 30 * 86400
    SEARCH_PER_MIN = 8
    PRODUCT_PER_MIN = 60

    def __init__(self, path=None, fetch=None):
        self.path = path
        self.fetch = fetch or self._http
        self.lock = threading.Lock()
        self.calls = {"s": [], "p": []}
        self.cache = {}
        if path:
            try:
                self.cache = json.loads(path.read_text("utf-8"))
            except Exception:
                self.cache = {}

    def _http(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read(3_000_000).decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            raise FoodError(f"Open Food Facts ha risposto {e.code}")
        except Exception as e:
            raise FoodError("Open Food Facts non risponde") from e

    def _call(self, kind, url):
        now, cap = time.time(), self.SEARCH_PER_MIN if kind == "s" else self.PRODUCT_PER_MIN
        with self.lock:
            self.calls[kind] = [t for t in self.calls[kind] if t > now - 60]
            if len(self.calls[kind]) >= cap:
                raise FoodError("troppe ricerche di prodotti: riprova tra un minuto")
            self.calls[kind].append(now)
        return self.fetch(url)

    def _get(self, key, ttl):
        with self.lock:
            v = self.cache.get(key)
        return v["v"] if v and time.time() - v.get("at", 0) < ttl else None

    def _put(self, key, value):
        with self.lock:
            self.cache[key] = {"at": time.time(), "v": value}
            if len(self.cache) > 4000:
                for k, _ in sorted(self.cache.items(), key=lambda kv: kv[1].get("at", 0))[:600]:
                    self.cache.pop(k, None)
            raw = json.dumps(self.cache, separators=(",", ":"))
        if self.path:
            try:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(raw, "utf-8")
                tmp.replace(self.path)
            except Exception:
                pass

    def search(self, q):
        q = " ".join(str(q or "").split())[:60]
        if len(q) < 3:
            return []
        key = "s:" + q.lower()
        hit = self._get(key, self.SEARCH_TTL)
        if hit is not None:
            return hit
        r = self._call("s", SEARCH_URL + urllib.parse.urlencode(
            {"search_terms": q, "search_simple": 1, "action": "process", "json": 1, "page_size": 12, "fields": FIELDS}))
        out = [x for x in (normalize(p) for p in (r or {}).get("products") or []) if x and x["k"] is not None][:10]
        self._put(key, out)
        return out

    def product(self, code):
        code = str(code or "").strip()
        if not CODE_RE.fullmatch(code):
            raise FoodError("codice a barre non valido")
        key = "p:" + code
        hit = self._get(key, self.PRODUCT_TTL)
        if hit is not None:
            return hit or None
        r = self._call("p", PRODUCT_URL.format(code) + urllib.parse.urlencode({"fields": FIELDS}))
        p = normalize((r or {}).get("product") or {}) if (r or {}).get("product") else None
        self._put(key, p or {})
        return p
