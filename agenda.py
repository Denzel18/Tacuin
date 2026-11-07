"""
Calendario: appuntamenti scritti in italiano, ripetizioni e date.

Tutto qui è "puro" (niente rete né database), così si prova facilmente:
- parse_appointment("dentista mercoledì alle 15:30", adesso) → dict dell'appuntamento
- parse_day("cosa ho domani", oggi) → data
- expand(eventi, dal, al) → occorrenze (anche di quelli che si ripetono)
"""
import re
import unicodedata
from datetime import date, datetime, timedelta

GIORNI = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre"]
REPEAT_LABEL = {"week": "ogni settimana", "month": "ogni mese", "year": "ogni anno"}


def norm(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _month(word):
    w = norm(word).rstrip(".")
    if len(w) < 3:
        return None
    for i, m in enumerate(MESI):
        if m.startswith(w):
            return i + 1
    return None


# ------------------------------------------------------------------ ore
TIME_RE = re.compile(
    r"(?:\b(?:dalle|alle|ore|h|alla)\s*(\d{1,2})(?:[:.](\d{2}))?\b|\b(\d{1,2})[:.](\d{2})\b)"
    r"(?:\s*(?:-|–|alle|fino alle)\s*(\d{1,2})(?:[:.](\d{2}))?\b)?", re.I)


def _hm(h, m):
    h, m = int(h), int(m or 0)
    if 0 <= h <= 24 and 0 <= m < 60:
        return f"{h % 24:02d}:{m:02d}"
    return None


def find_time(text):
    """→ (inizio, fine, testo_senza_ora) oppure (None, None, testo)."""
    m = TIME_RE.search(text)
    if not m:
        return None, None, text
    start = _hm(m.group(1), m.group(2)) if m.group(1) else _hm(m.group(3), m.group(4))
    end = _hm(m.group(5), m.group(6)) if m.group(5) else None
    if not start:
        return None, None, text
    # "alle 3" di pomeriggio: le ore 1-7 senza "di mattina" diventano 13-19
    h = int(start[:2])
    if 1 <= h <= 7 and not re.search(r"\b(mattin\w*|notte|am)\b", norm(text)):
        start = f"{h + 12:02d}{start[2:]}"
        if end and int(end[:2]) <= 12:
            end = f"{int(end[:2]) + 12:02d}{end[2:]}"
    rest = (text[:m.start()] + " " + text[m.end():]).strip()
    rest = re.sub(r"\b(di|del)\s+(mattina|pomeriggio|sera)\b", " ", rest, flags=re.I)
    return start, end, re.sub(r"\s{2,}", " ", rest)


# ------------------------------------------------------------------ giorni
def find_day(text, today, future=True):
    """
    Cerca un giorno nel testo: oggi, domani, dopodomani, ieri, venerdì (prossimo), il 12, 12/11, 12 novembre,
    tra 3 giorni. Ritorna (data, testo_senza_giorno) oppure (None, testo).
    future=True: «venerdì» e «il 12» indicano il prossimo; False: l'ultimo passato (per «cosa è successo»).
    """
    t = text
    rules = [
        (r"\bdopodomani\b", lambda m: today + timedelta(days=2)),
        (r"\baltroieri\b|\bl'altro ieri\b", lambda m: today - timedelta(days=2)),
        (r"\bdomani\b", lambda m: today + timedelta(days=1)),
        (r"\bieri\b", lambda m: today - timedelta(days=1)),
        (r"\b(?:stasera|stamattina|oggi|stanotte)\b", lambda m: today),
        (r"\btra\s+(\d{1,3})\s+giorni\b", lambda m: today + timedelta(days=int(m.group(1)))),
        (r"\btra\s+una\s+settimana\b", lambda m: today + timedelta(days=7)),
    ]
    nt = norm(t)
    for pat, fn in rules:
        m = re.search(pat, nt, re.I)
        if m:
            return fn(m), _cut(t, m)
    # 12/11 o 12/11/2026
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", nt)
    if m:
        d = _mkdate(today, int(m.group(1)), int(m.group(2)), m.group(3), future)
        if d:
            return d, _cut(t, m)
    # 12 novembre (2026)
    m = re.search(r"\b(?:il\s+|l')?(\d{1,2})\s+([a-z]{3,9})\.?(?:\s+(\d{4}))?\b", nt)
    if m and _month(m.group(2)):
        d = _mkdate(today, int(m.group(1)), _month(m.group(2)), m.group(3), future)
        if d:
            return d, _cut(t, m)
    # venerdì / venerdì prossimo / lunedì scorso
    m = re.search(r"\b(?:(?:il|di|la|questo|questa|prossimo|prossima)\s+)?(lunedi|martedi|mercoledi|giovedi|venerdi|sabato|domenica)"
                  r"(?:\s+(prossim[oa]|scors[oa]))?\b", nt)
    if m:
        wd = GIORNI.index(m.group(1))
        mod = m.group(2) or ""
        if mod.startswith("scors") or not future:
            delta = (today.weekday() - wd) % 7 or (7 if mod.startswith("scors") else 0)
            d = today - timedelta(days=delta)
        else:
            delta = (wd - today.weekday()) % 7
            if mod.startswith("prossim") and delta == 0:
                delta = 7
            d = today + timedelta(days=delta)
        return d, _cut(t, m)
    # "il 12" da solo
    m = re.search(r"\b(?:il|l')\s*(\d{1,2})\b(?!\s*[:./,])", nt)
    if m:
        dd = int(m.group(1))
        y, mo = today.year, today.month
        try:
            d = date(y, mo, dd)
            if future and d < today:
                mo, y = (1, y + 1) if mo == 12 else (mo + 1, y)
                d = date(y, mo, dd)
            elif not future and d > today:
                mo, y = (12, y - 1) if mo == 1 else (mo - 1, y)
                d = date(y, mo, dd)
            return d, _cut(t, m)
        except ValueError:
            pass
    return None, text


def _mkdate(today, dd, mm, yy, future):
    try:
        if yy:
            y = int(yy) + (2000 if len(yy) == 2 else 0)
            return date(y, mm, dd)
        d = date(today.year, mm, dd)
        if future and d < today - timedelta(days=1):
            d = date(today.year + 1, mm, dd)
        if not future and d > today:
            d = date(today.year - 1, mm, dd)
        return d
    except ValueError:
        return None


def _cut(text, m):
    # le posizioni su testo normalizzato coincidono con l'originale (le lettere accentate restano 1 carattere in NFC)
    s = unicodedata.normalize("NFC", text)
    out = (s[:m.start()] + " " + s[m.end():]).strip()
    return re.sub(r"\s{2,}", " ", out)


# ------------------------------------------------------------------ appuntamenti
LEAD_RE = re.compile(r"^\s*/?(?:(?:aggiungi|crea|metti|segna|nuovo|nuova|fissa)\s+)?(?:un\s+|una\s+|l')?"
                     r"(?:appuntamento|appuntamenti|evento|impegno|promemoria|ricordami(?:\s+(?:di|che))?|agenda|calendario)\b[\s:,-]*", re.I)
REPEAT_RE = re.compile(r"\b(?:ogni|tutti i|tutte le)\s+(settimana|settimane|mese|mesi|anno|anni|"
                       r"lunedi|martedi|mercoledi|giovedi|venerdi|sabato|domenica|lunedì|martedì|mercoledì|giovedì|venerdì)\b", re.I)
FILLER = {"il", "lo", "la", "le", "i", "gli", "di", "del", "della", "alle", "alla", "ore", "per", "a", "e", "in", "con", "un", "una",
          "giorno", "prossimo", "prossima"}


def looks_like_appointment(text):
    """Vero per «dentista venerdì alle 15», «appuntamento meccanico domani», «riunione 12/11 ore 9»."""
    if LEAD_RE.match(text):
        return True
    if re.search(r"\bcompleanno\b", norm(text)) and find_day(text, date.today())[0]:
        return True
    start, _, rest = find_time(text)
    if not start:
        return False
    d, _ = find_day(rest, date.today())
    return d is not None or bool(REPEAT_RE.search(text))


def parse_appointment(text, now=None):
    """
    → dict(title, date, start, end, allDay, repeat, remind) oppure None se manca qualcosa di essenziale.
    Senza giorno: oggi se l'ora non è ancora passata, altrimenti domani.
    """
    now = now or datetime.now()
    today = now.date()
    explicit = bool(LEAD_RE.match(text))
    t = LEAD_RE.sub("", text, count=1).strip()
    repeat = None
    m = REPEAT_RE.search(t)
    wd_from_repeat = None
    if m:
        w = norm(m.group(1))
        repeat = "week" if w.startswith("settiman") or w in GIORNI else "month" if w.startswith("mes") else "year"
        if w in GIORNI:
            wd_from_repeat = GIORNI.index(w)
        t = (t[:m.start()] + " " + t[m.end():]).strip()
    start, end, t = find_time(t)
    d, t = find_day(t, today)
    if d is None and wd_from_repeat is not None:
        d = today + timedelta(days=(wd_from_repeat - today.weekday()) % 7)
    if d is None:
        if not start and not explicit:
            return None
        d = today
        if start and start <= now.strftime("%H:%M"):
            d = today + timedelta(days=1)
    if re.search(r"\bcompleanno\b", norm(t)) and not repeat:
        repeat = "year"
    words = [w for w in re.split(r"\s+", t) if w]
    while words and norm(words[0]) in FILLER:
        words.pop(0)
    while words and norm(words[-1]) in FILLER:
        words.pop()
    title = " ".join(words).strip(" ,.;:-")
    if not title:
        return None
    title = title[:1].upper() + title[1:]
    if start and not end:
        h, mi = map(int, start.split(":"))
        e = datetime(2000, 1, 1, h, mi) + timedelta(hours=1)
        end = e.strftime("%H:%M") if e.day == 1 else "23:59"
    return {"title": title[:120], "date": d.isoformat(), "start": start or "", "end": end or "", "allDay": not start,
            "repeat": repeat or "", "remind": "60" if start else "morning", "place": "", "notes": ""}


# ------------------------------------------------------------------ ripetizioni
def _nth(base, rep, i):
    try:
        if rep == "week":
            return base + timedelta(days=7 * i)
        if rep == "month":
            y, m = divmod(base.month - 1 + i, 12)
            return date(base.year + y, m + 1, base.day)
        if rep == "year":
            return date(base.year + i, base.month, base.day)
    except ValueError:
        return None  # es. 31 in un mese corto, 29 febbraio: quella volta si salta
    return None


def span_days(ev):
    """Giorni in più oltre al primo per un appuntamento su più giorni (0 se dura un giorno)."""
    try:
        n = (date.fromisoformat(ev.get("until") or "") - date.fromisoformat(ev.get("date") or "")).days
    except ValueError:
        return 0
    return max(0, min(n, 366))


def expand(events, start, end, span=False):
    """Occorrenze tra start ed end (date incluse): lista di (evento, data) ordinata per data e ora.
    span=True: gli appuntamenti su più giorni compaiono in ognuno dei loro giorni (per agenda e riepiloghi);
    altrimenti solo nel primo (per gli avvisi, che arrivano una volta sola)."""
    if span:
        out = []
        for ev in events or []:
            n = span_days(ev)
            for e, d in expand([ev], start - timedelta(days=n), end):
                for k in range(n + 1):
                    dd = d + timedelta(days=k)
                    if start <= dd <= end:
                        out.append((e, dd))
        out.sort(key=lambda x: (x[1], "" if x[0].get("allDay") else str(x[0].get("start") or "")))
        return out
    out = []
    for ev in events or []:
        try:
            base = date.fromisoformat(ev.get("date") or "")
        except ValueError:
            continue
        rep = ev.get("repeat") or ""
        if not rep:
            if start <= base <= end:
                out.append((ev, base))
            continue
        i = 0
        if rep == "week" and base < start:
            i = max(0, (start - base).days // 7)
        elif rep == "month" and base < start:
            i = max(0, (start.year - base.year) * 12 + start.month - base.month - 1)
        elif rep == "year" and base < start:
            i = max(0, start.year - base.year - 1)
        for _ in range(800):
            d = _nth(base, rep, i)
            i += 1
            if d is None:
                continue
            if d > end:
                break
            if d >= start:
                out.append((ev, d))
    out.sort(key=lambda x: (x[1], "" if x[0].get("allDay") else str(x[0].get("start") or "")))
    return out


def when_label(ev):
    if ev.get("allDay") or not ev.get("start"):
        return "tutto il giorno"
    return ev["start"] + (f"–{ev['end']}" if ev.get("end") else "")
