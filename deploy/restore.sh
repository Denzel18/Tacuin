#!/usr/bin/env bash
# Ripristina un backup creato da backup.sh.  Uso: ./restore.sh backups/tracker-2026-11-01_0330.tar.gz[.enc]
set -euo pipefail
cd "$(dirname "$0")"
f=${1:?"indica il file di backup, es. ./restore.sh backups/tracker-....tar.gz"}
[ -f "$f" ] || { echo "File non trovato: $f" >&2; exit 1; }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
if [[ "$f" == *.enc ]]; then
  read -rsp "Passphrase del backup: " BACKUP_PASS_ENV; echo; export BACKUP_PASS_ENV
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$f" -out "$tmp/b.tar.gz" -pass env:BACKUP_PASS_ENV
  f="$tmp/b.tar.gz"
fi
mkdir "$tmp/data"; tar -xzf "$f" -C "$tmp/data"
[ -f "$tmp/data/backup/snapshot.db" ] || { echo "Nel backup manca il database" >&2; exit 1; }

docker compose stop tracker
[ -d data ] && mv data "data.prima-del-ripristino.$(date +%s)"
mv "$tmp/data" data
mv data/backup/snapshot.db data/tracker.db
chown -R 1000:1000 data 2>/dev/null || true
docker compose up -d
echo "Ripristinato. La cartella precedente è stata conservata come data.prima-del-ripristino.*"
