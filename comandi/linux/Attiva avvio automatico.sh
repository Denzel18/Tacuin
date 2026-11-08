#!/usr/bin/env bash
# Linux: Tacuìn parte da solo, in background, ogni volta che accedi (servizio systemd del tuo utente, senza sudo).
cd "$(dirname "$0")/../.." || exit 1   # gli script stanno in comandi/linux: lavoro dalla cartella principale
APPDIR=$(pwd)
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "  Python 3.9 o più recente non trovato: sudo apt install python3"; exit 1
fi
[ -f .env ] || { echo "  Prima avvia una volta «comandi/linux/Avvia Tracker.sh» per creare il file .env"; exit 1; }
PORT=$(grep -E '^PORT=' .env | tail -1 | cut -d= -f2); PORT=${PORT:-8080}

if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/tacuin.service" <<EOF
[Unit]
Description=Tacuìn (server)
After=network-online.target

[Service]
WorkingDirectory=$APPDIR
ExecStart=$PY $APPDIR/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now tacuin.service || { echo "  Non sono riuscito ad attivare il servizio."; exit 1; }
  HOW="un servizio systemd del tuo utente (systemctl --user status tacuin)"
else
  # niente systemd per l'utente: uso cron, che lo avvia all'accensione del PC
  LINE="@reboot cd \"$APPDIR\" && \"$PY\" server.py >/dev/null 2>&1"
  ( crontab -l 2>/dev/null | grep -v "$APPDIR\" && .*server.py"; echo "$LINE" ) | crontab - || { echo "  Non sono riuscito a usare cron."; exit 1; }
  nohup "$PY" server.py >/dev/null 2>&1 &
  HOW="una riga @reboot di cron (crontab -l)"
fi
sleep 2
xdg-open "http://localhost:$PORT" >/dev/null 2>&1 || true
echo
echo "  Fatto! Tacuìn ora parte da solo, in background, ogni volta che accedi."
echo "  Indirizzo: http://localhost:$PORT"
echo "  Log:       $APPDIR/data/server.log"
echo "  Avvio con: $HOW"
echo "  Per partire anche prima dell'accesso (PC usato come server): sudo loginctl enable-linger $USER"
echo "  Per toglierlo usa «comandi/linux/Disattiva avvio automatico.sh»."
echo
