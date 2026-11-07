"""
Alimentazione per l'assistente: capisce frasi come «1 banana a colazione», «350 g di pasta al ragù a cena»,
«ho bevuto 2 bicchieri d'acqua» e le trasforma in voci del diario (stessa forma che usa l'app).
Tutto puro: niente rete né database, la tabella degli alimenti è quella dell'app (web/foods.js).
"""
import re
import secrets
import unicodedata
from datetime import datetime
from pathlib import Path

SLOTS = [("Colazione", "07:30"), ("Spuntino di metà mattina", "10:30"), ("Pranzo", "13:00"), ("Spuntino del pomeriggio", "16:30"), ("Cena", "20:00")]
RULES = ("zu", "fv", "al", "fr")
NUMS = {"un": 1, "uno": 1, "una": 1, "un'": 1, "mezzo": 0.5, "mezza": 0.5, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
        "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "qualche": 2, "un paio di": 2}
UNITS = {"g": "g", "gr": "g", "grammi": "g", "grammo": "g", "kg": "kg", "ml": "ml", "cl": "cl", "l": "l", "litro": "l", "litri": "l"}
# parole di un momento della giornata → come riconoscere il momento tra quelli dell'utente
MEAL_WORDS = [
    (r"meta\s+mattin\w*|spuntino\s+(?:di\s+|della\s+)?mattin\w*|mattin\w*", ("mattin",)),
    (r"meta\s+pomeriggio|spuntino\s+(?:del\s+)?pomeriggio|merenda|pomeriggio", ("pomerigg", "merenda")),
    (r"colazione", ("colazion",)),
    (r"pranzo", ("pranz",)),
    (r"cena", ("cena",)),
    (r"spuntino", ("spuntin",)),
]
MEAL_RE = re.compile(r"\b(?:a|per|al|alla|di|come|in|durante\s+la|durante\s+il|dopo|uno|un)\s+(" + "|".join(w for w, _ in MEAL_WORDS) + r")\b")
VERB_RE = re.compile(r"^(?:oggi\s+|ieri\s+)?(?:ho\s+)?(?:mangiato|bevuto|mangio|bevo|preso|cibo|pasto|diario alimentare)\b[:\s]*")
WATER_RE = re.compile(r"^(?:(?:ho\s+)?bevuto\s+|bevo\s+|\+\s*)?(\d+|un|uno|una|due|tre|quattro|cinque|sei|sette|otto|mezzo)?\s*"
                      r"(?:bicchier[ei]|bottigliett[ae]|bottigli[ae])?\s*(?:d'|di\s+)?acqua$")
MONEY_RE = re.compile(r"€|\beur(?:o|i)?\b|\d+,\d{2}\b")
UID_ALPHA = "abcdefghijklmnopqrstuvwxyz0123456789"


def fold(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("’", "'").strip()


def uid():
    return "".join(secrets.choice(UID_ALPHA) for _ in range(8))


def load_foods(path):
    """Tabella dell'app: «nome|kcal|p|c|f|etichette|porzione:grammi,…» per riga."""
    try:
        raw = Path(path).read_text("utf-8")
    except OSError:
        return []
    body = raw.split("`", 2)[1] if raw.count("`") >= 2 else ""
    out = []
    for line in body.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 7:
            continue
        name, k, p, c, f, tags, por = parts[:7]
        portions = []
        for x in por.split(","):
            if ":" in x:
                a, g = x.rsplit(":", 1)
                try:
                    portions.append((a.strip(), float(g)))
                except ValueError:
                    pass
        try:
            out.append({"name": name.strip(), "k": float(k), "p": float(p), "c": float(c), "f": float(f), "tags": tags.split(), "portions": portions})
        except ValueError:
            continue
    return out


STOP = {"di", "del", "della", "dello", "dei", "degli", "delle", "al", "alla", "allo", "ai", "agli", "alle", "con", "e", "il", "lo", "la", "i",
        "gli", "le", "un", "una", "uno", "in", "a", "da"}


def _words(s):
    return [w for w in re.split(r"[^a-z0-9]+", fold(s)) if w and w not in STOP]


def _stem(w):
    return w[:-1] if len(w) > 3 and w[-1] in "aeio" else w


ALIASES = {"caffe": "Caffè espresso", "espresso": "Caffè espresso", "te": "Tè senza zucchero", "the": "Tè senza zucchero", "yogurt": "Yogurt bianco intero",
           "pane": "Pane comune", "pasta": "Pasta di semola (cruda)", "riso": "Riso bianco (crudo)", "latte": "Latte parzialmente scremato", "insalata": "Insalata verde",
           "pollo": "Petto di pollo", "vino": "Vino rosso", "uovo": "Uova", "uova": "Uova", "pizza": "Pizza margherita", "tonno": "Tonno al naturale",
           "succo": "Succo di frutta", "biscotti": "Biscotti secchi", "biscotto": "Biscotti secchi", "cornetto": "Cornetto vuoto", "brioche": "Brioche",
           "cioccolato": "Cioccolato fondente", "patatine": "Patatine in busta", "frutta secca": "Frutta secca mista", "spremuta": "Spremuta d'arancia",
           "olio": "Olio extravergine d'oliva", "parmigiano": "Parmigiano", "prosciutto": "Prosciutto cotto", "fagioli": "Fagioli (cotti)",
           "ceci": "Ceci (cotti)", "lenticchie": "Lenticchie (cotte)", "polenta": "Polenta (cotta)", "patata": "Patate", "verdure": "Verdure grigliate"}


def match_food(name, foods):
    """Alimento della tabella che corrisponde meglio a `name`, o None se non ce n'è uno abbastanza simile."""
    q = fold(name)
    if not q:
        return None
    if q in ALIASES:
        f = next((x for x in foods if x["name"] == ALIASES[q]), None)
        if f:
            return f
    qw = [_stem(w) for w in _words(q)]
    best, score = None, 0.0
    for f in foods:
        fn = fold(f["name"])
        if fn == q:
            return f
        fw = [_stem(w) for w in _words(fn)]
        if not fw or not qw:
            continue
        hit = sum(1 for w in qw if w in fw)
        if hit < len(qw):  # ogni parola cercata deve esserci («pasta al ragù» non è «pasta di semola»)
            continue
        s = hit / len(fw) + (0.3 if fn.startswith(q) else 0) - 0.01 * len(fn)
        if s > score:
            best, score = f, s
    return best


def _num(s):
    s = fold(s)
    if s in NUMS:
        return float(NUMS[s])
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def parse_item(text, foods):
    """«350 g di pasta al ragù» → voce del diario (senza momento)."""
    t = fold(text).strip(" .,;")
    t = re.sub(r"^(?:anche|poi|e)\s+", "", t)
    q, unit, name = None, None, t
    m = re.match(r"^(\d+(?:[.,]\d+)?|un paio di|" + "|".join(sorted((k for k in NUMS if k != "un paio di"), key=len, reverse=True)) + r")\s*(.*)$", t)
    if m and (m.group(1)[0].isdigit() or m.group(2)):
        q, rest = _num(m.group(1)), m.group(2).strip()
        mu = re.match(r"^(" + "|".join(sorted(UNITS, key=len, reverse=True)) + r")\b\.?\s*(?:di\s+|d')?(.*)$", rest)
        if mu:
            unit, name = UNITS[mu.group(1)], mu.group(2).strip()
        else:
            name = rest
    if unit == "kg":
        q, unit = q * 1000, "g"
    elif unit in ("cl", "l"):
        q, unit = q * (10 if unit == "cl" else 1000), "ml"
    # «un bicchiere di latte», «2 fette di pane»: la porzione detta a parole
    por_word = None
    mp = re.match(r"^([a-z']+)\s+(?:di\s+|d')(.+)$", name)
    if mp and not unit:
        por_word, name2 = mp.group(1), mp.group(2)
        food = match_food(name2, foods)
        if food:
            pw = _stem(por_word)
            hit = next((p for p in food["portions"] if _stem(fold(p[0]).split()[0]) == pw), None)
            if hit:
                return entry(food, q or 1, hit[0])
            name = name2
    name = name.strip(" .,;") or t
    food = match_food(name, foods)
    if not food:
        label = name[:1].upper() + name[1:]
        e = {"id": uid(), "name": label[:80], "q": q or 1, "u": unit or "", "tags": [], "src": "free"}
        return e
    if unit in ("g", "ml"):
        return entry(food, q, unit)
    if food["portions"]:
        return entry(food, q or 1, food["portions"][0][0])
    return entry(food, (q or 1) * 100, "g")


def entry(food, q, unit):
    por = next((p for p in food["portions"] if p[0] == unit), None)
    g = q if unit in ("g", "ml") else (q * por[1] if por else None)
    e = {"id": uid(), "name": food["name"], "q": round(q, 2), "u": unit, "tags": list(food["tags"]), "src": "gen"}
    if g is not None:
        e["g"] = round(g)
        e["kcal"] = round(food["k"] * g / 100)
        for k in ("p", "c", "f"):
            e[k] = round(food[k] * g / 100, 1)
    return e


def slots(nut):
    if not isinstance(nut.get("slots"), list) or not nut["slots"]:
        nut["slots"] = [{"id": uid(), "name": n, "time": t} for n, t in SLOTS]
    return nut["slots"]


def find_slot(nut, word, now):
    """Momento dell'utente che corrisponde alla parola («cena», «merenda»…); senza parola, quello dell'ora attuale."""
    S = slots(nut)
    if word:
        w = fold(word)
        for pat, keys in MEAL_WORDS:
            if re.fullmatch(pat, w):
                if keys == ("spuntin",):  # «spuntino» da solo: mattina prima delle 12, altrimenti pomeriggio
                    keys = ("mattin",) if now.hour < 12 else ("pomerigg", "merenda")
                for s in S:
                    if any(k in fold(s.get("name")) for k in keys):
                        return s
                for s in S:
                    if any(k[:4] in fold(s.get("name")) for k in keys):
                        return s
    hm, best = now.strftime("%H:%M"), S[0]
    for s in S:
        if s.get("time") and s["time"] <= hm:
            best = s
    return best


def looks_like_food(t):
    """Frase da diario alimentare? (non una spesa: niente euro, niente cifre con i centesimi)."""
    n = fold(t)
    if MONEY_RE.search(t) or not n:
        return False
    if WATER_RE.fullmatch(n):
        return True
    if VERB_RE.match(n):
        return True
    m = MEAL_RE.search(n) or re.match(r"^(" + "|".join(w for w, _ in MEAL_WORDS) + r")\b\s*[:,]?", n)
    if not m:
        return False
    rest = (n[:m.start()] + " " + n[m.end():]).strip(" :,")
    if not rest or re.match(r"^(da|dai|dalla|dal|fuori|al ristorante|in pizzeria|insieme)\b", rest):
        return False  # «pranzo da mamma», «cena fuori»: non dice cosa hai mangiato
    if re.search(r"\b(alle|ore|domani|dopodomani|lunedi|martedi|mercoledi|giovedi|venerdi|sabato|domenica)\b", n) or re.search(r"\bcon\s+[A-ZÀ-Ý]", str(t)):
        return False  # «cena con Marco alle 20» è un appuntamento
    for x in re.findall(r"\d+(?:[.,]\d+)?", rest):  # «pizza a cena 15» sembra una spesa: un numero grande senza unità non è una quantità
        if float(x.replace(",", ".")) > 12 and not re.search(re.escape(x) + r"\s*(g|gr|grammi|kg|ml|cl|l)\b", rest):
            return False
    return True


def parse(text, foods, nut, now):
    """→ (giorno ISO, momento, [voci], bicchieri d'acqua)."""
    n = fold(text).strip(" .!")
    day = now.date()
    if re.match(r"^ieri\b", n) or " ieri" in n:
        from datetime import timedelta
        day = day - timedelta(days=1)
        n = re.sub(r"\bieri\b", " ", n)
    n = re.sub(r"\boggi\b", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    wm = WATER_RE.fullmatch(VERB_RE.sub("", n).strip()) or WATER_RE.fullmatch(n)
    if wm:
        return day.isoformat(), None, [], int(_num(wm.group(1)) or 1) if wm.group(1) else 1
    m = MEAL_RE.search(n) or re.match(r"^(" + "|".join(w for w, _ in MEAL_WORDS) + r")\b\s*[:,]?", n)
    word = m.group(1) if m else None
    body = (n[:m.start()] + " " + n[m.end():]) if m else n
    body = VERB_RE.sub("", body.strip()).strip(" :,")
    slot = find_slot(nut, word, now)
    # i piatti con «e» o «con» nel nome non vanno spezzati («pasta e fagioli»)
    for f in sorted(foods, key=lambda x: -len(x["name"])):
        fn = fold(f["name"])
        if (" e " in fn or " con " in fn) and fn in body:
            body = body.replace(fn, fn.replace(" e ", " _e_ ").replace(" con ", " _con_ "))
    water, items = 0, []
    for part in re.split(r"\s*(?:,|;|\+|\be\b|\bpiu\b|\bcon\b)\s*", body):
        part = part.replace(" _e_ ", " e ").replace(" _con_ ", " con ")
        if not part.strip():
            continue
        pw = WATER_RE.fullmatch(part.strip())
        if pw:
            water += int(_num(pw.group(1)) or 1) if pw.group(1) else 1
            continue
        e = parse_item(part, foods)
        e["slot"] = slot["id"]
        items.append(e)
    return day.isoformat(), slot, items, water


def habit_sync(st, day):
    """Abitudini «dal diario»: stessa regola dell'app."""
    days = ((st.get("nutrition") or {}).get("days") or {})
    lst = (days.get(day) or {}).get("e") or []
    for h in (st.get("habits") or {}).get("list") or []:
        if h.get("kind") != "diet" or h.get("rule") not in RULES or (h.get("lock") or {}).get(day):
            continue
        if h["rule"] == "fv":
            n = sum(max(1, round(float(e.get("q") or 1))) if e.get("u") and e.get("u") not in ("g", "ml") else 1 for e in lst if "fv" in (e.get("tags") or []))
            ok = bool(lst) and n >= 5
        else:
            ok = bool(lst) and not any(h["rule"] in (e.get("tags") or []) for e in lst)
        done = h.setdefault("done", {})
        if ok:
            done[day] = True
        else:
            done.pop(day, None)


def water_add(st, day, n):
    """+n bicchieri all'abitudine «Acqua» (la crea se manca, come l'app). → (valore, obiettivo)"""
    H = st.setdefault("habits", {}).setdefault("list", [])
    h = next((x for x in H if x.get("kind") == "count" and x.get("link") == "water"), None)
    if not h:
        h = {"id": uid(), "name": "Acqua", "created": day, "done": {}, "kind": "count", "target": 8, "unit": "bicchieri", "link": "water", "val": {}}
        H.append(h)
    val = h.setdefault("val", {})
    v = max(0, min(99, int(val.get(day) or 0) + n))
    if v:
        val[day] = v
    else:
        val.pop(day, None)
    if v >= int(h.get("target") or 1):
        h.setdefault("done", {})[day] = True
    else:
        h.setdefault("done", {}).pop(day, None)
    if not h.get("created") or day < h["created"]:
        h["created"] = day
    return v, int(h.get("target") or 8)


PLURAL = {"uovo": "uova", "paio": "paia", "uomo": "uomini"}


def plural(u):
    """«fetta» → «fette», «mela media» → «mele medie» (solo per le misure delle porzioni)."""
    out = []
    for w in u.split():
        if w in PLURAL:
            out.append(PLURAL[w])
        elif w.endswith("io"):
            out.append(w[:-1])
        elif w.endswith(("ca", "ga")):
            out.append(w[:-1] + "he")
        elif w.endswith("a"):
            out.append(w[:-1] + "e")
        elif w.endswith(("o", "e")):
            out.append(w[:-1] + "i")
        else:
            out.append(w)
    return " ".join(out)


def qty_label(e):
    q = e.get("q") or 1
    qs = (f"{q:g}").replace(".", ",")
    u = e.get("u") or ""
    if u in ("g", "ml"):
        return f"{qs} {u}"
    if u and q > 1:
        u = plural(u)
    return f"{qs} {u}".strip() if u else ("" if q == 1 else qs)


def now_local():
    return datetime.now()
