"""
"Cervello" del Tracker: piccoli algoritmi di machine learning e statistica, solo libreria standard.

- CategoryModel: classificatore Naive Bayes multinomiale che impara le categorie dalle spese
  dell'utente (e dalle sue correzioni) + memoria delle descrizioni già viste.
- fuzzy: correzione dei refusi (somiglianza tra stringhe).
- answer_question: domande in linguaggio naturale sulle spese ("quanto ho speso in svago a ottobre?").
- insights: previsione di fine mese, anomalie, spese insolite, abbonamenti ricorrenti, abitudini.
"""
import calendar
import difflib
import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta

MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre"]
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
STOP = {"di", "da", "del", "della", "dei", "delle", "il", "lo", "la", "le", "gli", "un", "una", "uno", "e", "con",
        "per", "a", "al", "alla", "in", "su", "the", "condivisa", "spesa", "euro", "eur"}


def norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


def num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def eur(x):
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s + " €"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tokens(text):
    """Parole + 'radici' (prime 5 lettere): pizza/pizze, ristorante/ristoranti finiscono insieme."""
    words = [w for w in re.findall(r"[a-z0-9]+", norm(re.sub(r"\(condivisa\)", "", text or ""))) if len(w) > 1 and w not in STOP]
    out = []
    for w in words:
        if w.isdigit():
            continue
        out.append("w:" + w)
        if len(w) >= 5:
            out.append("s:" + w[:4])  # radice corta: pizza/pizze/pizzeria, ristorante/ristoranti
        if len(w) >= 8:
            out.append("t:" + w[:6])
    return out


def clean_desc(d):
    return re.sub(r"\s*\(condivisa\)\s*$", "", str(d or "")).strip()


# ---------------------------------------------------------------- Naive Bayes
class NaiveBayes:
    """Naive Bayes multinomiale con smoothing di Laplace."""

    def __init__(self, alpha=1.0):
        self.alpha = alpha
        self.class_docs = Counter()
        self.word_counts = defaultdict(Counter)
        self.class_words = Counter()
        self.vocab = set()
        self.n_docs = 0

    def fit(self, docs, labels):
        for toks, y in zip(docs, labels):
            if not toks:
                continue
            self.class_docs[y] += 1
            self.n_docs += 1
            for t in toks:
                self.word_counts[y][t] += 1
                self.class_words[y] += 1
                self.vocab.add(t)
        return self

    def predict_proba(self, toks):
        toks = [t for t in toks if t in self.vocab]
        if not toks or not self.n_docs:
            return []
        V = len(self.vocab)
        logp = {}
        for c, nd in self.class_docs.items():
            lp = math.log(nd / self.n_docs)
            denom = self.class_words[c] + self.alpha * V
            for t in toks:
                lp += math.log((self.word_counts[c][t] + self.alpha) / denom)
            logp[c] = lp
        m = max(logp.values())
        exp = {c: math.exp(v - m) for c, v in logp.items()}
        z = sum(exp.values())
        return sorted(((c, v / z) for c, v in exp.items()), key=lambda x: -x[1])


class CategoryModel:
    """Impara dalle spese dell'utente quale categoria dare a una descrizione."""
    MIN_DOCS = 6

    def __init__(self, items, categories=None):
        self.memory = defaultdict(Counter)
        docs, labels = [], []
        for x in items:
            if x.get("type") != "out" or not x.get("category"):
                continue
            d = clean_desc(x.get("desc"))
            if not d:
                continue
            self.memory[norm(d)][x["category"]] += 1
            docs.append(tokens(d))
            labels.append(x["category"])
        self.allowed = set(categories) if categories else None
        self.nb = NaiveBayes().fit(docs, labels)
        self.docs, self.labels = docs, labels

    def predict(self, desc):
        """Ritorna dict(category, confidence, similar, source) oppure None se non è abbastanza sicuro."""
        d = norm(clean_desc(desc))
        if not d:
            return None
        if d in self.memory:
            c = self.memory[d]
            cat, n = c.most_common(1)[0]
            if not self.allowed or cat in self.allowed:
                return {"category": cat, "confidence": n / sum(c.values()), "similar": sum(c.values()), "source": "memoria"}
        if self.nb.n_docs < self.MIN_DOCS or len(self.nb.class_docs) < 2:
            return None
        toks = tokens(d)
        probs = [p for p in self.nb.predict_proba(toks) if not self.allowed or p[0] in self.allowed]
        if not probs or probs[0][1] < 0.55:
            return None
        cat, p = probs[0]
        ts = set(toks)
        similar = sum(1 for doc, y in zip(self.docs, self.labels) if y == cat and ts & set(doc))
        if not similar:
            return None
        return {"category": cat, "confidence": p, "similar": similar, "source": "modello"}


