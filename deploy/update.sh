#!/usr/bin/env bash
# Aggiorna il Tracker all'ultima immagine pubblicata da GitHub Actions (prima fa un backup).
set -euo pipefail
cd "$(dirname "$0")"
./backup.sh || echo "Backup non riuscito: aggiorno comunque" >&2
docker compose pull
# prima il servizio scontrini, poi il resto: così Tacuìn lo trova già pronto
docker compose up -d --wait --wait-timeout 180 vision || echo "ATTENZIONE: il servizio scontrini non risponde ancora" >&2
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
docker compose ps
