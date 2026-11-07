"""
Viaggi: ricerca dei luoghi e meteo con Open-Meteo (gratuito, senza chiave API).

- geocode(q): città/luoghi -> nome, paese, coordinate
- weather(lat, lon, start, end): meteo attuale + previsioni giorno per giorno (fino a 16 giorni)
  e, per i viaggi più lontani, il "clima tipico" degli stessi giorni dell'anno scorso.
Le risposte restano in memoria per 30 minuti per non interrogare il servizio a ogni pagina.
"""
import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

import os  # noqa: E402

GEO_URL = os.getenv("OPEN_METEO_GEO_URL", "https://geocoding-api.open-meteo.com/v1/search")
FORECAST_URL = os.getenv("OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")
ARCHIVE_URL = os.getenv("OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive")
FORECAST_DAYS = 16

# codici meteo WMO -> (emoji, descrizione)
WMO = {0: ("☀️", "Sereno"), 1: ("🌤", "Poco nuvoloso"), 2: ("⛅", "Parzialmente nuvoloso"), 3: ("☁️", "Nuvoloso"),
       45: ("🌫", "Nebbia"), 48: ("🌫", "Nebbia con brina"), 51: ("🌦", "Pioviggine leggera"), 53: ("🌦", "Pioviggine"),
       55: ("🌧", "Pioviggine intensa"), 56: ("🌧", "Pioviggine gelata"), 57: ("🌧", "Pioviggine gelata"),
       61: ("🌦", "Pioggia leggera"), 63: ("🌧", "Pioggia"), 65: ("🌧", "Pioggia forte"), 66: ("🌧", "Pioggia gelata"),
       67: ("🌧", "Pioggia gelata forte"), 71: ("🌨", "Neve leggera"), 73: ("🌨", "Neve"), 75: ("❄️", "Neve forte"),
       77: ("🌨", "Nevischio"), 80: ("🌦", "Rovesci leggeri"), 81: ("🌧", "Rovesci"), 82: ("⛈", "Rovesci violenti"),
       85: ("🌨", "Rovesci di neve"), 86: ("❄️", "Rovesci di neve forti"), 95: ("⛈", "Temporale"),
       96: ("⛈", "Temporale con grandine"), 99: ("⛈", "Temporale con grandine forte")}


def wmo(code):
    return WMO.get(int(code) if code is not None else -1, ("🌡", "—"))


