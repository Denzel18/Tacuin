#!/usr/bin/env bash
# Linux: torna alle impostazioni di prima (coperchio chiuso = sospendi, standby automatico alla corrente).
sudo rm -f /etc/systemd/logind.conf.d/tacuin.conf
sudo systemctl kill -s HUP systemd-logind 2>/dev/null || true
if command -v gsettings >/dev/null && gsettings list-keys org.gnome.settings-daemon.plugins.power >/dev/null 2>&1; then
  gsettings reset org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type
fi
echo
echo "  Fatto: alla corrente il PC torna ad andare in standby e a sospendersi quando chiudi il coperchio."
echo
