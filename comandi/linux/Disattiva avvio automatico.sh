#!/usr/bin/env bash
# Linux: toglie l'avvio automatico e ferma Tacuìn in background.
cd "$(dirname "$0")/../.." || exit 1   # gli script stanno in comandi/linux: lavoro dalla cartella principale
APPDIR=$(pwd)
if [ -f "$HOME/.config/systemd/user/tacuin.service" ]; then
  systemctl --user disable --now tacuin.service 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/tacuin.service"
  systemctl --user daemon-reload 2>/dev/null || true
fi
if crontab -l 2>/dev/null | grep -q "$APPDIR\" && .*server.py"; then
  crontab -l 2>/dev/null | grep -v "$APPDIR\" && .*server.py" | crontab -
fi
pkill -f "$APPDIR/server.py" 2>/dev/null || true
echo
echo "  Avvio automatico disattivato e Tacuìn in background fermato."
echo "  Puoi sempre avviarlo a mano con «comandi/linux/Avvia Tracker.sh»."
echo
