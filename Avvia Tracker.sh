#!/usr/bin/env bash
# Linux (e Mac): avvia Tacuìn in questa finestra. Per fermarlo: Ctrl+C o chiudi la finestra.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo; echo "  Python 3.9 o più recente non trovato."
  echo "  Installalo: sudo apt install python3   (Fedora: sudo dnf install python3)"; echo
  read -rp "  Premi Invio per chiudere… " _; exit 1
fi
if [ ! -f .env ]; then   # primo avvio: crea il file di configurazione
  cp .env.example .env && chmod 600 .env
  echo; echo "  Ho creato il file di configurazione .env"
  echo "  Controlla le impostazioni (ROOT_EMAIL, email…), salva e chiudi l'editor."; echo
  "${VISUAL:-${EDITOR:-nano}}" .env 2>/dev/null || xdg-open .env 2>/dev/null || echo "  Apri il file .env con un editor di testo."
fi
echo; echo "  Tacuìn in avvio… il browser si aprirà da solo."
echo "  Per fermarlo premi Ctrl+C (o chiudi questa finestra)."; echo
OPEN_BROWSER=1 "$PY" server.py
echo; read -rp "  Premi Invio per chiudere… " _
