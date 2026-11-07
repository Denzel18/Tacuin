#!/usr/bin/env bash
# Backup del Tracker: copia coerente del database + tutti i file in data/, in un archivio datato.
# Facoltativo: cifratura (BACKUP_PASSPHRASE) e copia fuori dal server: via rsync (BACKUP_REMOTE)
# e/o su S3 o compatibili (BACKUP_S3_BUCKET + chiavi, vedi .env.example; usa archivio.py, solo Python standard).
# Uso: ./backup.sh   (setup.sh lo mette in cron ogni notte alle 03:30)
set -euo pipefail
cd "$(dirname "$0")"

env_get() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }
KEEP=$(env_get BACKUP_KEEP); KEEP=${KEEP:-14}
REMOTE=$(env_get BACKUP_REMOTE)
PORT=$(env_get BACKUP_SSH_PORT); PORT=${PORT:-23}
PASS=$(env_get BACKUP_PASSPHRASE)
S3_BUCKET=$(env_get BACKUP_S3_BUCKET)
GCS_BUCKET=$(env_get BACKUP_GCS_BUCKET)

mkdir -p backups data/backup
stamp=$(date +%Y-%m-%d_%H%M)

# 1) fotografia coerente del database mentre il Tracker è acceso (API di backup di sqlite)
docker compose exec -T tracker python - <<'PY'
import sqlite3
src = sqlite3.connect("/data/tracker.db")
dst = sqlite3.connect("/data/backup/snapshot.db")
src.backup(dst)
dst.close(); src.close()
PY

# 2) archivio: tutto data/ tranne il database "vivo" e le copie giornaliere vecchie
out="backups/tracker-$stamp.tar.gz"
tar -czf "$out" -C data \
  --exclude=./tracker.db --exclude=./tracker.db-wal --exclude=./tracker.db-shm \
  --exclude='./backup/tracker-*.db' --exclude=./tiles .
rm -f data/backup/snapshot.db

# 3) cifratura facoltativa
if [ -n "$PASS" ]; then
  BACKUP_PASS_ENV="$PASS" openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$out" -out "$out.enc" -pass env:BACKUP_PASS_ENV
  rm -f "$out"; out="$out.enc"
fi
echo "Backup creato: $out ($(du -h "$out" | cut -f1))"

# 4) pulizia: tiene gli ultimi $KEEP
ls -1t backups/tracker-* 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

# 5) copia fuori dal server
esito=0
if [ -n "$S3_BUCKET" ]; then
  BACKUP_FILE="$out" \
  S3_BUCKET="$S3_BUCKET" S3_REGION="$(env_get BACKUP_S3_REGION)" S3_PREFIX="$(env_get BACKUP_S3_PREFIX)" \
  S3_ENDPOINT="$(env_get BACKUP_S3_ENDPOINT)" S3_ACCESS_KEY="$(env_get BACKUP_S3_ACCESS_KEY)" S3_SECRET_KEY="$(env_get BACKUP_S3_SECRET_KEY)" \
  python3 - <<'EOPY' || { echo "ATTENZIONE: copia su S3 non riuscita" >&2; esito=1; }
import os, sys
sys.path.insert(0, os.getcwd())
import archivio
e = os.environ
regione = e.get("S3_REGION") or "eu-central-1"
endpoint = e.get("S3_ENDPOINT") or f"https://s3.{regione}.amazonaws.com"
st = archivio.ArchivioS3(endpoint, e["S3_BUCKET"], e.get("S3_ACCESS_KEY", ""), e.get("S3_SECRET_KEY", ""), regione, e.get("S3_PREFIX") or "tracker")
f = e["BACKUP_FILE"]
print("Copiato su", st.put(f, "backup/" + os.path.basename(f)))
EOPY
fi
if [ -n "$GCS_BUCKET" ]; then
  BACKUP_FILE="$out" GCS_BUCKET="$GCS_BUCKET" GCS_PREFIX="$(env_get BACKUP_GCS_PREFIX)" python3 - <<'EOPY' || { echo "ATTENZIONE: copia su Cloud Storage non riuscita" >&2; esito=1; }
import os, sys
sys.path.insert(0, os.getcwd())
import archivio
e = os.environ
st = archivio.ArchivioGCS(e["GCS_BUCKET"], e.get("GCS_PREFIX") or "tracker")
f = e["BACKUP_FILE"]
print("Copiato su", st.put(f, "backup/" + os.path.basename(f)))
EOPY
fi
if [ -n "$REMOTE" ]; then
  rsync -a -e "ssh -p $PORT -o BatchMode=yes -o StrictHostKeyChecking=accept-new" backups/ "$REMOTE/" \
    && echo "Copiato su $REMOTE" || { echo "ATTENZIONE: copia remota non riuscita" >&2; esito=1; }
fi
exit $esito
