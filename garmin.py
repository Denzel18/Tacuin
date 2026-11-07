"""
Garmin Connect «Personal API»: legge le attività del proprio account Garmin con una chiave personale.

La chiave si crea dal sito di Garmin Connect (Impostazioni → Personal API key). È gratuita per uso personale
e non serve nessuna approvazione. Qui: chiave → token di accesso → elenco delle attività → allenamenti del Tracker.
"""
import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

log = logging.getLogger("tracker")
BASE = os.getenv("GARMIN_API_URL", "https://personal.connectapi.garmin.com").rstrip("/")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
ACTIVITIES_PATH = "/activitylist-service/activities/search/activities"
STEPS_PATH = "/usersummary-service/stats/steps/daily/{start}/{end}"     # passi giorno per giorno
HR_PATH = "/wellness-service/wellness/dailyHeartRate"                  # battito del giorno (?date=AAAA-MM-GG)


class GarminError(ValueError):
    pass


class Garmin:
    def __init__(self, ssl_ctx=None):
        self.ctx = ssl_ctx

    def _req(self, method, path, headers, query=None, accept="application/json", fase=""):
        """Ritorna (corpo_bytes, content_type). Su 406 (formato non accettato) riprova una volta con Accept: */*."""
        url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
        for acc in dict.fromkeys([accept, "*/*"]):
            # niente corpo e niente Content-Type: come nell'esempio ufficiale (curl -X POST con la sola chiave)
            req = urllib.request.Request(url, method=method, headers={"Accept": acc, "User-Agent": "Tracker/1.0", **headers})
            try:
                with urllib.request.urlopen(req, timeout=25, context=self.ctx) as r:
                    return r.read(), r.headers.get("Content-Type", "")
            except urllib.error.HTTPError as e:
                dettaglio = _dettaglio(e)
                if e.code == 406 and acc != "*/*":
                    log.info("Garmin %s: 406 con Accept %s, riprovo con */*", fase, acc)
                    continue
                log.warning("Garmin %s: errore %s %s", fase, e.code, dettaglio)
                if e.code in (401, 403):
                    raise GarminError("Garmin non accetta la chiave: controlla di averla copiata tutta e che non sia scaduta."
                                      + (f" (Garmin: {dettaglio})" if dettaglio else ""))
                if e.code == 429:
                    raise GarminError("Garmin dice di aspettare un po' prima di sincronizzare di nuovo.")
                raise GarminError(f"Garmin ha risposto con errore {e.code} {fase}".strip() + (f": {dettaglio}" if dettaglio else "."))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise GarminError(f"Garmin non è raggiungibile ({getattr(e, 'reason', e)}).")
        raise GarminError(f"Garmin ha risposto con errore 406 {fase}".strip() + ".")

    def _json(self, body):
        try:
            return json.loads(body or b"null")
        except ValueError:
            raise GarminError("Risposta di Garmin non leggibile.")

    def token(self, key):
        """Scambia la chiave personale con un token (valido circa 24 ore). Ritorna (token, scadenza_epoch).
        Garmin può rispondere con un JSON ({"access_token": ...}) oppure con il token (JWT) come testo semplice."""
        body, ctype = self._req("POST", TOKEN_PATH, {"Authorization": f"ApiKey {key.strip()}"}, accept="*/*", fase="nel rilascio del token")
        testo = body.decode("utf-8", "replace").strip()
        j = None
        if testo[:1] in "{[\"":
            j = self._json(body)
        if isinstance(j, str):
            testo, j = j, None
        if isinstance(j, dict):
            tok = j.get("access_token") or j.get("accessToken") or j.get("token")
            exp = j.get("expires_at") or j.get("expiresAt")
            if not exp and j.get("expires_in"):
                exp = time.time() + float(j["expires_in"])
        else:
            tok, exp = (testo if re.fullmatch(r"[A-Za-z0-9._~+/=-]{20,}", testo or "") else None), None
        if not tok:
            raise GarminError("Garmin non ha restituito il token di accesso.")
        return tok, float(exp or _jwt_exp(tok) or time.time() + 23 * 3600)

    def activities(self, tok, start_date, limit=100):
        out, start = [], 0
        while start < 1000:
            body, _ = self._req("GET", ACTIVITIES_PATH, {"Authorization": f"Bearer {tok}"},
                                {"start": start, "limit": min(limit, 100), "startDate": start_date}, fase="nell'elenco delle attività")
            j = self._json(body)
            page = j if isinstance(j, list) else (j or {}).get("activities") or (j or {}).get("activityList") or []
            out += page
            if len(page) < min(limit, 100):
                break
            start += len(page)
        return out

    def daily_steps(self, tok, start, end):
        body, _ = self._req("GET", STEPS_PATH.format(start=start, end=end), {"Authorization": f"Bearer {tok}"}, fase="nei passi")
        return parse_steps(self._json(body))

    def daily_heart_rate(self, tok, day):
        body, _ = self._req("GET", HR_PATH, {"Authorization": f"Bearer {tok}"}, {"date": day}, fase="nel battito")
        return parse_hr(self._json(body))