# ---------------------------------------------------------------- refusi
def fuzzy(word, choices, cutoff=0.75):
    """La scelta più simile a 'word' (tollerante ai refusi), oppure None."""
    w = norm(word)
    m = {norm(c): c for c in choices}
    r = difflib.get_close_matches(w, list(m), n=1, cutoff=cutoff)
    return m[r[0]] if r else None


# ---------------------------------------------------------------- periodi
def month_add(d, k):
    y, m = divmod(d.month - 1 + k, 12)
    return date(d.year + y, m + 1, 1)


def parse_period(t, today):
    """Ritorna (inizio, fine incluso, etichetta, tipo) dal testo; default: questo mese."""
    n = norm(t)
    m = re.search(r"ultim[ie]\s+(\d+)\s+(giorni|settimane|mesi)", n)
    if m:
        k, unit = int(m.group(1)), m.group(2)
        days = k if unit == "giorni" else k * 7 if unit == "settimane" else k * 30
        return today - timedelta(days=days - 1), today, f"negli ultimi {k} {unit}", "days"
    if "oggi" in n:
        return today, today, "oggi", "day"
    if re.search(r"\bieri\b", n):
        d = today - timedelta(days=1)
        return d, d, "ieri", "day"
    if "settimana scorsa" in n:
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6), "la settimana scorsa", "week"
    if "settimana" in n:
        start = today - timedelta(days=today.weekday())
        return start, today, "questa settimana", "week"
    if "mese scorso" in n:
        s = month_add(today.replace(day=1), -1)
        return s, today.replace(day=1) - timedelta(days=1), f"a {MESI[s.month - 1]}", "month"
    if "anno scorso" in n:
        y = today.year - 1
        return date(y, 1, 1), date(y, 12, 31), f"nel {y}", "year"
    if "quest'anno" in n or "questo anno" in n or "quest anno" in n or "nel " + str(today.year) in n:
        return date(today.year, 1, 1), today, f"nel {today.year}", "year"
    for i, mese in enumerate(MESI):
        if re.search(r"\b" + mese + r"\b", n):
            ym = re.search(mese + r"\s+(20\d\d)", n)
            y = int(ym.group(1)) if ym else (today.year if i + 1 <= today.month else today.year - 1)
            s = date(y, i + 1, 1)
            e = month_add(s, 1) - timedelta(days=1)
            return s, min(e, today) if s <= today else e, f"a {mese}" + (f" {y}" if y != today.year else ""), "month"
    s = today.replace(day=1)
    return s, today, f"a {MESI[today.month - 1]}", "month"


def in_range(x, s, e):
    try:
        d = date.fromisoformat(x.get("date", ""))
    except ValueError:
        return False
    return s <= d <= e


# ---------------------------------------------------------------- domande
QUESTION_RE = re.compile(r"^(quanto|quanti|quante|totale|spese|spesa totale|entrate|uscite|cosa ho speso|dove ho speso|in cosa ho speso)\b", re.I)


def is_question(text):
    t = norm(text)
    return bool(QUESTION_RE.match(t)) or (t.endswith("?") and re.search(r"\b(speso|spendo|spese|guadagnato|entrate|pagato)\b", t))


