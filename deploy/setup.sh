#!/usr/bin/env bash
# Prepara un server Ubuntu/Debian nuovo per il Tracker. Da lanciare come root, una volta sola:
#   curl -fsSL <questo file> -o setup.sh   (oppure copialo con scp)   →   sudo bash setup.sh
# Fa: aggiornamenti automatici di sicurezza, firewall, swap, Docker, cartella /opt/tracker, backup notturno.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Lancialo come root (sudo bash setup.sh)"; exit 1; }
DIR=/opt/tracker
REPO_RAW=${REPO_RAW:-}   # facoltativo: URL "raw" della cartella deploy se vuoi scaricare i file da qui

echo "== Pacchetti e aggiornamenti automatici"
export DEBIAN_FRONTEND=noninteractive
# su una VM appena accesa gli aggiornamenti automatici tengono occupato apt: aspetto invece di fallire
APT="apt-get -o DPkg::Lock::Timeout=600"
$APT update -q
$APT upgrade -yq
$APT install -yq ca-certificates curl ufw unattended-upgrades rsync openssl python3
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== Swap (utile sui server da 1-2 GB)"
if ! swapon --show | grep -q . && [ "$(awk '/MemTotal/{print $2}' /proc/meminfo)" -lt 4000000 ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "== Firewall: SSH sempre, 80/443 solo se serve (profilo https)"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
if [ "${TUNNEL_ONLY:-0}" != 1 ]; then
  ufw allow 80/tcp; ufw allow 443/tcp; ufw allow 443/udp
fi
ufw --force enable

echo "== SSH: niente password se c'è già una chiave"
if [ -s /root/.ssh/authorized_keys ] || ls /home/*/.ssh/authorized_keys >/dev/null 2>&1; then
  mkdir -p /etc/ssh/sshd_config.d
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
    > /etc/ssh/sshd_config.d/10-tracker.conf
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
else
  echo "   (nessuna chiave SSH trovata: lascio l'accesso con password. Aggiungi una chiave e rilancia.)"
fi

echo "== Cartella $DIR"
mkdir -p "$DIR/data" "$DIR/backups"
here=$(cd "$(dirname "$0")" && pwd)
for f in docker-compose.yml Caddyfile .env.example backup.sh update.sh restore.sh archivio.py aws-s3-backup-policy.json; do
  if [ -f "$here/$f" ] && [ "$here" != "$DIR" ]; then cp "$here/$f" "$DIR/"; fi
  if [ ! -f "$DIR/$f" ] && [ -n "$REPO_RAW" ]; then curl -fsSL "$REPO_RAW/$f" -o "$DIR/$f"; fi
done
chmod +x "$DIR"/*.sh 2>/dev/null || true
chown -R 1000:1000 "$DIR/data"          # il Tracker gira come utente 1000 nel container
if [ ! -f "$DIR/.env" ] && [ -f "$here/.env" ] && [ "$here" != "$DIR" ]; then install -m 600 "$here/.env" "$DIR/.env"; fi
[ -f "$DIR/.env" ] || { cp "$DIR/.env.example" "$DIR/.env"; chmod 600 "$DIR/.env"; }
chmod 600 "$DIR/.env"
# chiave interna tra Tracker e servizio scontrini: se manca la creo (nessuno deve ricordarla)
if grep -q '^VISION_TOKEN=$' "$DIR/.env"; then
  sed -i "s/^VISION_TOKEN=$/VISION_TOKEN=$(openssl rand -hex 24)/" "$DIR/.env"
fi

if [ "${BLOCK_METADATA:-0}" = 1 ]; then
  echo "== I container non possono parlare con il metadata server della VM (169.254.169.254)"
  # Il token della VM serve solo al backup, che gira fuori dai container. Se un giorno l'app avesse una falla
  # che le fa chiamare un indirizzo scelto da altri, il token resterebbe comunque irraggiungibile.
  cat > /etc/systemd/system/tracker-metadata-block.service <<'UNIT'
[Unit]
Description=Blocca ai container Docker l'accesso al metadata server
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
# Su Google Cloud 169.254.169.254 è anche il server DNS: blocco solo l'API dei metadati (TCP 80), il DNS (53) resta libero
ExecStart=/bin/sh -c 'iptables -C DOCKER-USER -d 169.254.169.254 -p tcp --dport 80 -j DROP 2>/dev/null || iptables -I DOCKER-USER -d 169.254.169.254 -p tcp --dport 80 -j DROP'
ExecStop=/bin/sh -c 'iptables -D DOCKER-USER -d 169.254.169.254 -p tcp --dport 80 -j DROP 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
UNIT
  # versione precedente: bloccava tutto, anche il DNS
  iptables -D DOCKER-USER -d 169.254.169.254 -j DROP 2>/dev/null || true
  systemctl daemon-reload
  systemctl enable tracker-metadata-block.service
  systemctl restart tracker-metadata-block.service
fi

echo "== Backup ogni notte alle 03:30"
echo "30 3 * * * root $DIR/backup.sh >> /var/log/tracker-backup.log 2>&1" > /etc/cron.d/tracker-backup

# lanciato da deploy/gcp/deploy.sh: i passi successivi li fa lo script, niente istruzioni manuali
[ "${FROM_DEPLOY:-0}" = 1 ] && { echo "== Server pronto"; exit 0; }

cat <<EOF

Fatto. Prossimi passi:
  1) nano $DIR/.env            → DOMAIN, ROOT_*, SMTP_*, COMPOSE_PROFILES
  2) se il repository è privato:
       docker login ghcr.io -u <utente-github>   (password = token GitHub con permesso read:packages)
  3) cd $DIR && docker compose up -d
  4) apri https://<il tuo dominio>
EOF
