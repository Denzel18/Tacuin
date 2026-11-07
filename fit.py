"""
Lettura minima dei file .FIT (Garmin e altri orologi): estrae i riepiloghi delle attività ("session").

Il formato FIT è pubblico (FIT SDK di Garmin). Qui leggiamo solo quello che serve:
sport, inizio, durata, distanza, calorie e battito. Accetta anche lo .zip che scarica Garmin Connect.
"""
import io
import struct
import zipfile
from datetime import datetime, timezone

FIT_EPOCH = 631065600  # 31/12/1989 00:00 UTC

# numeri dei campi del messaggio "session" (numero globale 18)
F_START, F_SPORT, F_SUB, F_ELAPSED, F_TIMER, F_DIST, F_CAL, F_AVGHR, F_MAXHR, F_TS = 2, 5, 6, 7, 8, 9, 11, 16, 17, 253
BASE_FMT = {0x00: ("B", 1), 0x01: ("b", 1), 0x02: ("B", 1), 0x83: ("h", 2), 0x84: ("H", 2), 0x85: ("i", 4), 0x86: ("I", 4),
            0x07: (None, 1), 0x88: ("f", 4), 0x89: ("d", 8), 0x0A: ("B", 1), 0x8B: ("H", 2), 0x8C: ("I", 4), 0x0D: (None, 1),
            0x8E: ("q", 8), 0x8F: ("Q", 8), 0x90: ("Q", 8)}
INVALID = {"B": 0xFF, "b": 0x7F, "h": 0x7FFF, "H": 0xFFFF, "i": 0x7FFFFFFF, "I": 0xFFFFFFFF}

SPORT = {1: "running", 2: "cycling", 4: "fitness_equipment", 5: "swimming", 10: "training", 11: "walking", 12: "cross_country_skiing",
         13: "alpine_skiing", 15: "rowing", 16: "mountaineering", 17: "hiking", 19: "paddling", 31: "rock_climbing", 41: "dance",
         47: "boxing", 62: "hiit", 83: "yoga"}
SUB_SPORT = {1: "treadmill", 6: "indoor_cycling", 14: "indoor_rowing", 15: "elliptical", 16: "stair_climbing", 17: "lap_swimming",
             18: "open_water", 19: "flexibility_training", 20: "strength_training", 26: "cardio_training", 43: "yoga", 44: "pilates",
             68: "indoor_climbing", 69: "bouldering", 70: "hiit"}


class FitError(ValueError):
    pass


def _unzip(data):
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = [i for i in z.infolist() if i.filename.lower().endswith(".fit")]
            if not infos:
                raise FitError("Nello .zip non c'è nessun file .FIT")
            # un file piccolo che si "gonfia" a gigabyte bloccherebbe il server: limiti su numero e dimensione
            if len(infos) > 50 or sum(i.file_size for i in infos) > 100 * 1024 * 1024:
                raise FitError("Lo .zip è troppo grande (al massimo 50 file .FIT e 100 MB in tutto)")
            out = []
            for i in infos:
                with z.open(i) as f:
                    b = f.read(50 * 1024 * 1024 + 1)
                if len(b) > 50 * 1024 * 1024:
                    raise FitError("Un file .FIT nello .zip è troppo grande")
                out.append(b)
            return out
    return [data]


def sessions(data):
    """Lista di dict(sport, sub_sport, start, elapsed_s, distance_m, calories, avg_hr, max_hr) da un file .FIT o .zip."""
    out = []
    for blob in _unzip(data):
        out += _parse(blob)
    if not out:
        raise FitError("Nel file non trovo nessuna attività")
    return out