def answer_question(text, st, today=None):
    """Risponde a domande sulle spese. Ritorna (html, dati_grafico) oppure None."""
    today = today or date.today()
    t = norm(text)
    if not is_question(text):
        return None
    income = bool(re.search(r"guadagn|entrat|incass|stipend", t))
    typ = "in" if income else "out"
    s, e, label, kind = parse_period(t, today)
    E = st["expenses"]
    cats = E.get("categories", [])
    # categoria o parola cercata
    cat = None
    for c in sorted(cats, key=len, reverse=True):
        if re.search(r"\b" + re.escape(norm(c)) + r"\b", t):
            cat = c
            break
    term = None
    tq = re.sub(r"ultim[ie]\s+\d+\s+\w+|questo mese|mese scorso|questa settimana|settimana scorsa|quest'?\s?anno|anno scorso|"
                r"\boggi\b|\bieri\b|\b(?:a|di|in|nel|nella)\s+(?:" + "|".join(MESI) + r")(?:\s+20\d\d)?|\b(?:" + "|".join(MESI) + r")\b", " ", t)
    tq = re.sub(r"\s+", " ", tq).strip()
    if not cat:
        m = re.search(r"\b(?:in|di|per|al|alla|da|nel|nella|su)\s+([a-z0-9' ]+?)(?:\s+(?:a|in|nel|nella|negli|nei|questo|questa|quest|ultimi|ultime|oggi|ieri|la|il|mese|anno|settimana)\b|\?|$)", tq)
        if m:
            w = m.group(1).strip()
            w = re.sub(r"\b(questo|questa|mese|anno|settimana|scorso|scorsa|ultimi|ultime|giorni)\b", "", w).strip()
            if w and w not in MESI and not re.fullmatch(r"\d+", w):
                c2 = fuzzy(w, cats, 0.8)
                if c2:
                    cat = c2
                else:
                    term = w
    def match(x):
        if x.get("type") != typ:
            return False
        if cat and x.get("category") != cat:
            return False
        if term and term not in norm(x.get("desc", "")) and term not in norm(x.get("category", "")):
            return False
        return True
    sel = [x for x in E["items"] if match(x) and in_range(x, s, e)]
    tot = sum(num(x.get("amount")) for x in sel)
    what = esc(cat) if cat else (f"«{esc(term)}»" if term else ("entrate" if income else "uscite"))
    icon = "💰" if income else "💸"
    lines = [f"<i>{what.capitalize() if not cat else what} · {esc(label)} · {len(sel)} {'movimento' if len(sel) == 1 else 'movimenti'}</i>",
             f"{icon} <b>{eur(tot)}</b>"]
    # confronto con i mesi precedenti (stessa ricerca)
    chart = None
    if kind == "month":
        hist = []
        for k in range(5, -1, -1):
            ms = month_add(s, -k)
            me = month_add(ms, 1) - timedelta(days=1)
            v = sum(num(x.get("amount")) for x in E["items"] if match(x) and in_range(x, ms, me))
            hist.append({"label": MESI[ms.month - 1][:3], "value": round(v, 2)})
        prev = [h["value"] for h in hist[:-1] if h["value"] > 0][-3:]
        if prev:
            avg = sum(prev) / len(prev)
            if avg > 0:
                diff = (tot - avg) / avg * 100
                if abs(diff) < 3:
                    lines.append(f"In linea con la tua media ({eur(avg)})")
                else:
                    lines.append(f"{'▲' if diff > 0 else '▼'} {abs(diff):.0f}% rispetto alla tua media ({eur(avg)})")
        if any(h["value"] for h in hist):
            chart = hist
    top = sorted(sel, key=lambda x: -num(x.get("amount")))[:3]
    if top and len(sel) > 1:
        lines.append("")
        lines += [f"· {esc(clean_desc(x.get('desc')) or x.get('category'))} — {eur(num(x.get('amount')))}" for x in top]
    if not sel:
        lines.append("Nessun movimento trovato." + ("" if cat or term else ""))
    return "\n".join(lines), chart


# ---------------------------------------------------------------- insight
def _month_totals(items, typ="out", cat=None):
    tot = defaultdict(float)
    for x in items:
        if x.get("type") != typ or (cat and x.get("category") != cat):
            continue
        ym = str(x.get("date", ""))[:7]
        if len(ym) == 7:
            tot[ym] += num(x.get("amount"))
    return tot


