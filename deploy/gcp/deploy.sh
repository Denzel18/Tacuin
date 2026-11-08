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
#
# Durante il lavoro vedi solo una barra di avanzamento; tutti i dettagli finiscono in deploy/gcp/installazione.log
# (password e token non ci vengono mai scritti). Se qualcosa va storto, manda quel file.
set -eEuo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DEPLOY=$(cd "$HERE/.." && pwd)
SECRETS="$HERE/app.env"          # il .env dell'app: resta qui (chmod 600) e su GitHub non va mai
LOG="$HERE/installazione.log"
exec 3>&1                        # fd 3 = lo schermo, anche quando l'output dei comandi va nel log

# ---------------------------------------------------------------- barra di avanzamento e log
CUR_I=0; CUR_N=1; CUR_LABEL=""; SPIN=""
FR=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
cols() { local c; c=$(tput cols 2>/dev/null || echo 80); [ "$c" -gt 20 ] 2>/dev/null && echo "$c" || echo 80; }

draw() {  # draw [spinner] [tempo]
  local pct=$((CUR_I * 100 / CUR_N)) w=20 f lab room
  f=$((CUR_I * w / CUR_N))
  room=$(( $(cols) - w - 18 )); [ "$room" -gt 8 ] || room=8
  lab=$CUR_LABEL; [ ${#lab} -le "$room" ] || lab="${lab:0:$((room - 1))}…"
  printf '\r\033[K  [%s%s] %3d%%  %s %s %s' "$(printf '%*s' "$f" '' | sed 's/ /█/g')" "$(printf '%*s' $((w - f)) '' | sed 's/ /░/g')" \
    "$pct" "$lab" "${1:-}" "${2:-}" >&3
}
spin_start() {
  if [ ! -t 3 ]; then printf '  [%d/%d] %s\n' "$((CUR_I + 1))" "$CUR_N" "$CUR_LABEL" >&3; return 0; fi
  ( trap - ERR; t0=$SECONDS; i=0
    while :; do draw "${FR[i % 10]}" "$((SECONDS - t0))s"; i=$((i + 1)); sleep 0.2; done ) &
  SPIN=$!
}
spin_stop() {
  if [ -n "$SPIN" ]; then kill "$SPIN" 2>/dev/null || true; wait "$SPIN" 2>/dev/null || true; SPIN=""; fi
}
bar_begin() {  # bar_begin "Titolo" numero_di_passi
  CUR_I=0; CUR_N=$2
  { echo "########## $(date '+%Y-%m-%d %H:%M:%S') · $1"; echo; } >>"$LOG"
  printf '\n\033[1m%s\033[0m\n' "$1" >&3
}
step() {  # step "Cosa sto facendo" comando [argomenti]: l'output del comando va solo nel log
  CUR_LABEL=$1; shift
  printf '\n===== %s · %s\n' "$(date '+%H:%M:%S')" "$CUR_LABEL" >>"$LOG"
  spin_start
  "$@" >>"$LOG" 2>&1
  spin_stop
  CUR_I=$((CUR_I + 1))
}
bar_end() {
  CUR_LABEL="Fatto"; [ -t 3 ] && draw && printf '\n' >&3
  printf '\n===== %s · tutto ok\n' "$(date '+%H:%M:%S')" >>"$LOG"
}
on_err() {
  local rc=$? line=$1
  [ "${BASH_SUBSHELL:-0}" -eq 0 ] || return 0   # dentro $(…): decide chi l'ha chiamato
  spin_stop
  { echo; echo "!!!!! ERRORE (codice $rc, riga $line di deploy.sh) durante: ${CUR_LABEL:-avvio}"; } >>"$LOG" 2>/dev/null || true
  printf '\n\n\033[31m✗ Non è andato a buon fine: %s\033[0m\n' "${CUR_LABEL:-avvio}" >&3
  if [ -s "$LOG" ]; then
    printf '  Ultime righe del log:\n' >&3
    grep -v '^\s*$' "$LOG" | tail -n 6 | sed 's/^/    /' | cut -c1-160 >&3 || true
  fi
  printf '\n  Tutti i dettagli sono in %s\n  Rilancia lo stesso comando: riparte da dove si era fermato. Se si ferma ancora, mandami quel file.\n' "$LOG" >&3
  exit "$rc"
}
trap 'on_err $LINENO' ERR
trap 'spin_stop' EXIT

say()  { printf '\n== %s\n' "$*"; }   # dentro i passi: finisce nel log
die()  {
  spin_stop
  printf '\nERRORE: %s\n' "$*" >>"$LOG" 2>/dev/null || true
  printf '\n\n\033[31m✗ %s\033[0m\n' "$*" >&3
  [ ! -s "$LOG" ] || printf '  Dettagli in %s\n' "$LOG" >&3
  exit 1
}
tell() { printf '%s\n' "$*" >&3; }   # un messaggio per te, anche nel mezzo di un passo
need() { command -v "$1" >/dev/null || die "manca «$1»: usa Google Cloud Shell (ce l'ha già) oppure installalo"; }

# ---------------------------------------------------------------- domande (sempre visibili sullo schermo)
ask() {  # ask VAR "domanda" [predefinito]
  local v def="${3:-}" again=""
  [ -z "$SPIN" ] || { spin_stop; again=1; printf '\n' >&3; }
  # la risposta si vede mentre scrivi: con il testo nascosto, dal telefono l'incolla spesso non funziona
  printf '%s%s: ' "$2" "${def:+ [$def]}" >&3
  IFS= read -r v </dev/tty || v=""
  # incollando, alcuni terminali aggiungono codici invisibili (ESC[200~ … ESC[201~, \r): li tolgo
  v=${v//$'\e[200~'/}; v=${v//$'\e[201~'/}; v=$(printf '%s' "$v" | tr -d '[:cntrl:]')
  printf -v "$1" '%s' "${v:-$def}"
  [ -z "$again" ] || spin_start
}
ask_email() {  # ask_email VAR "domanda"
  local e
  while :; do
    ask e "$2"; e=$(printf '%s' "$e" | tr -d ' ')
    if [[ "$e" =~ ^[^@]+@[^@]+\.[a-z]{2,}$ ]]; then printf -v "$1" '%s' "$e"; return; fi
    tell "  «$e» non è un indirizzo email valido, riprova."
  done
}

# ---------------------------------------------------------------- Google Cloud
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
  spin_stop
  tell ""
  tell "  Cloud Shell non è collegato al tuo account Google (succede spesso dall'app per telefono)."
  tell "  1) tocca il link che compare qui sotto e scegli l'account del progetto"
  tell "  2) consenti l'accesso, copia il codice che ti mostra Google"
  tell "  3) torna qui, incollalo e premi Invio"
  tell ""
  gcloud auth login --update-adc --no-launch-browser </dev/tty >&3 2>&3 \
    || die "login non riuscito: riprova, oppure apri Cloud Shell dal browser (shell.cloud.google.com)"
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
  terraform version 2>/dev/null | grep -q '^Terraform v' && { terraform version | head -1; return 0; }
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
vm_status() { gcloud compute instances describe "$VM" --zone "$ZONE" --project "$PROJECT" --format='value(status)'; }

wait_ssh() {
  say "Aspetto che la VM sia pronta per SSH (via IAP)…"
  for _ in $(seq 1 30); do
    if remote "true" >/dev/null 2>&1; then echo "  pronta"; return 0; fi
    sleep 10
  done
  die "la VM non risponde in SSH: controlla nella console che sia accesa e riprova «./deploy.sh up»"
}

# ---------------------------------------------------------------- passi dell'installazione
pick_project() {
  [ -f "$HERE/terraform.tfvars" ] && return 0
  local PID ALL
  PID=$(gcloud config get-value project 2>/dev/null || true)
  if [ -z "$PID" ]; then   # un solo progetto sull'account: è quello
    ALL=$(gcloud projects list --format='value(projectId)' 2>/dev/null || true)
    [ "$(grep -c . <<<"$ALL")" = 1 ] && PID=$ALL
  fi
  [ -n "$PID" ] || ask PID "ID del progetto Google Cloud (lo trovi in alto nella console, es. tacuin-123456)"
  [[ "$PID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || die "«$PID» non sembra un ID di progetto (tutto minuscolo, es. tacuin-123456)"
  sed "s|^project_id = \"[^\"]*\"|project_id = \"$PID\"|" "$HERE/terraform.tfvars.example" > "$HERE/terraform.tfvars"
  tell "  Progetto Google Cloud: $PID"
}

ask_root() {  # le sole domande della prima installazione
  tell ""
  tell "  Account root: entrerai in Tacuìn con questa email e questa password."
  tell "  Email per gli inviti, dominio, dati del titolare e nome utente li imposti poi dall'app."
  ask_email ROOT_EMAIL "Email del root (è il tuo nome utente per entrare)"
  while :; do
    ask ROOT_PASSWORD "Password del root (almeno 10 caratteri; vuoto = la creo io)"
    if [ -z "$ROOT_PASSWORD" ]; then ROOT_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20); GEN_PW=1; break; fi
    [ ${#ROOT_PASSWORD} -ge 10 ] && break
    tell "  Troppo corta (${#ROOT_PASSWORD} caratteri), riprova."
  done
}

enable_apis() {
  # Su un progetto nuovo le API sono spente e Terraform non può nemmeno leggere il progetto: le accendo prima
  local PID MISSING=() ON a
  PID=$(tfvar project_id)
  gcloud config set project "$PID"
  ON=$(gcloud services list --enabled --project "$PID" --format='value(config.name)' 2>/dev/null || true)
  for a in cloudresourcemanager serviceusage iam compute iap oslogin storage logging monitoring; do
    grep -qx "$a.googleapis.com" <<<"$ON" || MISSING+=("$a.googleapis.com")
  done
  if [ ${#MISSING[@]} -gt 0 ]; then
    gcloud services enable --project "$PID" "${MISSING[@]}" \
      || die "Google ha rifiutato l'attivazione dei servizi (fatturazione, permessi o progetto sbagliato): il motivo è nel log"
  fi
  echo "servizi attivi"
}

tf_apply() {
  terraform -chdir="$HERE" init -input=false -no-color  # senza -upgrade: le versioni restano quelle del file .terraform.lock.hcl
  if ! terraform -chdir="$HERE" state list 2>/dev/null | grep -q google_compute_instance.vm; then
    terraform -chdir="$HERE" apply -input=false -no-color -auto-approve   # prima installazione: crea e basta
    return
  fi
  # infrastruttura esistente: applico da solo, ma se qualcosa verrebbe cancellato chiedo prima
  local rc=0 summary del
  terraform -chdir="$HERE" plan -input=false -no-color -detailed-exitcode -out="$HERE/.tfplan" || rc=$?
  [ "$rc" -ne 1 ] || die "Terraform non riesce a preparare le modifiche"
  if [ "$rc" -eq 2 ]; then
    summary=$(terraform -chdir="$HERE" show -no-color "$HERE/.tfplan" | grep -E '^Plan:' || true)
    echo "$summary"
    del=$(sed -nE 's/.* ([0-9]+) to destroy.*/\1/p' <<<"$summary"); del=${del:-0}
    if [ "$del" -gt 0 ]; then
      local ok
      tell ""; tell "  Terraform vuole cancellare e ricreare $del risorse (elenco nel log). Con la VM si perdono i dati non salvati."
      ask ok "Continuo? scrivi s per sì" "n"
      [ "${ok,,}" = s ] || { rm -f "$HERE/.tfplan"; die "annullato: l'infrastruttura non è stata toccata"; }
    fi
    terraform -chdir="$HERE" apply -input=false -no-color "$HERE/.tfplan"
  else
    echo "nessuna modifica all'infrastruttura"
  fi
  rm -f "$HERE/.tfplan"
}

vm_on() {
  target
  [ "$(vm_status)" = RUNNING ] || gcloud compute instances start "$VM" --zone "$ZONE" --project "$PROJECT" --quiet
  echo "VM accesa"
}

write_env() {
  if [ -f "$SECRETS" ] && grep -q '^DOMAIN=..*' "$SECRETS"; then echo "configurazione già presente"; return 0; fi
  [ ! -f "$SECRETS" ] || mv "$SECRETS" "$SECRETS.vecchio"
  local DOMAIN ROOT_NAME PASS IP BUCKET
  # il nome si cambia dal Profilo: intanto uso la parte prima della @ (mario.rossi → Mario Rossi)
  ROOT_NAME=$(printf '%s' "${ROOT_EMAIL%%@*}" | tr '._-' '   ' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)} 1')
  # indirizzo provvisorio con HTTPS vero: sslip.io risponde con l'IP scritto nel nome (34-154-57-95.sslip.io → 34.154.57.95).
  # Il tuo dominio lo aggiungi dall'app (Impostazioni → Server → Dominio) e diventa quello principale.
  IP=$(tfout ip || true)
  [[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "non trovo l'IP della VM (terraform output ip): rilancia «./deploy.sh up»"
  DOMAIN="${IP//./-}.sslip.io"
  PASS=$(openssl rand -base64 33 | tr -d '\n')
  BUCKET=$(tfout backup_bucket || true)
  umask 077
  # Scrivo con Python (niente problemi di caratteri speciali) in un file temporaneo, poi lo controllo e lo rinomino:
  # se qualcosa va storto non resta un app.env vuoto o a metà
  V_COMPOSE_PROFILES=https V_DOMAIN=$DOMAIN V_ROOT_EMAIL=$ROOT_EMAIL V_ROOT_NAME=$ROOT_NAME \
  V_ROOT_PASSWORD=$ROOT_PASSWORD V_SMTP_HOST='' V_SMTP_PORT=587 V_SMTP_SECURITY=starttls V_SMTP_USER='' V_SMTP_PASSWORD='' V_SMTP_FROM='' \
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
  echo "configurazione salvata in $SECRETS (sito $DOMAIN)"
  [ -z "${GEN_PW:-}" ] || ROOT_PW_SHOW=$ROOT_PASSWORD   # le mostro alla fine, insieme all'indirizzo
  BACKUP_PASS_SHOW=$PASS
}

copy_files() {
  remote "rm -rf ~/deploy && mkdir -p ~/deploy"
  gcloud compute scp --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap --quiet \
    "$DEPLOY/docker-compose.yml" "$DEPLOY/Caddyfile" "$DEPLOY/.env.example" "$DEPLOY/setup.sh" "$DEPLOY/backup.sh" \
    "$DEPLOY/update.sh" "$DEPLOY/restore.sh" "$DEPLOY/archivio.py" "$VM:~/deploy/"
  # gcloud compute ssh via IAP non inoltra in modo affidabile lo stdin: la configurazione viaggia nel comando
  # stesso, in base64, dentro il tunnel cifrato (sulla VM ci sei solo tu), e viene scritta direttamente con permessi 600
  local B64; B64=$(base64 -w0 < "$SECRETS")
  remote "sudo mkdir -p /opt/tracker && echo '$B64' | base64 -d | sudo tee /opt/tracker/.env >/dev/null && sudo chmod 600 /opt/tracker/.env"
  if ! remote "sudo grep -q '^DOMAIN=..*' /opt/tracker/.env"; then
    echo "Diagnostica: righe locali $(grep -c . "$SECRETS"), DOMAIN locale: $(grep -c '^DOMAIN=..*' "$SECRETS")"
    remote "sudo ls -la /opt/tracker/; echo righe sulla VM: \$(sudo grep -c . /opt/tracker/.env)" || true
    die "sulla VM la configurazione non è arrivata"
  fi
  echo "file e configurazione copiati"
}

prepare_vm() { remote "sudo BLOCK_METADATA=1 FROM_DEPLOY=1 bash ~/deploy/setup.sh"; }

# immagini Docker del repository da cui lanci lo script (questo progetto, un fork o la vecchia copia privata)
image_repo() {
  local u; u=$(git -C "$HERE" config --get remote.origin.url 2>/dev/null || true)
  u=${u%.git}; u=${u#*github.com[:/]}
  [[ "$u" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] && echo "ghcr.io/${u,,}"
}
set_image() {
  local img; img=$(image_repo || true)
  [ -n "$img" ] || return 0
  remote "cd /opt/tracker && if sudo grep -q '^TACUIN_IMAGE=' .env; then sudo sed -i 's|^TACUIN_IMAGE=.*|TACUIN_IMAGE=$img|' .env; else echo 'TACUIN_IMAGE=$img' | sudo tee -a .env >/dev/null; fi"
  echo "Immagini: $img"
}
# il codice è lo stesso del progetto pubblico: se le immagini del tuo repository sono private, uso quelle pubbliche
PUBLIC_IMAGE=ghcr.io/denzel18/tacuin
use_public_image() {
  remote "cd /opt/tracker && sudo sed -i 's|^TACUIN_IMAGE=.*|TACUIN_IMAGE=$PUBLIC_IMAGE|' .env && sudo grep -q '^TACUIN_IMAGE=' .env || echo 'TACUIN_IMAGE=$PUBLIC_IMAGE' | sudo tee -a .env >/dev/null"
}
images() {
  set_image
  if remote "cd /opt/tracker && sudo docker compose pull -q tracker"; then
    echo "immagini scaricate"
  elif use_public_image && remote "cd /opt/tracker && sudo docker compose pull -q tracker"; then
    echo "immagini pubbliche del progetto ($PUBLIC_IMAGE)"
  elif remote "sudo test -f /root/.docker/config.json && sudo grep -q ghcr.io /root/.docker/config.json"; then
    echo "accesso a GitHub già configurato"
  else
    local GH_USER GH_TOKEN T64
    tell ""
    tell "  Le immagini dell'app sono private: serve un token GitHub con il solo permesso «read:packages»"
    tell "  (github.com → Settings → Developer settings → Personal access tokens → Tokens (classic))."
    ask GH_USER "Utente GitHub"
    ask GH_TOKEN "Token"
    # il token viaggia nel tunnel cifrato e arriva a docker da stdin (non resta nella cronologia della VM né nel log)
    T64=$(printf '%s' "$GH_TOKEN" | base64 -w0)
    remote "echo '$T64' | base64 -d | sudo docker login ghcr.io -u '$GH_USER' --password-stdin" >/dev/null \
      || die "GitHub ha rifiutato il token: serve un token (classic) con «read:packages»"
  fi
}

start_app() {
  remote "cd /opt/tracker && sudo docker compose pull -q && (sudo docker compose up -d --wait --wait-timeout 180 vision || echo 'ATTENZIONE: il servizio scontrini non risponde ancora') && sudo docker compose up -d --remove-orphans && sleep 15 && sudo docker compose ps"
  local ok=""
  for _ in 1 2 3 4 5 6; do
    if remote "cd /opt/tracker && sudo docker compose exec -T tracker python -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=5).read().decode())\""; then ok=1; break; fi
    sleep 10
  done
  [ -n "$ok" ] || die "l'app non risponde dopo l'avvio"
  if remote "cd /opt/tracker && sudo docker compose exec -T tracker python -c \"import os,urllib.request;urllib.request.urlopen(os.environ['VISION_URL']+'/health',timeout=5)\""; then
    echo "scontrini da foto: attivi"
  else
    VISION_WARN=1; echo "ATTENZIONE: scontrini da foto non attivi (docker compose logs vision)"
  fi
}

# ---------------------------------------------------------------- comandi
cmd_up() {
  need gcloud; need openssl; need python3
  [ -n "${KEEP_LOG:-}" ] || { : >"$LOG"; chmod 600 "$LOG"; }
  login                     # eventuale accesso a Google prima della barra
  pick_project
  [ -f "$SECRETS" ] && grep -q '^DOMAIN=..*' "$SECRETS" || ask_root

  bar_begin "Installo Tacuìn su Google Cloud" 10
  step "Preparo gli strumenti" ensure_terraform
  export PATH="$HOME/.local/bin:$PATH"
  step "Attivo i servizi Google" enable_apis
  step "Creo l'infrastruttura (VM, rete, backup)" tf_apply
  step "Accendo la VM" vm_on
  step "Salvo la configurazione" write_env
  step "Aspetto che la VM risponda" wait_ssh
  step "Copio i file sulla VM" copy_files
  step "Preparo il server (Docker, firewall, backup)" prepare_vm
  step "Scarico l'app" images
  step "Avvio Tacuìn" start_app
  bar_end

  local IP DOMAIN
  IP=$(tfout ip || true); DOMAIN=$(grep '^DOMAIN=' "$SECRETS" | cut -d= -f2)
  tell ""
  tell "✓ Tacuìn è online: https://$DOMAIN"
  tell "  (il certificato HTTPS arriva da solo: se la prima volta non si apre, riprova tra un minuto)"
  tell ""
  tell "  • Entra con $(grep '^ROOT_EMAIL=' "$SECRETS" | cut -d= -f2) e la tua password."
  tell "  • La procedura guidata ti chiede il resto: email per gli inviti, dominio, dati del titolare, nome utente."
  tell "    Per il tuo dominio: nel DNS crea un record A → $IP, poi aggiungilo da Impostazioni → Server → Dominio."
  [ -z "${VISION_WARN:-}" ] || tell "  • Scontrini da foto non ancora attivi: dettagli in $LOG"
  [ -z "${ROOT_PW_SHOW:-}" ] || tell "  • Password del root (generata): $ROOT_PW_SHOW   ← salvala nel gestore di password"
  if [ -n "${BACKUP_PASS_SHOW:-}" ]; then
    tell "  • Passphrase dei backup: $BACKUP_PASS_SHOW"
    tell "    ↑ salvala nel gestore di password: senza, i backup cifrati non si aprono (è anche in deploy/gcp/app.env)."
  fi
  tell ""
}

update_files() {
  # su questa VM Tacuìn non c'è (VM nuova, ricreata o di un'altra installazione): aggiornare non ha senso
  remote "test -f /opt/tracker/.env" \
    || die "Su «$VM» (progetto $PROJECT) Tacuìn non è installato: /opt/tracker non c'è. Se è la VM giusta, lancia «./deploy.sh up»."
  remote "rm -rf ~/deploy && mkdir -p ~/deploy"
  gcloud compute scp --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap --quiet \
    "$DEPLOY/docker-compose.yml" "$DEPLOY/Caddyfile" "$DEPLOY/.env.example" "$DEPLOY/backup.sh" \
    "$DEPLOY/update.sh" "$DEPLOY/restore.sh" "$DEPLOY/archivio.py" "$VM:~/deploy/"
  set_image
  # l'IP pubblico serve a Tacuìn per controllare i nuovi domini aggiunti dall'interfaccia
  IP=$(gcloud compute instances describe "$VM" --zone "$ZONE" --project "$PROJECT" --format='value(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null || true)
}
update_run() {
  remote "set -e; cd /opt/tracker
    CH=\$(sudo sha256sum Caddyfile 2>/dev/null | cut -c1-64 || true)
    for f in docker-compose.yml Caddyfile .env.example backup.sh update.sh restore.sh archivio.py; do sudo install -m \$( [ \"\${f##*.}\" = sh ] && echo 755 || echo 644 ) ~/deploy/\$f \$f; done
    if [ -n '$IP' ] && ! sudo grep -q '^PUBLIC_IP=' .env; then echo 'PUBLIC_IP=$IP' | sudo tee -a .env >/dev/null; fi
    sudo ./update.sh
    if [ \"\$CH\" != \"\$(sudo sha256sum Caddyfile | cut -c1-64)\" ]; then sudo docker compose restart caddy >/dev/null 2>&1 || true; echo 'Caddy riavviato con la nuova configurazione.'; fi"
}
cmd_update() {
  : >"$LOG"; chmod 600 "$LOG"
  target
  local IP=""
  bar_begin "Aggiorno Tacuìn" 2
  step "Copio i file aggiornati" update_files
  step "Backup, nuova versione e riavvio" update_run
  bar_end
  tell "✓ Aggiornato. Dettagli in $LOG"
}
cmd_ssh()    { target; gcloud compute ssh "$VM" --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap; }
cmd_logs()   { target; remote "cd /opt/tracker && sudo docker compose logs -f --tail 100 tracker"; }
cmd_status() { target; remote "cd /opt/tracker && sudo docker compose ps && uptime && df -h / | tail -1"; }
cmd_backup() {
  : >"$LOG"; target
  bar_begin "Backup" 1
  step "Backup cifrato nel bucket" remote "sudo /opt/tracker/backup.sh"
  bar_end
  tell "✓ Backup fatto."
}

stop_backup() { remote "sudo /opt/tracker/backup.sh" || echo "(backup non riuscito: spengo comunque, i dati restano sul disco)"; }
cmd_stop() {
  : >"$LOG"; target
  [ "$(vm_status)" = RUNNING ] || { tell "La VM è già spenta ($(vm_status))."; return; }
  bar_begin "Spengo la VM" 2
  step "Backup prima di spegnere" stop_backup
  step "Spengo la VM" gcloud compute instances stop "$VM" --zone "$ZONE" --project "$PROJECT" --quiet
  bar_end
  tell ""
  tell "Spenta. Il sito, le notifiche, i promemoria e il backup notturno sono fermi finché non la riaccendi."
  tell "Mentre è spenta paghi solo disco, snapshot e IP statico (qualche euro al mese), non la VM."
  tell "Per riaccenderla:  ./deploy.sh start"
}

wait_app() {
  for _ in $(seq 1 18); do
    if remote "cd /opt/tracker && sudo docker compose exec -T tracker python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=5)\""; then return 0; fi
    sleep 10
  done
  remote "cd /opt/tracker && sudo docker compose up -d && sudo docker compose ps"
}
cmd_start() {
  : >"$LOG"; target
  bar_begin "Riaccendo Tacuìn" 3
  step "Accendo la VM" vm_on
  step "Aspetto che la VM risponda" wait_ssh
  step "Aspetto che Tacuìn riparta" wait_app
  bar_end
  tell "✓ Tacuìn è di nuovo online: https://$(grep '^DOMAIN=' "$SECRETS" 2>/dev/null | cut -d= -f2)"
}

# Ripartire da zero: niente dati vecchi da nessuna parte (VM, backup nel bucket, snapshot del disco).
# L'IP statico, la rete e il bucket restano: il dominio continua a puntare qui.
reset_unprotect() {
  terraform -chdir="$HERE" init -input=false -no-color
  terraform -chdir="$HERE" apply -input=false -no-color -auto-approve -var deletion_protection=false -target=google_compute_instance.vm
}
reset_bucket() {
  local BUCKET; BUCKET=$(tfout backup_bucket || true)
  [ -z "$BUCKET" ] || gcloud storage rm --recursive --all-versions "gs://$BUCKET/**" --project "$PROJECT" --quiet || echo "(bucket già vuoto)"
}
reset_vm() {
  terraform -chdir="$HERE" apply -input=false -no-color -auto-approve -var deletion_protection=false \
    -replace=google_compute_instance.vm -replace=google_compute_disk_resource_policy_attachment.snapshots
  # gli snapshot del disco vecchio restano finché non li cancello io (regola KEEP_AUTO_SNAPSHOTS)
  local SNAPS
  SNAPS=$(gcloud compute snapshots list --project "$PROJECT" --filter="sourceDisk~/disks/$VM\$" --format='value(name)' 2>/dev/null || true)
  # shellcheck disable=SC2086
  [ -z "$SNAPS" ] || gcloud compute snapshots delete $SNAPS --project "$PROJECT" --quiet
}
cmd_reset() {
  need gcloud; : >"$LOG"; target
  export PATH="$HOME/.local/bin:$PATH"
  tell ""
  tell "ATTENZIONE: questo comando cancella in modo definitivo"
  tell "  • la VM «$VM» e il suo disco: utenti, spese, appuntamenti, diario, impostazioni di Tacuìn"
  tell "  • tutti i backup nel bucket e tutti gli snapshot del disco"
  tell "Poi ricrea la VM da zero e avvia Tacuìn vuoto. Restano l'IP statico, la rete e il bucket (vuoto)."
  tell "Non si torna indietro."
  local ok again
  ask ok "Per continuare scrivi CANCELLA TUTTO"
  [ "$ok" = "CANCELLA TUTTO" ] || die "annullato: non ho toccato niente"
  ask again "Rifaccio anche email e password del root? (s/N)" "n"

  bar_begin "Cancello tutto" 4
  step "Preparo gli strumenti" ensure_terraform
  step "Tolgo la protezione dalla cancellazione" reset_unprotect
  step "Cancello i backup nel bucket" reset_bucket
  step "Ricreo la VM da zero" reset_vm
  bar_end
  if [ "${again,,}" = s ] && [ -f "$SECRETS" ]; then
    mv "$SECRETS" "$SECRETS.vecchio"; tell "  La configurazione vecchia è in $SECRETS.vecchio"
  fi
  KEEP_LOG=1 cmd_up   # rimette anche la protezione dalla cancellazione (deletion_protection del file terraform.tfvars)
  tell "Tacuìn è ripartito vuoto. Per 7 giorni Google tiene i file cancellati dal bucket in un cestino recuperabile."
}

case "${1:-}" in
  up) cmd_up ;; update) cmd_update ;; ssh) cmd_ssh ;; logs) cmd_logs ;; status) cmd_status ;; backup) cmd_backup ;;
  stop) cmd_stop ;; start) cmd_start ;; reset) cmd_reset ;;
  *) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
