#!/usr/bin/env bash
# Tacuìn su Google Cloud: crea l'infrastruttura (Terraform), prepara la VM e avvia l'app.
#
# Si usa da Google Cloud Shell (nel browser, ha già gcloud; Terraform, se manca, lo installa da solo) oppure da un PC con gcloud.
#
#   ./deploy.sh up        crea/aggiorna tutto e avvia l'app (la prima volta chiede solo email e password del root)
#   ./deploy.sh update    scarica l'ultima versione dell'app e la riavvia (con backup prima)
#   ./deploy.sh ssh       entra nella VM (tramite IAP: la porta 22 non è aperta su internet)
#   ./deploy.sh logs      log dell'app in tempo reale (Ctrl+C per uscire)
#   ./deploy.sh status    stato dei container e dell'app
#   ./deploy.sh backup    backup subito (oltre a quello notturno)
#   ./deploy.sh stop      spegne la VM (backup prima): non paghi CPU e RAM, solo disco e IP (pochi euro al mese)
#   ./deploy.sh start     riaccende la VM; l'app riparte da sola
#   ./deploy.sh reset     CANCELLA TUTTO: dati, backup e snapshot; ricrea la VM da zero (IP e dominio restano)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DEPLOY=$(cd "$HERE/.." && pwd)
SECRETS="$HERE/app.env"          # il .env dell'app: resta qui (chmod 600) e su GitHub non va mai

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()  { printf '\n\033[31mERRORE: %s\033[0m\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "manca «$1»: usa Google Cloud Shell (ce l'ha già) oppure installalo"; }

# legge un valore da terraform.tfvars; se manca, il predefinito di variables.tf (così stop/start/update non hanno bisogno di Terraform)
tfvar() {
  local v=""
  [ -f "$HERE/terraform.tfvars" ] && v=$(grep -E "^\s*$1\s*=" "$HERE/terraform.tfvars" | head -1 | sed -E 's/[^"]*"([^"]*)".*/\1/')
  [ -n "$v" ] || v=$(awk -v n="$1" '$1=="variable" && $2=="\""n"\"" {f=1} f && $1=="default" {gsub(/"/,"",$3); print $3; exit}' "$HERE/variables.tf")
  printf '%s' "$v"
}
# Cloud Shell (soprattutto dall'app per telefono o dopo tanto tempo) può ripartire senza account collegato:
# in quel caso gcloud dice «You do not currently have an active account selected». Qui si rifà il login.
login() {
  if gcloud auth print-access-token >/dev/null 2>&1; then return; fi
  echo
  echo "  Cloud Shell non è collegato al tuo account Google (succede spesso dall'app per telefono)."
  echo "  1) tocca il link che compare qui sotto e scegli l'account del progetto"
  echo "  2) consenti l'accesso, copia il codice che ti mostra Google"
  echo "  3) torna qui, incollalo e premi Invio"
  echo
  gcloud auth login --update-adc --no-launch-browser || die "login non riuscito: riprova, oppure apri Cloud Shell dal browser (shell.cloud.google.com)"
}
target() {
  [ -f "$HERE/terraform.tfvars" ] || die "manca deploy/gcp/terraform.tfvars: lancia prima «./deploy.sh up» (se sei su un altro dispositivo, controlla di essere nella stessa cartella: cd ~/Tacuin/deploy/gcp)"
  need gcloud
  login
  PROJECT=$(tfvar project_id); ZONE=$(tfvar zone); VM=$(tfvar name)
  [[ "$ZONE" =~ ^[a-z]+-[a-z]+[0-9]+-[a-z]$ ]] || die "zona non valida in terraform.tfvars: «$ZONE»"
  gcloud config set project "$PROJECT" >/dev/null 2>&1 || true
}

tfout() { terraform -chdir="$HERE" output -raw "$1" 2>/dev/null; }