def insights(st, today=None):
    today = today or date.today()
    out = []
    E = st["expenses"]
    items = E["items"]
    ym = today.strftime("%Y-%m")
    prev_months = [month_add(today.replace(day=1), -k).strftime("%Y-%m") for k in (1, 2, 3)]

    # 1. previsione di fine mese
    totals = _month_totals(items)
    spent = totals.get(ym, 0.0)
    days_in = calendar.monthrange(today.year, today.month)[1]
    hist = [totals[m] for m in prev_months if totals.get(m)]
    if spent > 0:
        pace = spent / today.day * days_in
        if hist:
            avg = sum(hist) / len(hist)
            w = today.day / days_in
            forecast = w * pace + (1 - w) * max(avg, spent)
        else:
            avg, forecast = None, pace
        forecast = max(forecast, spent)
        shown = math.ceil(forecast / 10) * 10 if forecast > 100 else forecast
        txt = f"Al ritmo attuale chiuderai {MESI[today.month - 1]} a circa <b>{eur(shown)}</b> di uscite."
        if avg:
            txt += f" La tua media degli ultimi mesi è {eur(avg)}."
        out.append({"kind": "forecast", "icon": "📈", "title": "Previsione di fine mese", "text": txt, "prio": 1,
                    "data": {"spent": round(spent, 2), "forecast": round(forecast, 2), "avg": round(avg, 2) if avg else None}})

    # 2. categorie sopra la media
    anomalies = []
    for c in E.get("categories", []):
        ct = _month_totals(items, cat=c)
        cur = ct.get(ym, 0.0)
        ph = [ct.get(m, 0.0) for m in prev_months]
        if sum(1 for v in ph if v > 0) < 2 or cur <= 0:
            continue
        avg = sum(ph) / len(ph)
        if avg > 0 and cur > avg * 1.4 and cur - avg > 20:
            anomalies.append((c, cur, avg, (cur - avg) / avg * 100))
    anomalies.sort(key=lambda a: -(a[1] - a[2]))
    # 3. spesa singola insolita (ultimi 30 giorni)
    unusual = None
    by_cat = defaultdict(list)
    for x in items:
        if x.get("type") == "out":
            by_cat[x.get("category")].append(x)
    for c, xs in by_cat.items():
        recent = [x for x in xs if in_range(x, today - timedelta(days=30), today)]
        older = [num(x.get("amount")) for x in xs if x not in recent]
        if len(older) < 5 or not recent:
            continue
        mu, sd = statistics.mean(older), statistics.pstdev(older)
        for x in recent:
            a = num(x.get("amount"))
            if a > mu + 3 * sd and a > 2 * mu and a > 30 and (not unusual or a / mu > unusual[2]):
                unusual = (x, c, a / mu)
    if anomalies or unusual:
        parts = []
        if anomalies:
            c, cur, avg, pct = anomalies[0]
            parts.append(f"<b>{esc(c)}</b> è a {eur(cur)}, il <b>+{pct:.0f}%</b> rispetto ai tuoi soliti {eur(avg)} al mese.")
            if len(anomalies) > 1:
                parts.append("Anche sopra la media: " + ", ".join(esc(a[0]) for a in anomalies[1:3]) + ".")
        if unusual:
            x, c, ratio = unusual
            d = date.fromisoformat(x["date"])
            parts.append(f"Spesa insolita: <b>{esc(clean_desc(x.get('desc')) or c)} {eur(num(x['amount']))}</b> ({d.day}/{d.month}), "
                         f"{ratio:.0f} volte più del solito per «{esc(c)}».")
        out.append({"kind": "anomaly", "icon": "⚠️", "title": "Fuori dalla tua media", "text": " ".join(parts), "prio": 2})

    # 4. spese ricorrenti (abbonamenti)
    groups = defaultdict(list)
    horizon = month_add(today.replace(day=1), -6)
    for x in items:
        if x.get("type") != "out":
            continue
        try:
            d = date.fromisoformat(x.get("date", ""))
        except ValueError:
            continue
        if d < horizon:
            continue
        key = re.sub(r"[\d.,€]+", "", norm(clean_desc(x.get("desc")))).strip()
        if len(key) >= 3:
            groups[key].append((d, num(x.get("amount")), clean_desc(x.get("desc"))))
    rec = []
    for key, xs in groups.items():
        months = {d.strftime("%Y-%m") for d, _, _ in xs}
        if len(months) < 3:
            continue
        amts = [a for _, a, _ in xs]
        mu = statistics.mean(amts)
        if mu <= 0 or (len(amts) > 1 and statistics.pstdev(amts) / mu > 0.15):
            continue
        if len(xs) > len(months) * 1.5:  # più volte al mese: non è un abbonamento
            continue
        rec.append((xs[-1][2], mu))
    if rec:
        rec.sort(key=lambda r: -r[1])
        tot = sum(m for _, m in rec)
        chips = " · ".join(f"{esc(n)} {eur(m)}" for n, m in rec[:6])
        out.append({"kind": "recurring", "icon": "🔁", "title": "Spese ricorrenti trovate", "prio": 3,
                    "text": f"{len(rec)} {'spesa ricorrente' if len(rec) == 1 else 'spese ricorrenti'}, in totale <b>{eur(tot)} al mese</b> "
                            f"({eur(tot * 12)} all'anno): {chips}",
                    "data": {"items": [{"name": n, "amount": round(m, 2)} for n, m in rec]}})

    # 5. abitudini
    H = st["habits"]["list"]
    if H:
        def rate(h, start, end):
            c0 = max(start, date.fromisoformat(h.get("created") or start.isoformat()))
            days = (end - c0).days + 1
            if days <= 0:
                return None
            done = sum(1 for k in range(days) if (h.get("done") or {}).get((c0 + timedelta(days=k)).isoformat()))
            return done, days
        y = today - timedelta(days=1)
        cur = [rate(h, today - timedelta(days=14), y) for h in H]
        prv = [rate(h, today - timedelta(days=28), today - timedelta(days=15)) for h in H]
        c_done, c_days = sum(r[0] for r in cur if r), sum(r[1] for r in cur if r)
        p_done, p_days = sum(r[0] for r in prv if r), sum(r[1] for r in prv if r)
        parts = []
        if c_days >= 7:
            cr = c_done / c_days * 100
            txt = f"Costanza delle ultime 2 settimane: <b>{cr:.0f}%</b>"
            if p_days >= 7:
                d = cr - p_done / p_days * 100
                if abs(d) >= 3:
                    txt += f" ({'+' if d > 0 else ''}{d:.0f}% rispetto alle 2 precedenti)"
            parts.append(txt + ".")
        weekday = None
        for h in H:
            start = max(today - timedelta(days=56), date.fromisoformat(h.get("created") or today.isoformat()))
            if (today - start).days < 28:
                continue
            per = defaultdict(lambda: [0, 0])
            k = start
            while k < today:
                per[k.weekday()][1] += 1
                if (h.get("done") or {}).get(k.isoformat()):
                    per[k.weekday()][0] += 1
                k += timedelta(days=1)
            rates = {wd: d / n for wd, (d, n) in per.items() if n >= 3}
            if len(rates) < 7:
                continue
            avg = sum(rates.values()) / 7
            worst = min(rates, key=rates.get)
            best = max(rates, key=rates.get)
            gap = avg - rates[worst]
            if gap >= 0.3 and (not weekday or gap > weekday[0]):
                weekday = (gap, h["name"], worst, best, per[worst])
        if weekday:
            gap, name, worst, best, (d, n) = weekday
            parts.append(f"Il <b>{GIORNI[worst]}</b> salti spesso «{esc(name)}» (fatto {d} volte su {n}). Il giorno migliore è il {GIORNI[best]}.")
        if parts:
            out.append({"kind": "habits", "icon": "🗓", "title": "Abitudini", "text": " ".join(parts), "prio": 4})

    # 6. scadenze nei tracker
    late = 0
    for t in st.get("trackers", []):
        dcol = next((c for c in t.get("columns", []) if c.get("type") == "date"), None)
        scol = next((c for c in t.get("columns", []) if c.get("type") == "select"), None)
        ccol = next((c for c in t.get("columns", []) if c.get("type") == "checkbox"), None)
        if not dcol:
            continue
        for r in t.get("rows", []):
            cells = r.get("cells") or {}
            try:
                d = date.fromisoformat(str(cells.get(dcol["id"]) or ""))
            except ValueError:
                continue
            done = (scol and norm(cells.get(scol["id"]) or "") in {"fatto", "finito", "completato", "letto", "visto"}) or \
                   (ccol and cells.get(ccol["id"]) is True)
            if d < today and not done:
                late += 1
    if late:
        out.append({"kind": "late", "icon": "⏰", "title": "Scadenze superate", "prio": 2,
                    "text": f"Hai <b>{late}</b> {'voce scaduta' if late == 1 else 'voci scadute'} nei tracker. Scrivi «da fare» all'assistente per vederle."})
    out.sort(key=lambda i: i["prio"])
    return out


def insights_text(st, today=None):
    ins = insights(st, today)
    if not ins:
        return ("🧠 Non ho ancora abbastanza dati per delle analisi.\n"
                "Continua a registrare spese e abitudini: dopo qualche settimana troverò previsioni, anomalie e spese ricorrenti.")
    return "🧠 <b>Le tue analisi</b>\n\n" + "\n\n".join(f"{i['icon']} <b>{i['title']}</b>\n{i['text']}" for i in ins)
