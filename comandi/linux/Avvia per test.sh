#!/usr/bin/env bash
# Linux (e Mac): avvio per i test. Aggiorna il codice, accende il servizio scontrini (Docker), avvia Tacuìn
# e archivia i log alla chiusura (Ctrl+C). Opzioni: --no-pull --no-vision --keep-vision
cd "$(dirname "$0")/../.." || exit 1   # gli script stanno in comandi/linux: lavoro dalla cartella principale
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "  Python 3.9 o più recente non trovato: sudo apt install python3"; exit 1
fi
command -v git >/dev/null || echo "  Git non trovato: senza git non posso scaricare gli aggiornamenti (sudo apt install git)."
if [ ! -f .env ]; then
  cp .env.example .env && chmod 600 .env
  echo "  Ho creato il file .env: compilalo, salva e chiudi l'editor."
  "${VISUAL:-${EDITOR:-nano}}" .env 2>/dev/null || true
fi
"$PY" comandi/avvio_test.py "$@"
echo; read -rp "  Premi Invio per chiudere… " _