# Cloud Shell a volte non ha più Terraform (al suo posto c'è un messaggio con le istruzioni): lo installo nella tua home
TF_VERSION=1.9.8
ensure_terraform() {
  export PATH="$HOME/.local/bin:$PATH"
  terraform version 2>/dev/null | grep -q '^Terraform v' && return
  say "Installo Terraform $TF_VERSION nella tua home (una volta sola)"
  need curl; need unzip
  local arch tmp; arch=$(uname -m); case "$arch" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; esac
  tmp=$(mktemp -d)
  local base="https://releases.hashicorp.com/terraform/${TF_VERSION}" zip="terraform_${TF_VERSION}_linux_${arch}.zip"
  curl -fsSL "$base/$zip" -o "$tmp/$zip" && curl -fsSL "$base/terraform_${TF_VERSION}_SHA256SUMS" -o "$tmp/SUMS" \
    || die "non riesco a scaricare Terraform da releases.hashicorp.com"
  # controllo l'impronta pubblicata da HashiCorp: se il file è stato alterato, mi fermo
  (cd "$tmp" && grep " $zip\$" SUMS | sha256sum -c --quiet -) || { rm -rf "$tmp"; die "impronta di Terraform non valida: download interrotto"; }
  mkdir -p "$HOME/.local/bin"; unzip -oq "$tmp/$zip" terraform -d "$HOME/.local/bin"; rm -rf "$tmp"
  hash -r
  terraform version 2>/dev/null | grep -q '^Terraform v' || die "installazione di Terraform non riuscita"
  echo "  Fatto: $(terraform version | head -1)"
}
remote() { gcloud compute ssh "$VM" --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap --quiet --command "$1"; }

wait_ssh() {
  say "Aspetto che la VM sia pronta per SSH (via IAP)…"
  for _ in $(seq 1 30); do
    if remote "true" >/dev/null 2>&1; then return 0; fi
    sleep 10
  done
  die "la VM non risponde in SSH: controlla nella console che sia accesa e riprova «./deploy.sh up»"
}