def parse_steps(j):
    """→ {"AAAA-MM-GG": {"steps": n, "goal": n, "km": x}} (accetta lista di giorni o {"values": [...]})."""
    rows = j if isinstance(j, list) else (j or {}).get("values") or (j or {}).get("dailySteps") or []
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = str(r.get("calendarDate") or r.get("date") or "")[:10]
        v = r.get("values") if isinstance(r.get("values"), dict) else r
        steps = v.get("totalSteps", v.get("steps"))
        if not d or steps is None:
            continue
        dist = _num(v.get("totalDistance") or v.get("distance"))
        out[d] = {"steps": int(_num(steps)), "goal": int(_num(v.get("stepGoal") or v.get("dailyStepGoal"))) or None,
                  "km": round(dist / 1000, 2) if dist else None}
    return out


def parse_hr(j):
    """Battito del giorno → riposo, min, max, media 7 giorni, media degli ultimi 5 minuti registrati e l'ultima ora di valori."""
    j = j if isinstance(j, dict) else {}
    vals = []
    for p in j.get("heartRateValues") or []:
        if isinstance(p, (list, tuple)) and len(p) >= 2 and p[0] and p[1]:
            vals.append((int(_num(p[0])), int(_num(p[1]))))
        elif isinstance(p, dict) and p.get("value"):
            vals.append((int(_num(p.get("timestamp") or p.get("time"))), int(_num(p["value"]))))
    vals = sorted(v for v in vals if v[0] > 0 and 25 <= v[1] <= 240)
    out = {"date": str(j.get("calendarDate") or "")[:10] or None, "rest": _int(j.get("restingHeartRate")),
           "min": _int(j.get("minHeartRate")), "max": _int(j.get("maxHeartRate")), "avg7": _int(j.get("lastSevenDaysAvgRestingHeartRate")),
           "last5": None, "lastAt": None, "series": []}
    if vals:
        t_last = vals[-1][0]
        recenti = [b for t, b in vals if t >= t_last - 5 * 60 * 1000]
        out.update(last5=round(sum(recenti) / len(recenti)), lastAt=t_last,
                   series=[[t, b] for t, b in vals if t >= t_last - 60 * 60 * 1000])
    return out


def _int(v):
    n = _num(v)
    return int(round(n)) if n else None


def _dettaglio(e):
    """Il messaggio che Garmin mette nella risposta d'errore (breve, senza dati sensibili)."""
    try:
        t = e.read(400).decode("utf-8", "replace").strip()
    except Exception:
        return ""
    try:
        j = json.loads(t)
        t = j.get("message") or j.get("error_description") or j.get("error") or t if isinstance(j, dict) else t
    except ValueError:
        pass
    t = re.sub(r"<[^>]+>", " ", str(t))
    return re.sub(r"\s+", " ", t).strip()[:160]


def _jwt_exp(tok):
    """Scadenza (epoch) letta dal token JWT, se lo è."""
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp") or 0) or None
    except Exception:
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def activity_type(a):
    t = a.get("activityType")
    if isinstance(t, dict):
        return str(t.get("typeKey") or t.get("key") or "")
    return str(t or a.get("activityTypeKey") or a.get("sport") or "")


def parse_start(a):
    for k in ("startTimeLocal", "startTimeGMT", "startTime", "beginTimestamp"):
        v = a.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v)
        try:
            return datetime.fromisoformat(str(v).replace("Z", "").replace("T", " ")[:19])
        except ValueError:
            continue
    return None