class Travel:
    def __init__(self, ssl_ctx=None):
        self.ctx = ssl_ctx
        self.cache = {}
        self.lock = threading.Lock()

    def _get(self, url, params, ttl=1800):
        full = url + "?" + urllib.parse.urlencode(params)
        with self.lock:
            hit = self.cache.get(full)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        req = urllib.request.Request(full, headers={"User-Agent": "Tracker personale"})
        try:
            with urllib.request.urlopen(req, timeout=15, context=self.ctx) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Servizio meteo non raggiungibile ({type(e).__name__}). Riprova più tardi.")
        with self.lock:
            if len(self.cache) > 500:
                self.cache.clear()
            self.cache[full] = (time.time(), data)
        return data

    def geocode(self, q, count=6):
        q = (q or "").strip()
        if len(q) < 2:
            return []
        d = self._get(GEO_URL, {"name": q, "count": count, "language": "it", "format": "json"}, ttl=86400)
        out = []
        for r in d.get("results") or []:
            out.append({"name": r.get("name"), "country": r.get("country") or "", "region": r.get("admin1") or "",
                        "lat": round(r["latitude"], 5), "lon": round(r["longitude"], 5),
                        "tz": r.get("timezone") or "auto", "pop": r.get("population") or 0})
        return out

    def weather(self, lat, lon, start=None, end=None, today=None):
        today = today or date.today()
        d = self._get(FORECAST_URL, {
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": FORECAST_DAYS,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,sunrise,sunset"})
        cur = d.get("current") or {}
        ic, desc = wmo(cur.get("weather_code"))
        out = {"current": {"temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
                           "wind": cur.get("wind_speed_10m"), "humidity": cur.get("relative_humidity_2m"),
                           "icon": ic, "desc": desc}, "days": [], "climate": None, "forecast_from": None}
        dl = d.get("daily") or {}
        s = date.fromisoformat(start) if start else None
        e = date.fromisoformat(end) if end else None
        for i, day in enumerate(dl.get("time") or []):
            dd = date.fromisoformat(day)
            if s and dd < s or e and dd > e:
                continue
            ic, desc = wmo((dl.get("weather_code") or [None])[i])
            out["days"].append({"date": day, "icon": ic, "desc": desc,
                                "max": (dl.get("temperature_2m_max") or [None])[i], "min": (dl.get("temperature_2m_min") or [None])[i],
                                "rain": (dl.get("precipitation_probability_max") or [None])[i],
                                "rain_mm": (dl.get("precipitation_sum") or [None])[i]})
        last = today + timedelta(days=FORECAST_DAYS - 1)
        if s and (e or s) > last:
            # date del viaggio (in parte) oltre le previsioni: clima degli stessi giorni dell'anno scorso
            out["forecast_from"] = max(today, s - timedelta(days=FORECAST_DAYS - 1)).isoformat() if s > last else None
            cs = max(s, last + timedelta(days=1)) if s <= last else s
            ce = e or s
            try:
                ly = lambda x: x.replace(year=x.year - 1) if not (x.month == 2 and x.day == 29) else x.replace(year=x.year - 1, day=28)
                a = self._get(ARCHIVE_URL, {"latitude": lat, "longitude": lon, "timezone": "auto",
                                            "start_date": ly(cs).isoformat(), "end_date": ly(min(ce, cs + timedelta(days=30))).isoformat(),
                                            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum"}, ttl=86400 * 7)
                ad = a.get("daily") or {}
                mx = [v for v in ad.get("temperature_2m_max") or [] if v is not None]
                mn = [v for v in ad.get("temperature_2m_min") or [] if v is not None]
                rain = [v for v in ad.get("precipitation_sum") or [] if v is not None]
                if mx and mn:
                    out["climate"] = {"year": ly(cs).year, "max": round(sum(mx) / len(mx), 1), "min": round(sum(mn) / len(mn), 1),
                                      "rainy_days": sum(1 for v in rain if v >= 1), "days": len(mx)}
            except ValueError:
                pass
        return out


def fmt_t(v):
    return "–" if v is None else f"{round(v):.0f}°"


def weather_text(name, w, esc=lambda x: x):
    """Testo per bot / chat (HTML semplice)."""
    c = w["current"]
    lines = [f"{c['icon']} <b>{esc(name)}</b>: {fmt_t(c['temp'])} · {c['desc']}"
             + (f" · percepiti {fmt_t(c['feels'])}" if c.get("feels") is not None else "")
             + (f" · vento {round(c['wind'])} km/h" if c.get("wind") is not None else "")]
    if w["days"]:
        lines.append("")
        nomi = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
        for d in w["days"][:7]:
            dd = date.fromisoformat(d["date"])
            rain = f" · 💧{d['rain']}%" if d.get("rain") else ""
            lines.append(f"{nomi[dd.weekday()]} {dd.day}: {d['icon']} {fmt_t(d['min'])} / {fmt_t(d['max'])}{rain}")
    if w.get("climate"):
        cl = w["climate"]
        lines.append(f"\n📅 Clima tipico (stessi giorni del {cl['year']}): {fmt_t(cl['min'])} / {fmt_t(cl['max'])}, "
                     f"{cl['rainy_days']} {'giorno' if cl['rainy_days'] == 1 else 'giorni'} di pioggia su {cl['days']}")
    if w.get("forecast_from"):
        f = date.fromisoformat(w["forecast_from"])
        lines.append(f"<i>Le previsioni per le date del viaggio arrivano dal {f.day}/{f.month}.</i>")
    return "\n".join(lines)