ask() {  # ask VAR "domanda" [predefinito] [segreto]
  local v def="${3:-}" secret="${4:-}"
  if [ -n "$secret" ]; then read -rsp "$2: " v; echo; else read -rp "$2${def:+ [$def]}: " v; fi
  # incollando, alcuni terminali aggiungono codici invisibili (ESC[200~ … ESC[201~, \r): li tolgo
  v=${v//$'\e[200~'/}; v=${v//$'\e[201~'/}; v=$(printf '%s' "$v" | tr -d '[:cntrl:]')
  printf -v "$1" '%s' "${v:-$def}"
}

ask_email() {  # ask_email VAR "domanda"
  local e
  while :; do
    ask e "$2"; e=$(printf '%s' "$e" | tr -d ' ')
    if [[ "$e" =~ ^[^@]+@[^@]+\.[a-z]{2,}$ ]]; then printf -v "$1" '%s' "$e"; return; fi
    echo "  «$e» non è un indirizzo email valido, riprova."
  done
}

make_env() {
  say "Account root (il resto lo configuri dall'app)"
  echo "  Entrerai in Tacuìn con questa email e questa password. Email, dominio, dati del titolare"
  echo "  e nome utente li imposti poi dall'app: al primo accesso una procedura guidata te li chiede."
  local DOMAIN ROOT_EMAIL ROOT_NAME ROOT_PASSWORD PW2 PASS IP
  ask_email ROOT_EMAIL "Email del root (è il tuo nome utente per entrare)"
  while :; do
    ask ROOT_PASSWORD "Password del root (almeno 10 caratteri; vuoto = la creo io)" "" s
    if [ -z "$ROOT_PASSWORD" ]; then ROOT_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20); GEN_PW=1; break; fi
    [ ${#ROOT_PASSWORD} -ge 10 ] || { echo "  Troppo corta, riprova."; continue; }
    ask PW2 "Ripeti la password" "" s
    [ "$ROOT_PASSWORD" = "$PW2" ] && break
    echo "  Le due password non coincidono, riprova."
  done
  # il nome si cambia dal Profilo: intanto uso la parte prima della @ (mario.rossi → Mario Rossi)
  ROOT_NAME=$(printf '%s' "${ROOT_EMAIL%%@*}" | tr '._-' '   ' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)} 1')
  # indirizzo provvisorio con HTTPS vero: sslip.io risponde con l'IP scritto nel nome (34-154-57-95.sslip.io → 34.154.57.95).
  # Il tuo dominio lo aggiungi dall'app (Impostazioni → Server → Dominio) e diventa quello principale.
  IP=$(tfout ip 2>/dev/null || true)
  [[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "non trovo l'IP della VM (terraform output ip): rilancia «./deploy.sh up»"
  DOMAIN="${IP//./-}.sslip.io"
  local SMTP_HOST="" SMTP_PORT=587 SMTP_SECURITY=starttls SMTP_USER="" SMTP_PASSWORD="" SMTP_FROM=""
  PASS=$(openssl rand -base64 33 | tr -d '\n')
  local BUCKET; BUCKET=$(tfout backup_bucket 2>/dev/null || true)
  umask 077
  # Scrivo con Python (niente problemi di caratteri speciali) in un file temporaneo, poi lo controllo e lo rinomino:
  # se qualcosa va storto non resta un app.env vuoto o a metà
  V_COMPOSE_PROFILES=https V_DOMAIN=$DOMAIN V_ROOT_EMAIL=$ROOT_EMAIL V_ROOT_NAME=$ROOT_NAME \
  V_ROOT_PASSWORD=$ROOT_PASSWORD V_SMTP_HOST=$SMTP_HOST V_SMTP_PORT=$SMTP_PORT \
  V_SMTP_SECURITY=$SMTP_SECURITY V_SMTP_USER=$SMTP_USER V_SMTP_PASSWORD=${SMTP_PASSWORD:-} V_SMTP_FROM=$SMTP_FROM \
  V_BACKUP_PASSPHRASE=$PASS V_BACKUP_GCS_BUCKET=$BUCKET \
  V_PUBLIC_IP="$IP" V_VISION_URL=http://vision:8090 V_VISION_TOKEN="$(openssl rand -hex 24)" \
  python3 - "$DEPLOY/.env.example" "$SECRETS.tmp" <<'EOPY2'
import os, sys
vals = {k[2:]: v for k, v in os.environ.items() if k.startswith("V_")}
out = []
for line in open(sys.argv[1], encoding="utf-8"):
    key = line.split("=", 1)[0]
    if "=" in line and not line.lstrip().startswith("#") and key in vals:
        line = f"{key}={vals.pop(key)}\n"
    out.append(line)
out += [f"{k}={v}\n" for k, v in vals.items()]   # chiavi mancanti nel modello: le aggiungo in fondo
open(sys.argv[2], "w", encoding="utf-8").writelines(out)
EOPY2
  grep -q '^DOMAIN=..*' "$SECRETS.tmp" || { rm -f "$SECRETS.tmp"; die "non sono riuscito a scrivere la configurazione"; }
  mv "$SECRETS.tmp" "$SECRETS"
  chmod 600 "$SECRETS"
  echo "  Salvato in $SECRETS (solo tu puoi leggerlo)."
  [ -z "${GEN_PW:-}" ] || ROOT_PW_SHOW=$ROOT_PASSWORD   # la mostro alla fine, insieme all'indirizzo
  BACKUP_PASS_SHOW=$PASS
}

cmd_up() {
  need gcloud; need openssl; ensure_terraform
  if [ ! -f "$HERE/terraform.tfvars" ]; then
    local PID
    login
    PID=$(gcloud config get-value project 2>/dev/null || true)
    if [ -z "$PID" ]; then   # un solo progetto sull'account: è quello
      local ALL; ALL=$(gcloud projects list --format='value(projectId)' 2>/dev/null || true)
      [ "$(grep -c . <<<"$ALL")" = 1 ] && PID=$ALL
    fi
    [ -n "$PID" ] || ask PID "ID del progetto Google Cloud (lo trovi in alto nella console, es. tacuin-123456)"
    echo "  Progetto Google Cloud: $PID"
    [[ "$PID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || die "«$PID» non sembra un ID di progetto (tutto minuscolo, es. tacuin-123456)"
    sed "s|^project_id = \"[^\"]*\"|project_id = \"$PID\"|" "$HERE/terraform.tfvars.example" > "$HERE/terraform.tfvars"
    echo "  Creato deploy/gcp/terraform.tfvars (lo puoi modificare per cambiare zona, VM, ecc.)"
  fi
  gcloud config set project "$(grep -E '^\s*project_id' "$HERE/terraform.tfvars" | sed -E 's/.*"(.*)".*/\1/')" >/dev/null 2>&1 || true

  say "1/6 Infrastruttura (Terraform)"
  # Su un progetto nuovo le API sono spente e Terraform non può nemmeno leggere il progetto: le accendo prima
  local PID APIS MISSING=()
  PID=$(grep -E '^\s*project_id' "$HERE/terraform.tfvars" | sed -E 's/.*"(.*)".*/\1/')
  APIS="cloudresourcemanager serviceusage iam compute iap oslogin storage logging monitoring"
  login
  local ON; ON=$(gcloud services list --enabled --project "$PID" --format='value(config.name)' 2>/dev/null || true)
  for a in $APIS; do grep -qx "$a.googleapis.com" <<<"$ON" || MISSING+=("$a.googleapis.com"); done
  if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  Attivo i servizi Google necessari (la prima volta ci vuole un minuto)…"
    gcloud services enable --project "$PID" "${MISSING[@]}" \
      || die "Google ha rifiutato l'attivazione dei servizi: il motivo è nel messaggio qui sopra (fatturazione, permessi o progetto sbagliato)"
  fi
  terraform -chdir="$HERE" init -input=false >/dev/null  # senza -upgrade: le versioni restano quelle del file .terraform.lock.hcl
  if terraform -chdir="$HERE" state list 2>/dev/null | grep -q google_compute_instance.vm; then
    terraform -chdir="$HERE" apply -input=false          # infrastruttura esistente: mostra le modifiche e chiede conferma
  else
    terraform -chdir="$HERE" apply -input=false -auto-approve   # prima installazione: crea e basta
  fi
  target
  [ "$(vm_status)" = RUNNING ] || { echo "  La VM era spenta: la accendo"; gcloud compute instances start "$VM" --zone "$ZONE" --project "$PROJECT" --quiet; }

  say "2/6 Configurazione dell'app"
  if [ -f "$SECRETS" ] && ! grep -q '^DOMAIN=..*' "$SECRETS"; then
    echo "  La configurazione salvata è vuota o incompleta: la rifaccio."
    mv "$SECRETS" "$SECRETS.vecchio"
  fi
  [ -f "$SECRETS" ] || make_env
  echo "  Configurazione: $(grep -c '^[A-Z_]*=' "$SECRETS") voci, sito $(grep '^DOMAIN=' "$SECRETS" | cut -d= -f2)"

  wait_ssh

  say "3/6 Copio i file sulla VM"
  remote "rm -rf ~/deploy && mkdir -p ~/deploy"
  gcloud compute scp --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap --quiet \
    "$DEPLOY/docker-compose.yml" "$DEPLOY/Caddyfile" "$DEPLOY/.env.example" "$DEPLOY/setup.sh" "$DEPLOY/backup.sh" \
    "$DEPLOY/update.sh" "$DEPLOY/restore.sh" "$DEPLOY/archivio.py" "$VM:~/deploy/"
  # gcloud compute ssh via IAP non inoltra in modo affidabile lo stdin: la configurazione viaggia nel comando
  # stesso, in base64, dentro il tunnel cifrato (sulla VM ci sei solo tu), e viene scritta direttamente con permessi 600
  local B64; B64=$(base64 -w0 < "$SECRETS")
  remote "sudo mkdir -p /opt/tracker && echo '$B64' | base64 -d | sudo tee /opt/tracker/.env >/dev/null && sudo chmod 600 /opt/tracker/.env"
  if ! remote "sudo grep -q '^DOMAIN=..*' /opt/tracker/.env"; then
    echo "  Diagnostica: righe locali $(grep -c . "$SECRETS"), DOMAIN locale: $(grep -c '^DOMAIN=..*' "$SECRETS")"
    remote "sudo ls -la /opt/tracker/; echo righe sulla VM: \$(sudo grep -c . /opt/tracker/.env)" || true
    die "sulla VM la configurazione non è arrivata: mandami le righe «Diagnostica» qui sopra"
  fi
  echo "  Configurazione copiata."

  say "4/6 Preparo il server (aggiornamenti automatici, firewall, Docker, backup notturno)"
  remote "sudo BLOCK_METADATA=1 FROM_DEPLOY=1 bash ~/deploy/setup.sh"

  say "5/6 Accesso alle immagini su GitHub"
  set_image
  if remote "cd /opt/tracker && sudo docker compose pull -q tracker" >/dev/null 2>&1; then
    echo "  immagini scaricate: nessun accesso da configurare"
  elif use_public_image && remote "cd /opt/tracker && sudo docker compose pull -q tracker" >/dev/null 2>&1; then
    echo "  immagini pubbliche del progetto ($PUBLIC_IMAGE): nessun accesso da configurare"
  elif remote "sudo test -f /root/.docker/config.json && sudo grep -q ghcr.io /root/.docker/config.json"; then
    echo "  già fatto"
  else
    echo "  Le immagini sono private (repository privato o fork):"
    echo "  Serve un token GitHub con il solo permesso «read:packages»:"
    echo "  github.com → Settings → Developer settings → Personal access tokens → Tokens (classic) → read:packages"
    local GH_USER GH_TOKEN
    ask GH_USER "Utente GitHub"
    ask GH_TOKEN "Token" "" s
    # il token viaggia nel tunnel cifrato e arriva a docker da stdin (non resta nella cronologia della VM)
    local T64; T64=$(printf '%s' "$GH_TOKEN" | base64 -w0)
    remote "echo '$T64' | base64 -d | sudo docker login ghcr.io -u '$GH_USER' --password-stdin" >/dev/null \
      && echo "  ok" || die "GitHub ha rifiutato il token: serve un token (classic) con «read:packages»"
  fi

  say "6/6 Avvio Tacuìn"
  echo "  Avvio prima il servizio scontrini e aspetto che sia pronto…"
  remote "cd /opt/tracker && sudo docker compose pull -q && (sudo docker compose up -d --wait --wait-timeout 180 vision || echo '  ATTENZIONE: il servizio scontrini non risponde ancora') && sudo docker compose up -d --remove-orphans && sleep 15 && sudo docker compose ps"
  remote "cd /opt/tracker && sudo docker compose exec -T tracker python -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=5).read().decode())\"" \
    && echo "  L'app risponde."
  if remote "cd /opt/tracker && sudo docker compose exec -T tracker python -c \"import os,urllib.request;urllib.request.urlopen(os.environ['VISION_URL']+'/health',timeout=5)\"" >/dev/null 2>&1; then
    echo "  Scontrini da foto: attivi"
  else
    echo "  ATTENZIONE: scontrini da foto non attivi. Guarda i log con: ./deploy.sh ssh  →  cd /opt/tracker && sudo docker compose logs vision"
  fi

  local IP DOMAIN
  IP=$(tfout ip); DOMAIN=$(grep '^DOMAIN=' "$SECRETS" | cut -d= -f2)
  cat <<EOF

Fatto. Apri https://$DOMAIN
(il certificato HTTPS arriva da solo: se la prima volta non si apre, riprova tra un minuto)

  • Entra con $(grep '^ROOT_EMAIL=' "$SECRETS" | cut -d= -f2) e la password che hai scelto.
  • La procedura guidata ti chiede il resto: email per gli inviti, il tuo dominio, dati del titolare, nome utente.
    Per il dominio: nel DNS crea un record A  tuodominio  →  $IP, poi aggiungilo da Impostazioni → Server → Dominio.
  • Aggiornare l'app:  ./deploy.sh update     Entrare nella VM:  ./deploy.sh ssh
  • Backup: ogni notte alle 03:30, cifrati, nel bucket $(tfout backup_bucket)
EOF
  [ -z "${ROOT_PW_SHOW:-}" ] || echo "  Password del root (generata): $ROOT_PW_SHOW   ← salvala nel gestore di password"
  if [ -n "${BACKUP_PASS_SHOW:-}" ]; then
    echo "  Passphrase dei backup: $BACKUP_PASS_SHOW"
    echo "  ↑ salvala nel gestore di password: senza, i backup cifrati non si aprono (è anche in deploy/gcp/app.env)."
  fi
}

# immagini Docker del repository da cui lanci lo script (questo progetto, un fork o la vecchia copia privata)
image_repo() {
  local u; u=$(git -C "$HERE" config --get remote.origin.url 2>/dev/null || true)
  u=${u%.git}; u=${u#*github.com[:/]}
  [[ "$u" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] && echo "ghcr.io/${u,,}"
}
# il codice è lo stesso del progetto pubblico: se le immagini del tuo repository sono private, uso quelle pubbliche
PUBLIC_IMAGE=ghcr.io/denzel18/tacuin
use_public_image() {
  remote "cd /opt/tracker && sudo sed -i 's|^TACUIN_IMAGE=.*|TACUIN_IMAGE=$PUBLIC_IMAGE|' .env && sudo grep -q '^TACUIN_IMAGE=' .env || echo 'TACUIN_IMAGE=$PUBLIC_IMAGE' | sudo tee -a .env >/dev/null"
}
set_image() {
  local img; img=$(image_repo) || return 0
  [ -n "$img" ] || return 0
  remote "cd /opt/tracker && if sudo grep -q '^TACUIN_IMAGE=' .env; then sudo sed -i 's|^TACUIN_IMAGE=.*|TACUIN_IMAGE=$img|' .env; else echo 'TACUIN_IMAGE=$img' | sudo tee -a .env >/dev/null; fi"
  echo "  Immagini: $img"
}

cmd_update() {
  target
  say "Copio i file aggiornati (Docker Compose, Caddy, script)"
  remote "rm -rf ~/deploy && mkdir -p ~/deploy"
  gcloud compute scp --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap --quiet \
    "$DEPLOY/docker-compose.yml" "$DEPLOY/Caddyfile" "$DEPLOY/.env.example" "$DEPLOY/backup.sh" \
    "$DEPLOY/update.sh" "$DEPLOY/restore.sh" "$DEPLOY/archivio.py" "$VM:~/deploy/"
  # l'IP pubblico serve a Tacuìn per controllare i nuovi domini aggiunti dall'interfaccia
  set_image
  local IP; IP=$(gcloud compute instances describe "$VM" --zone "$ZONE" --project "$PROJECT" --format='value(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null || true)
  remote "set -e; cd /opt/tracker
    CH=\$(sudo sha256sum Caddyfile 2>/dev/null | cut -c1-64 || true)
    for f in docker-compose.yml Caddyfile .env.example backup.sh update.sh restore.sh archivio.py; do sudo install -m \$( [ \"\${f##*.}\" = sh ] && echo 755 || echo 644 ) ~/deploy/\$f \$f; done
    if [ -n '$IP' ] && ! sudo grep -q '^PUBLIC_IP=' .env; then echo 'PUBLIC_IP=$IP' | sudo tee -a .env >/dev/null; fi
    sudo ./update.sh
    if [ \"\$CH\" != \"\$(sudo sha256sum Caddyfile | cut -c1-64)\" ]; then sudo docker compose restart caddy >/dev/null 2>&1 || true; echo 'Caddy riavviato con la nuova configurazione.'; fi"
}
cmd_ssh()    { target; gcloud compute ssh "$VM" --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap; }
cmd_logs()   { target; remote "cd /opt/tracker && sudo docker compose logs -f --tail 100 tracker"; }
cmd_status() { target; remote "cd /opt/tracker && sudo docker compose ps && uptime && df -h / | tail -1"; }
cmd_backup() { target; remote "sudo /opt/tracker/backup.sh"; }

vm_status() { gcloud compute instances describe "$VM" --zone "$ZONE" --project "$PROJECT" --format='value(status)'; }

cmd_stop() {
  target
  [ "$(vm_status)" = RUNNING ] || { echo "La VM è già spenta ($(vm_status))."; return; }
  say "Backup prima di spegnere"
  remote "sudo /opt/tracker/backup.sh" || echo "  (backup non riuscito: spengo comunque, i dati restano sul disco)"
  say "Spengo la VM"
  gcloud compute instances stop "$VM" --zone "$ZONE" --project "$PROJECT" --quiet
  cat <<EOF

Spenta. Il sito, le notifiche, i promemoria e il backup notturno sono fermi finché non la riaccendi.
Mentre è spenta paghi solo disco, snapshot e IP statico (qualche euro al mese), non la VM.
Per riaccenderla:  ./deploy.sh start
EOF
}

cmd_start() {
  target
  if [ "$(vm_status)" != RUNNING ]; then
    say "Accendo la VM"
    gcloud compute instances start "$VM" --zone "$ZONE" --project "$PROJECT" --quiet
  fi
  wait_ssh
  say "Aspetto che Tacuìn riparta"
  for _ in $(seq 1 18); do
    if remote "cd /opt/tracker && sudo docker compose exec -T tracker python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=5)\"" >/dev/null 2>&1; then
      echo "  Tacuìn è di nuovo online: https://$(grep '^DOMAIN=' "$SECRETS" 2>/dev/null | cut -d= -f2)"
      return
    fi
    sleep 10
  done
  remote "cd /opt/tracker && sudo docker compose up -d && sudo docker compose ps"
}

# Ripartire da zero: niente dati vecchi da nessuna parte (VM, backup nel bucket, snapshot del disco).
# L'IP statico, la rete e il bucket restano: il dominio continua a puntare qui.
cmd_reset() {
  need gcloud; ensure_terraform; target
  terraform -chdir="$HERE" init -input=false >/dev/null
  local BUCKET; BUCKET=$(tfout backup_bucket || true)
  echo
  echo "ATTENZIONE: questo comando cancella in modo definitivo"
  echo "  • la VM «$VM» e il suo disco: utenti, spese, appuntamenti, diario, impostazioni di Tacuìn"
  echo "  • tutti i backup nel bucket ${BUCKET:-(non trovato)}"
  echo "  • tutti gli snapshot del disco"
  echo "Poi ricrea la VM da zero e avvia Tacuìn vuoto. Restano l'IP statico, la rete e il bucket (vuoto)."
  echo "Non si torna indietro."
  local ok again
  read -rp "Per continuare scrivi CANCELLA TUTTO: " ok
  [ "$ok" = "CANCELLA TUTTO" ] || die "annullato: non ho toccato niente"
  read -rp "Rifaccio anche la configurazione (email e password del root; email e dominio tornano da configurare nell'app)? [s/N] " again

  say "1/4 Tolgo la protezione dalla cancellazione della VM"
  terraform -chdir="$HERE" apply -input=false -auto-approve -var deletion_protection=false \
    -target=google_compute_instance.vm >/dev/null

  say "2/4 Cancello i backup nel bucket"
  if [ -n "$BUCKET" ]; then
    gcloud storage rm --recursive --all-versions "gs://$BUCKET/**" --project "$PROJECT" --quiet 2>/dev/null || echo "  (bucket già vuoto)"
  fi

  say "3/4 Ricreo la VM da zero (disco nuovo e vuoto)"
  terraform -chdir="$HERE" apply -input=false -auto-approve -var deletion_protection=false \
    -replace=google_compute_instance.vm -replace=google_compute_disk_resource_policy_attachment.snapshots
  # gli snapshot del disco vecchio restano finché non li cancello io (regola KEEP_AUTO_SNAPSHOTS)
  local SNAPS
  SNAPS=$(gcloud compute snapshots list --project "$PROJECT" --filter="sourceDisk~/disks/$VM\$" --format='value(name)' 2>/dev/null || true)
  if [ -n "$SNAPS" ]; then
    echo "  Cancello $(wc -l <<<"$SNAPS") snapshot del disco vecchio"
    # shellcheck disable=SC2086
    gcloud compute snapshots delete $SNAPS --project "$PROJECT" --quiet >/dev/null
  fi
  if [ "${again,,}" = s ] && [ -f "$SECRETS" ]; then
    mv "$SECRETS" "$SECRETS.vecchio"; echo "  La configurazione vecchia è in $SECRETS.vecchio"
  fi

  say "4/4 Preparo la VM nuova e avvio Tacuìn"
  cmd_up   # rimette anche la protezione dalla cancellazione (deletion_protection del file terraform.tfvars)
  echo
  echo "Fatto: Tacuìn è ripartito vuoto. Entra con l'email e la password del root del file di configurazione."
  echo "Per 7 giorni Google tiene i file cancellati dal bucket in un cestino recuperabile, poi spariscono da soli."
}

case "${1:-}" in
  up) cmd_up ;; update) cmd_update ;; ssh) cmd_ssh ;; logs) cmd_logs ;; status) cmd_status ;; backup) cmd_backup ;;
  stop) cmd_stop ;; start) cmd_start ;; reset) cmd_reset ;;
  *) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