def _parse(b):
    if len(b) < 14:
        raise FitError("File .FIT troppo corto")
    hsize = b[0]
    if b[8:12] != b".FIT":
        raise FitError("Non è un file .FIT")
    data_size = struct.unpack_from("<I", b, 4)[0]
    pos, end = hsize, min(len(b), hsize + data_size)
    defs, last_ts, found = {}, 0, []
    while pos < end:
        h = b[pos]
        pos += 1
        if h & 0x80:  # intestazione compressa con timestamp
            local, offset = (h >> 5) & 0x3, h & 0x1F
            last_ts = (last_ts & ~0x1F) + offset + (0x20 if offset < (last_ts & 0x1F) else 0)
            d = defs.get(local)
            if not d:
                raise FitError("File .FIT non valido (definizione mancante)")
            vals, pos = _read(b, pos, d)
            if d["global"] == 18:
                vals.setdefault(F_TS, last_ts)
                found.append(vals)
            continue
        local = h & 0x0F
        if h & 0x40:  # definizione
            arch = b[pos + 1]
            endian = ">" if arch == 1 else "<"
            gnum = struct.unpack_from(endian + "H", b, pos + 2)[0]
            n = b[pos + 4]
            pos += 5
            fields = []
            for _ in range(n):
                fields.append((b[pos], b[pos + 1], b[pos + 2]))
                pos += 3
            dev = 0
            if h & 0x20:
                nd = b[pos]
                pos += 1
                dev = sum(b[pos + 1 + 3 * i] for i in range(nd))
                pos += 3 * nd
            defs[local] = {"global": gnum, "endian": endian, "fields": fields, "dev": dev}
        else:
            d = defs.get(local)
            if not d:
                raise FitError("File .FIT non valido (definizione mancante)")
            vals, pos = _read(b, pos, d)
            if F_TS in vals:
                last_ts = vals[F_TS]
            if d["global"] == 18:
                found.append(vals)
    return [_session(v) for v in found if v.get(F_START) or v.get(F_TS)]


def _read(b, pos, d):
    vals = {}
    for num, size, base in d["fields"]:
        fmt = BASE_FMT.get(base, (None, 1))[0]
        if fmt and size == struct.calcsize(fmt):
            v = struct.unpack_from(d["endian"] + fmt, b, pos)[0]
            if INVALID.get(fmt) != v:
                vals[num] = v
        pos += size
    return vals, pos + d["dev"]


def _session(v):
    start = v.get(F_START) or v.get(F_TS)
    el = v.get(F_TIMER) or v.get(F_ELAPSED) or 0
    return {"sport": SPORT.get(v.get(F_SPORT), "generic"), "sub_sport": SUB_SPORT.get(v.get(F_SUB), ""),
            "start": datetime.fromtimestamp(start + FIT_EPOCH, tz=timezone.utc), "elapsed_s": el / 1000,
            "distance_m": (v.get(F_DIST) or 0) / 100, "calories": v.get(F_CAL) or 0, "avg_hr": v.get(F_AVGHR), "max_hr": v.get(F_MAXHR)}


def build(sess):
    """Crea un piccolo file .FIT con le sessioni date (serve ai test)."""
    recs = bytearray()
    fields = [(F_TS, 4, 0x86), (F_START, 4, 0x86), (F_SPORT, 1, 0x00), (F_SUB, 1, 0x00), (F_ELAPSED, 4, 0x86), (F_TIMER, 4, 0x86),
              (F_DIST, 4, 0x86), (F_CAL, 2, 0x84), (F_AVGHR, 1, 0x02), (F_MAXHR, 1, 0x02)]
    recs += bytes([0x40, 0, 0]) + struct.pack("<H", 18) + bytes([len(fields)]) + b"".join(bytes(f) for f in fields)
    inv = {v: k for k, v in SPORT.items()}
    invs = {v: k for k, v in SUB_SPORT.items()}
    for s in sess:
        ts = int(s["start"].timestamp()) - FIT_EPOCH
        recs += bytes([0x00]) + struct.pack("<IIBBIIIHBB", ts + int(s["elapsed_s"]), ts, inv.get(s["sport"], 0), invs.get(s.get("sub_sport"), 0),
                                            int(s["elapsed_s"] * 1000), int(s["elapsed_s"] * 1000), int(s["distance_m"] * 100),
                                            s.get("calories", 0), s.get("avg_hr") or 0xFF, s.get("max_hr") or 0xFF)
    head = struct.pack("<BBHI4s", 12, 0x20, 2132, len(recs), b".FIT")
    return head + bytes(recs) + b"\x00\x00"
