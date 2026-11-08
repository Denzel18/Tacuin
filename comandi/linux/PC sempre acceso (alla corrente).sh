#!/usr/bin/env bash
# Linux: alla corrente il PC resta acceso (anche a coperchio chiuso), così Tacuìn funziona sempre.
# A batteria continua ad andare in standby normalmente. Chiede la password di amministratore (sudo).
echo
echo "  Imposto il PC per restare acceso quando è collegato alla corrente, anche a coperchio chiuso."
echo "  A batteria continua ad andare in standby normalmente."
echo
sudo mkdir -p /etc/systemd/logind.conf.d || exit 1
printf '[Login]\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\n' | sudo tee /etc/systemd/logind.conf.d/tacuin.conf >/dev/null || exit 1
# GNOME (Ubuntu, Fedora): niente sospensione automatica alla corrente
if command -v gsettings >/dev/null && gsettings list-keys org.gnome.settings-daemon.plugins.power >/dev/null 2>&1; then
  gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
fi
# KDE e altri: il gestore energia del desktop può ancora sospendere dopo un po' di inattività: controlla le sue impostazioni
sudo systemctl kill -s HUP systemd-logind 2>/dev/null || true
echo "  Fatto! Alla corrente il PC non va più in standby, neanche chiudendo il coperchio."
echo "  Lo schermo si spegne comunque da solo per risparmiare energia."
echo "  Per tornare come prima: «comandi/linux/Ripristina standby PC.sh»."
echo
