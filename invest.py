"""
Investimenti: conti dei titoli e piani di accumulo (PAC). Tutto "puro" (niente rete né database), così si prova facilmente.

Un piano: {id, item (id del titolo), amount (euro), every (mesi: 1, 2, 3, 6, 12), day (1-31), start (data ISO),
auto (registra da solo), done: [date ISO già registrate o saltate]}.
"""
import calendar
from datetime import date, timedelta

TYPES = ["ETF", "Azione", "Obbligazione", "Fondo", "Crypto", "Liquidità", "Altro"]
EVERY = (1, 2, 3, 6, 12)


def num(v):
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else 0.0
    except ValueError:
        return 0.0


def calc(x):
    inv, val = num(x.get("qty")) * num(x.get("avg")), num(x.get("qty")) * num(x.get("price"))
    return inv, val


def totals(items):
    inv = val = 0.0
    for x in items or []:
        a, b = calc(x)
        inv += a
        val += b
    return round(inv, 2), round(val, 2)


def _on(y, m, day):
    return date(y, m, min(int(day), calendar.monthrange(y, m)[1]))


def schedule(plan, until, limit=400):
    """Date dei versamenti dall'inizio del piano fino a `until` compreso."""
    try:
        start = date.fromisoformat(str(plan.get("start") or ""))
    except ValueError:
        return []
    every = int(plan.get("every") or 1) if int(plan.get("every") or 1) in EVERY else 1
    day = max(1, min(31, int(num(plan.get("day")) or start.day)))
    out, k = [], 0
    while len(out) < limit:
        y, m = divmod(start.month - 1 + k * every, 12)
        d = _on(start.year + y, m + 1, day)
        k += 1
        if d < start:
            continue
        if d > until:
            break
        out.append(d)
    return out


def next_date(plan, today):
    """Prossimo versamento da oggi in poi (anche oggi, se non è ancora registrato)."""
    done = set(plan.get("done") or [])
    for d in schedule(plan, today + timedelta(days=400)):
        if d >= today and d.isoformat() not in done:
            return d
    return None


def due(plan, today, back=24):
    """Versamenti arrivati (fino a oggi) e non ancora registrati: al massimo gli ultimi `back`."""
    done = set(plan.get("done") or [])
    return [d for d in schedule(plan, today) if d.isoformat() not in done][-back:]


def buy(item, amount, price):
    """Aggiunge un acquisto da `amount` euro al prezzo `price`: aggiorna quantità e prezzo medio. → quote comprate."""
    amount, price = num(amount), num(price)
    if amount <= 0 or price <= 0:
        return 0.0
    q0, a0 = num(item.get("qty")), num(item.get("avg"))
    q = amount / price
    item["qty"] = round(q0 + q, 6)
    item["avg"] = round((q0 * a0 + amount) / (q0 + q), 6)
    return q


def run_plans(inv, today):
    """Registra i versamenti automatici arrivati. → [(piano, titolo, data, quote, prezzo)] di quelli registrati."""
    items = {x.get("id"): x for x in inv.get("items") or []}
    out = []
    for p in inv.get("plans") or []:
        if not p.get("auto") or p.get("paused"):
            continue
        it = items.get(p.get("item"))
        if not it or num(it.get("price")) <= 0:
            continue
        for d in due(p, today):
            q = buy(it, p.get("amount"), it.get("price"))
            if not q:
                break
            p.setdefault("done", []).append(d.isoformat())
            inv.setdefault("log", []).append({"d": d.isoformat(), "item": it["id"], "plan": p.get("id"), "amount": num(p.get("amount")),
                                              "qty": round(q, 6), "price": num(it.get("price"))})
            out.append((p, it, d, q, num(it.get("price"))))
        p["done"] = sorted(set(p.get("done") or []))[-400:]
    inv["log"] = (inv.get("log") or [])[-2000:]
    return out


def snapshot(inv, today):
    """Valore e versato di oggi, per il grafico dell'andamento (uno al giorno, gli ultimi 3 anni)."""
    a, b = totals(inv.get("items"))
    h = inv.setdefault("hist", {})
    if not inv.get("items"):
        return False
    k = today.isoformat()
    if h.get(k) == [a, b]:
        return False
    h[k] = [a, b]
    if len(h) > 1100:
        for old in sorted(h)[:len(h) - 1100]:
            h.pop(old, None)
    return True
