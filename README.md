<p align="center"><img src="brand/tacuin-logo.svg" alt="Tacuìn" height="64"></p>

# Tacuìn — il tuo secondo cervello

*Tacuìn*, in milanese, vuol dire taccuino. Giornate, abitudini, spese (anche condivise), investimenti, viaggi, palestra, diario e progetti personalizzati, con:
- **più utenti**, ruoli e approvazione degli accessi;
- **assistente**: scrivi come parli («12,50 pizza», «dentista venerdì alle 15:30») e impara dalle tue spese;
- **app installabile sul telefono** con **notifiche** per promemoria, appuntamenti e inviti, più le email;
- **inviti agli appuntamenti** via email a chiunque, con risposte Partecipo / Forse / Non partecipo;
- **scansione degli scontrini**: scatti o carichi la foto e la spesa si compila da sola;
- **quattro aspetti** (automatico, chiaro, scuro, luce calda) e una **guida dentro l'app** (Impostazioni → Istruzioni).

> **Versione di prova.** Tacuìn è in fase di test: può contenere errori ed è offerto senza garanzie. Ogni pagina lo ricorda con un piè di pagina e la nota completa.

- **Avvio con doppio clic** su Windows, oppure in automatico all'accensione del PC, in background.
- **Niente Apache, niente librerie da installare** per l'app: basta Python 3.
- **Pronto per Docker**: in un container nel cloud, insieme al servizio che legge gli scontrini.
- **In produzione su Google Cloud** (Milano): una VM con Caddy, app e lettura scontrini in Docker, backup cifrati e SSH solo tramite IAP. Schema completo: [architettura su Google Cloud](deploy/gcp/README.md).

```
tacuin\
├── Avvia Tracker.bat                  doppio clic per avviarlo (Windows)
├── Attiva / Disattiva avvio automatico.bat
├── PC sempre acceso (alla corrente).bat · Ripristina standby PC.bat
├── server.py                          web server, API, assistente, inviti, notifiche
├── webpush.py                         notifiche push del browser (Web Push con VAPID, nessun servizio esterno)
├── iam.py                             database SQLite, utenti, ruoli, sessioni, registro attività
├── brain.py                           "cervello": Naive Bayes, domande, insight
├── notify.py                          email (SMTP) e promemoria
├── travel.py                          viaggi: luoghi e meteo (Open-Meteo)
├── web\index.html                     la web app
├── vision\                            servizio scontrini: OpenCV + Tesseract (container separato)
├── data\                              database, backup e log (creata al primo avvio)
├── .env.example                       modello della configurazione
└── Dockerfile, docker-compose.yml     per Docker / cloud
```

## Installa il tuo Tacuìn

Tacuìn è **software libero**: ognuno può installarne una copia tutta sua, con i propri utenti e i propri dati. Chi installa è il titolare dei dati della sua installazione; nessun dato passa da altri server.

| Dove | Per chi | Istruzioni |
| --- | --- | --- |
| Sul tuo PC Windows | Uso personale o in famiglia, sulla rete di casa | [qui sotto](#1-installa-python-una-volta-sola) |
| Su un server con Docker | Online, con HTTPS, su qualsiasi VPS | [Cloud con Docker](#cloud-con-docker-quando-vorrai) |
| Su Google Cloud | Online in 10 minuti, tutto automatico | [Su Google Cloud](#su-google-cloud-consigliato-milano) · [architettura](deploy/gcp/README.md) |

Dopo il primo accesso, il root trova una schermata di benvenuto con quattro cose da sistemare: **email** (server di posta per inviti, promemoria e password), **dominio**, **dati del titolare** per l'informativa privacy e il proprio **@nome utente**. Email e dominio si cambiano anche dopo, da *Impostazioni → Server*, senza toccare file di configurazione.

---

## 1. Installa Python (una volta sola)

1. Vai su **https://www.python.org/downloads/** e scarica l'ultima versione per Windows.
2. Avvia l'installazione e, **nella prima schermata, spunta "Add python.exe to PATH"**. Poi premi *Install Now*.

Per controllare che sia installato, apri il **Prompt dei comandi** (tasto Windows, scrivi `cmd`, Invio) e scrivi `py --version`: deve comparire `Python 3.x.x`.

> In alternativa, nel Prompt dei comandi: `winget install Python.Python.3.12`

## 2. Metti la cartella al posto giusto

Estrai lo zip e sposta la cartella `tracker-server` in **`C:\Users\<tuo nome>\tracker-server`**.

> Meglio evitare Desktop e Documenti: spesso sono sincronizzati da OneDrive, che può interferire con il file dei dati.

## 3. Primo avvio

1. Fai doppio clic su **`Avvia Tracker.bat`**.
   - Se compare *"Windows ha protetto il PC"*, clicca **Ulteriori informazioni → Esegui comunque**. È normale per gli script scaricati.
2. Al primo avvio si apre il **Blocco note** con il file `.env`: controlla le impostazioni (per le email vedi «Promemoria e notifiche»), **salva e chiudi**.
3. Il browser si apre da solo su **http://localhost:8080**.
4. La prima volta la pagina ti chiede di **creare l'account root**, cioè l'amministratore principale: nome, email e password. Se avevi già dei dati della versione precedente, vengono spostati in questo account.
5. Parte un breve **tour** di tre schermate; lo rivedi quando vuoi da **Impostazioni → Istruzioni**.

La finestra nera è il server: **lasciala aperta** finché usi il tracker. Chiudendola, il tracker si ferma.

## 4. Avvio automatico (consigliato)

Doppio clic su **`Attiva avvio automatico.bat`**: da adesso il tracker parte da solo, **senza finestre**, ogni volta che accedi a Windows. Ti basta aprire http://localhost:8080 (conviene metterlo tra i preferiti).

- Per toglierlo e fermare il tracker in background: **`Disattiva avvio automatico.bat`**.
- Con l'avvio automatico attivo non serve più usare `Avvia Tracker.bat`. Se lo apri comunque, ti avvisa che il tracker è già in esecuzione.
- Dopo aver modificato `.env`, esegui **Disattiva** e poi **Attiva** per applicare le modifiche.

## 5. Tracker attivo anche quando il PC andrebbe in standby

Un PC in standby è "addormentato" e non può eseguire nessun programma, quindi il tracker (e i promemoria) si fermano finché non si risveglia. Per questo:

- **Mentre il tracker è acceso, il PC non va più in standby da solo per inattività.** Lo schermo invece si spegne normalmente. È automatico; per disattivarlo metti `KEEP_AWAKE=0` nel `.env`. Nel log trovi `Standby automatico del PC: bloccato mentre il tracker è attivo`.
- **Portatile con il coperchio chiuso:** chiudere il coperchio manda comunque in standby. Se vuoi che il tracker resti attivo anche a coperchio chiuso quando il PC è **collegato alla corrente**, fai doppio clic su **`PC sempre acceso (alla corrente).bat`**. A batteria il PC continua a sospendersi normalmente. Per tornare come prima usa `Ripristina standby PC.bat`.
- Se il PC viene sospeso a mano (Start → Sospendi) o si scarica la batteria, alla ripresa il tracker recupera da solo: i promemoria saltati partono entro 2 ore.

> Per avere il tracker **davvero sempre attivo** senza tenere acceso il PC, la soluzione è metterlo in **Docker** su un piccolo server sempre acceso: una VPS da pochi euro al mese, un mini PC, un NAS o un Raspberry Pi. Vedi "Cloud con Docker".

---

## Avvio per i test

Per chi deve solo provare l'app: doppio clic su **`Avvia per test.bat`**. Lo script fa tutto da solo:

1. **Aggiorna il codice**: controlla su GitHub se c'è una versione nuova, mostra le novità e la scarica (`git pull`). Se hai file modificati non scarica niente, per non sovrascriverli, e te lo dice. Se cambia anche lo script di avvio, riparte con la versione nuova.
2. **Avvia la scansione scontrini**: accende Docker Desktop se è spento, avvia il servizio `vision` e aspetta che risponda. Senza Docker prosegue lo stesso, senza scansione.
3. **Avvia il Tracker** e apre il browser. I log compaiono nella finestra e vengono scritti su file mentre arrivano.
4. **Alla chiusura** (premi **Ctrl+C**): ferma il Tracker e il servizio scontrini, scrive il riepilogo e archivia la sessione. Se chiudi la finestra con la X la sessione non si perde: viene chiusa e archiviata al prossimo avvio.

Dove finisce tutto (cartella `logs`, esclusa da GitHub):

| Percorso | Contenuto |
|---|---|
| `logs/sessioni/<data>_<commit>/` | `avvio.log` (aggiornamento e avvio), `tracker.log`, `vision.log`, `docker.log`, `sessione.json` (riepilogo: versione, commit scaricati, durata, errori, dove è archiviata) |
| `logs/archivio/AAAA/MM/` | lo `.zip` di ogni sessione (archivio `local`) |
| `logs/da-caricare/` | gli `.zip` non ancora caricati (es. S3 non raggiungibile): li ricarica da solo al prossimo avvio |

Token e password del `.env` vengono oscurati (`***`) prima di scrivere i log. Le cartelle di sessione già archiviate si cancellano dopo 30 giorni (`TEST_KEEP_DAYS`), gli `.zip` restano.

**Archiviare su S3 o simili**: nel `.env` metti `ARCHIVE_BACKEND=s3` e compila `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` (e `S3_REGION`, `S3_PREFIX` se servono). Va bene qualsiasi servizio compatibile S3: AWS, MinIO, Cloudflare R2, Backblaze B2, Wasabi. Non serve installare niente. Per aggiungere un'altra destinazione basta una classe in `tools/archivio.py`.

Da terminale, anche su Mac e Linux: `python3 tools/avvio_test.py` (opzioni `--no-pull`, `--no-vision`, `--keep-vision`).

## Utenti e accessi

- **Root** (l'account creato al primo avvio): può fare tutto, nomina gli amministratori e non si può disattivare.
- **Admin**: approva le richieste, disattiva o riattiva gli utenti, genera link per una nuova password.
- **Utente**: usa il tracker con i propri dati.

**Come entra una nuova persona:**
1. Apre la pagina del tracker → **Richiedi accesso**, sceglie un **@nome utente** unico e conferma la sua email con il link che riceve.
2. Root o admin trova la richiesta in **🛡 Utenti**. Il numero rosso nel menu indica quante ce ne sono in attesa.
3. Sceglie il ruolo e preme **Approva**. Da quel momento la persona può accedere.

**Privacy:** ogni utente vede solo i propri dati. Root e admin gestiscono gli account ma **non possono vedere** spese, abitudini o tracker degli altri: nell'elenco utenti vedono solo nome, email, ruolo e stato, e il registro mostra solo le operazioni di gestione, senza IP né dettagli personali.

**Nome utente (@):** serve agli altri per trovarti (per esempio per dividere una spesa con te) senza vedere la tua email. La ricerca chiede almeno 3 caratteri e mostra solo nome e @; con **Profilo → Non farti trovare** ti trova solo chi scrive il tuo nome utente esatto. Chi viene indicato in una spesa riceve solo una notifica (nei suoi dati non viene scritto nulla) e può bloccare un mittente dal Profilo.

**Sicurezza:**
- le password sono salvate cifrate con PBKDF2;
- dopo 8 tentativi falliti dalla stessa rete l'accesso si blocca per 15 minuti (chi sbaglia password non blocca il vero titolare);
- cambiare l'email richiede la password attuale e la conferma dal nuovo indirizzo; cambiare la password scollega gli altri dispositivi;
- gli script della pagina partono solo con un codice valido per quella risposta (CSP con *nonce*): un testo inserito da un altro utente non può eseguire codice;
- inviti limitati per utente (15 all'ora, 30 al giorno, 10 per i nuovi account) e per tutto il server (`INVITES_SERVER_PER_DAY`, predefinito 400);
- in **📜 Registro attività** trovi accessi, approvazioni e modifiche agli account.

Dal **Profilo** ognuno può cambiare nome, email e password, uscire da tutti i dispositivi, esportare i propri dati ed eliminare il proprio account.

**Password dimenticata:** l'admin apre il menu ⋯ dell'utente → *Link per nuova password*: il link arriva **solo all'utente**, via email, e vale 24 ore. Senza email configurate l'opzione non è disponibile, così nessun amministratore può entrare nell'account di un altro.

---

### Logo

Il logo («Il secondo foglio») è nella cartella [`brand/`](brand/): SVG per sfondi chiari e scuri, icona dell'app in PNG, favicon. Disegno originale con licenza **CC BY 4.0**; il nome è disegnato con il font Inter (SIL OFL). Dettagli in [`brand/LICENSE.md`](brand/LICENSE.md).

### Verifica in due passaggi

Da **Profilo → Verifica in due passaggi → Attiva** (serve la password): inquadri il QR con un'app di autenticazione (Google Authenticator, Microsoft Authenticator, 1Password, Bitwarden…), scrivi il codice e ricevi **8 codici di recupero** da conservare (valgono una volta sola). Da quel momento all'accesso, dopo la password, serve il codice di 6 cifre dell'app. Consigliata per root e admin.

Telefono perso: entra con un codice di recupero, oppure un admin (root per gli admin) usa **Utenti → ⋯ → Azzera la verifica in due passaggi**, che chiude anche tutte le sessioni dell'utente.

## Usarlo dal telefono (stessa rete Wi-Fi)

1. Nel file `.env` imposta `HOST=0.0.0.0`. Chi apre la pagina dovrà comunque accedere con il proprio account.
2. Riavvia il tracker. Se Windows mostra l'avviso del Firewall, scegli **Reti private** → *Consenti*. Se l'avviso non compare (succede con l'avvio automatico), apri il Prompt dei comandi come amministratore e scrivi:
   ```bat
   netsh advfirewall firewall add rule name="Tracker" dir=in action=allow protocol=TCP localport=8080 profile=private
   ```
3. Trova l'indirizzo del PC con `ipconfig` (riga *Indirizzo IPv4*, es. `192.168.1.23`).
4. Sul telefono apri `http://192.168.1.23:8080`, poi *Condividi → Aggiungi alla schermata Home*. Le **notifiche** però richiedono HTTPS: in casa con `http://` non arrivano (vanno da `localhost` sul PC e in cloud con il dominio).

Su telefono in basso c'è una barra con **Home, Calendario, +, Spese e Altro**: «Altro» apre il menu con tutte le altre sezioni (si apre anche con uno swipe dal bordo sinistro). Con **Impostazioni → Personalizza menu** (in fondo al menu) riordini le sezioni trascinandole e nascondi quelle che non usi; i **Progetti** si fissano in alto con lo spillo, gli altri stanno sotto «Mostra altri». Su tablet il menu diventa una colonna di sole icone, su PC è completo. Ricaricando la pagina resti dove eri, e il tasto "indietro" funziona. La rete Windows deve essere impostata come *Privata*.

La grafica è la stessa su telefono, tablet e PC: un unico set di icone a linee (niente emoji nell'interfaccia, che cambiano aspetto da un sistema all'altro), campi di testo a 16 px (così l'iPhone non ingrandisce la pagina quando li tocchi) e pulsanti grandi almeno 44 px. Sul telefono le tabelle diventano schede, modificabili come sul PC.

---

## App sul telefono e notifiche

Tacuìn si **installa sul telefono** come un'app (è una *PWA*: niente App Store, si aggiorna da sola):
- **iPhone/iPad**: apri il sito in Safari → **Condividi → «Aggiungi alla schermata Home»**, poi apri Tacuìn dall'icona (serve iOS 16.4 o successivo per le notifiche);
- **Android**: in Chrome menu **⋮ → «Installa app»**;
- **computer**: Chrome ed Edge mostrano «Installa» nella barra dell'indirizzo.

In **Profilo → Notifiche** (anche da Impostazioni → Notifiche e promemoria) tocchi **«Attiva le notifiche»** su ogni dispositivo: vedi l'elenco dei dispositivi iscritti, mandi una notifica di prova e la togli quando vuoi.

Come funziona: il server crea al primo avvio una chiave (VAPID) in `data/vapid_private.pem`, cifra ogni notifica per il singolo dispositivo e la consegna al servizio push del browser (Apple, Google, Mozilla, Microsoft): nessun account o chiave di terzi. Le iscrizioni scadute vengono tolte da sole. Serve la libreria `cryptography` (già nell'immagine Docker; sul PC: `pip install cryptography`).

## Promemoria e notifiche

Ogni utente sceglie i propri promemoria in **Profilo → Promemoria e avvisi**: per ciascuno l'orario e il canale (**notifica** e/o **email**).

| Promemoria | Quando (predefinito) | Canale predefinito | Contenuto |
|---|---|---|---|
| Riepilogo del mattino | ogni giorno 08:00 | email | abitudini da fare, scadenze vicine, uscite del mese e previsione |
| Abitudini non fatte | ogni giorno 21:00 | notifica | solo se ne manca qualcuna |
| Scadenze dei progetti | ogni giorno 09:00 | notifica + email | voci che scadono oggi o domani |
| Resoconto settimanale | lunedì 08:00 | email | uscite della settimana, confronto, abitudini e insight |
| Debiti nelle spese condivise | sabato 10:00 | notifica | chi ti deve ancora dei soldi |

- I promemoria partono solo se c'è qualcosa da dire, e al massimo una volta al giorno per tipo.
- Gli **avvisi degli appuntamenti** arrivano come notifica; se non hai attivato le notifiche su nessun dispositivo, arrivano via email.
- Se il PC era spento all'ora giusta, arrivano appena riparte, entro 2 ore.
- Con **Prova come notifica** e **Prova via email** controlli subito che tutto funzioni.

Le email servono anche per:
- avvisare gli admin di una nuova richiesta di accesso (anche con notifica);
- avvisare la persona quando viene approvata;
- inviare il link per una nuova password;
- i messaggi **«Scrivi all'amministratore»** della pagina di accesso (vedi sotto).

### Configurare le email con Gmail (5 minuti)

1. Sull'account Google che userai per inviare, attiva la **verifica in due passaggi**: *Account Google → Sicurezza*.
2. Apri **https://myaccount.google.com/apppasswords**, crea una password per l'app chiamata "Tracker" e copia le 16 lettere.
3. Nel file `.env`:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=tuoindirizzo@gmail.com
   SMTP_PASSWORD=abcdefghijklmnop
   ```
4. Riavvia il tracker. Nel log compare `Email: attive tramite smtp.gmail.com`.

Con altri servizi (Brevo, Mailgun, Outlook) basta usare host, porta e credenziali SMTP che ti forniscono. Per la porta 465 imposta `SMTP_SECURITY=ssl`.

> Gmail ha un limite di circa 500 email al giorno: per un uso personale o di famiglia basta e avanza. Per molti utenti conviene un servizio dedicato.

---

## Calendario

Il **Calendario** (mese, settimana o agenda dei prossimi 30 giorni) mostra tutto quello che succede, giorno per giorno:

- **Appuntamenti**: sono l'unica cosa che scrivi qui. Titolo, giorno, ora (o «tutto il giorno»), luogo, note, ripetizione (ogni settimana, mese o anno) e promemoria (all'ora, 15 minuti o 1 ora prima, il giorno prima, oppure nel riepilogo del mattino). Gli avvisi arrivano come notifica, o via email se non hai attivato le notifiche.
- **Tutto il resto arriva da solo** dalle altre sezioni, senza copie: spese (anche condivise), allenamenti, viaggi (dalla partenza al ritorno), scadenze delle righe dei tracker con una data, abitudini fatte (accendile dai filtri). Se modifichi una spesa, cambia anche nel calendario; toccando una voce si apre dove è nata.
- Nella **Home** il pannello «Oggi» mostra gli appuntamenti di oggi e i prossimi; il **+** propone «Nuovo appuntamento».

Dall'assistente:
- **dentista mercoledì alle 15:30** · **riunione 12/11 ore 9** · **calcetto ogni giovedì alle 19** · **compleanno Giulia 25 novembre** (si ripete ogni anno)
- **cosa ho domani** · **agenda** (prossimi 7 giorni) · **cosa è successo ieri** · **cancella appuntamento dentista**

## Icone

Ogni sezione e ogni tracker ha la sua icona: **toccala** in alto accanto al titolo per sceglierne un'altra tra oltre 80 (con la ricerca: «musica», «auto», «salute»…). Le icone sono disegnate apposta per questo progetto, quindi non hanno diritti di terzi. «Predefinita» rimette quella originale.

---

## Da fare del giorno

Nel **Calendario**, sotto ogni giorno, c'è la lista delle cose da fare: scrivi e premi Invio, tocca il quadratino per spuntarle. Quelle non fatte passano da sole a oggi con la scritta «rimandata». Le trovi anche nella **Home** (pannello «Oggi») e nel **riepilogo del mattino**.
Dall'assistente: **da fare: pagare il bollo, chiamare Marco** · **da fare domani: …** · **da fare** (con i pulsanti per spuntarle).

## Diario

Una pagina al giorno: l'**umore** (cinque faccine), il testo (si salva da solo) e i **tag**. Sotto trovi le pagine dello stesso giorno negli anni passati e il mese colorato per umore; «Tutte le pagine» ha la ricerca e i filtri per tag. Ogni pagina compare nel calendario.
Scrivere nel diario segna da sola l'abitudine **«Scrivere nel diario»**, che i nuovi utenti hanno già pronta (chi c'era già può aggiungerla con quel nome).
Dall'assistente: **diario: oggi giornata lenta e bella** aggiunge il testo alla pagina di oggi; **diario** la mostra.

## Cestino

Quello che elimini (progetti, voci, spese, saldi, investimenti, abitudini, viaggi, appuntamenti, allenamenti, corsi, schede, cose da fare, pagine del diario) va nel **Cestino** e resta lì **30 giorni**, poi sparisce da solo. Subito dopo l'eliminazione compare «Annulla»; dal Cestino puoi **ripristinare** nel punto dov'era, **eliminare per sempre** o **svuotare** tutto. Anche quello che elimini dall'assistente finisce lì. Sugli **allenamenti, appuntamenti e viaggi** le azioni sono sempre a vista: **⋯** a destra di ogni riga, sul telefono **scorri a sinistra** (Modifica / Elimina) o **tieni premuto**, sul computer passando sopra la riga compaiono le icone.

## Partecipanti agli appuntamenti e inviti

Nell'appuntamento aggiungi le persone con nome ed email (anche senza premere +: chi hai scritto viene aggiunto al salvataggio). Scegli se mandare l'**invito** quando salvi, se avvisarle anche con il **promemoria** e se avvisarle quando **cambi o annulli**.

- **Tutti** ricevono l'email, con il file `.ics` allegato.
- **Chi usa Tacuìn** (stessa email) riceve anche la notifica. Nell'email: **«Aggiungi al mio Tacuìn»** e i pulsanti **Partecipo / Forse / Non partecipo**, che aprono il suo Calendario sull'invito. L'appuntamento entra nel suo calendario solo con il pulsante; se poi lo cambi o lo annulli, si aggiorna da solo anche lì.
- **Chi non usa Tacuìn** ha i pulsanti **Google Calendar**, **Outlook** e **Apple Calendario** (file .ics) e può rispondere da una pagina senza account; con «Partecipo» scarica anche il file per il suo calendario. Il link è personale; la risposta va confermata con un tocco, così i controlli antivirus delle email che «aprono» i link non rispondono al posto suo.
- Tu vedi **✓ partecipa, ? forse, ✕ no** accanto a ogni partecipante e ricevi un avviso a ogni risposta.

Serve l'invio email configurato (SMTP nel `.env`). Limiti contro l'abuso: 15 inviti all'ora e 30 al giorno per utente; l'admin può disattivare gli inviti di un account.

---

**Mittente delle email:** le email di sistema (registrazioni, promemoria, link per la password) partono da `SMTP_FROM` con il nome `SMTP_FROM_NAME` (predefinito «Tacuìn»). Gli inviti partono dallo stesso indirizzo ma come **«Mario Rossi via Tacuìn»**, con *Rispondi a* e organizzatore del calendario sull'email di chi invita: chi risponde scrive a lui. Il mittente non può essere direttamente l'email dell'utente: sarebbe una falsificazione e Gmail/Outlook la scarterebbero. `SMTP_FROM` deve essere di un dominio tuo verificato sul servizio email (es. `noreply@tuodominio.it`).

## Palestra

Nella sezione **Palestra** registri gli allenamenti:

- **Inizia allenamento** → scegli gli esercizi dall'elenco (oltre 45, divisi per petto, schiena, spalle, braccia, gambe, core, cardio e mobilità; puoi cercarli o crearne di tuoi).
- **Pesi**: per ogni serie scrivi kg e ripetizioni e tocca ✓. In grigio vedi i valori della volta scorsa: se lasci il campo vuoto, li usa lui. Dopo ogni serie parte il **recupero** (60, 90, 120 o 180 secondi, si cambia dal menu ⋯). Se l'ultima volta hai completato tutte le serie, ti suggerisce +2,5 kg.
- **Corpo libero** (piegamenti, trazioni…) e **a tempo** (plank) funzionano allo stesso modo, con ripetizioni o secondi.
- **Cardio** (tapis roulant, corsa, cyclette, vogatore…): durata, distanza e pendenza o livello. Calcola velocità, passo e calorie stimate.
- Quando superi il tuo miglior risultato su un esercizio compare **record!**.
- **Termina** salva l'allenamento e segna come fatta l'abitudine che si chiama «Allenamento» (o palestra, sport…). Puoi salvarlo come **scheda**, per ripeterlo con un tocco.

La pagina della Palestra mostra allenamenti della settimana rispetto all'obiettivo (si cambia toccandolo), volume sollevato, cardio, settimane di fila, il grafico dei **progressi** per esercizio (massimale stimato con la formula di Epley), gli ultimi allenamenti, le schede e i record personali. Un riepilogo è anche nella Home.

L'allenamento in corso resta salvato: puoi chiudere la pagina o cambiare dispositivo e riprenderlo.

### Corsi

Nella scheda **Corsi** aggiungi i corsi che frequenti (total body, arrampicata, boulder, spinning, yoga, pilates, crossfit, boxe, zumba…), con i giorni, l'ora, la durata e il posto: compaiono nel calendario come «corso in programma». Dopo la lezione tocca **Fatto**: segni quanto è stata faticosa (da 1 a 10), le note e, per l'arrampicata, le vie con il grado (chiusa o provata).

### Garmin

Due modi, entrambi gratuiti:
1. **Collegamento automatico** con la *Personal API* di Garmin: su [Garmin Connect → Personal API key](https://connect.garmin.com/app/settings/personalApiToken) crei la tua chiave (serve accettare le condizioni e scegliere la durata), la copi e la incolli in Palestra → Garmin Connect → Collega. Le attività degli ultimi 60 giorni arrivano subito, poi il server sincronizza da solo ogni 3 ore (o con «Aggiorna»). La chiave resta sul server e non viene mai mostrata nella pagina.
2. **Importa un file .FIT**: in Garmin Connect apri un'attività → ⚙ → «Esporta originale» e caricalo (va bene anche lo .zip).

Corse, bici, nuoto, vogatore, ellittica e camminate diventano attività di cardio con distanza e durata; pesi, arrampicata, yoga, pilates ecc. compaiono con durata, battito e calorie.

Quando Garmin è collegato, la sua sezione va **in cima alla Palestra** con:
- **passi di oggi** rispetto all'obiettivo, con i km;
- **battito degli ultimi 5 minuti** registrati (con l'ora), la linea dell'ultima ora, battito a riposo, minimo e massimo;
- **passi degli ultimi 7 giorni**, con l'obiettivo tratteggiato;
- **ultime 3 attività** dall'orologio (toccandole si apre il dettaglio).

I dati sono quelli dell'**ultima sincronizzazione dell'orologio** con l'app Garmin Connect. Aprendo la Palestra, passi e battito si aggiornano da soli se hanno più di 10 minuti (oppure con «Aggiorna»). Passi e riepilogo del battito di ogni giorno **si conservano per sempre**: al collegamento arrivano gli ultimi 60 giorni, poi i giorni nuovi (anche quelli persi se il Tracker è rimasto spento). Scollegando Garmin, passi e battito salvati vengono cancellati; le attività restano.

---

## Viaggi (✈️)

Nella sezione **✈️ Viaggi** crei un viaggio scegliendo la destinazione (ricerca della città) e le date. Per ogni viaggio hai:

- **🌤 Meteo**:
  - il tempo di adesso a destinazione;
  - le **previsioni giorno per giorno** per le date del viaggio, disponibili fino a 16 giorni prima;
  - per i viaggi più lontani, il **clima tipico** degli stessi giorni dell'anno precedente e la data da cui arriveranno le previsioni.
- **🗺 Mappa**: destinazione e **luoghi da visitare**. Li aggiungi cercandoli per nome o toccando la mappa, e li spunti quando li hai visitati.
- **🧳 Valigia e cose da fare**: una lista già pronta con le cose essenziali, da completare e spuntare.
- **💶 Spese del viaggio**: tutte le spese fatte nei giorni del viaggio, con un'aggiunta rapida nella categoria "Viaggi".
- **📝 Note**: voli, hotel, codici di prenotazione.

Il prossimo viaggio compare anche nella **Home**, con countdown e meteo. Se parte entro 3 giorni, lo trovi anche nel **riepilogo del mattino**.

Dall'assistente: `viaggi` (prossimi viaggi con il meteo), `meteo lisbona`, `meteo` (il prossimo viaggio), `che tempo fa a roma`.

Meteo e ricerca dei luoghi usano **Open-Meteo** (gratuito, senza chiave), la mappa usa **OpenStreetMap**. Serve la connessione a internet.

---

### Mappa

Le tessere della mappa (OpenStreetMap) le scarica il Tracker e le tiene in cache in `data/tiles` per 30 giorni: il browser chiede tutto al Tracker, quindi la mappa funziona anche da `localhost`, da un indirizzo IP o dietro un proxy. Per usare un altro fornitore di tessere imposta `TILE_URL` nel `.env` (es. `TILE_URL=https://tile.example.com/{z}/{x}/{y}.png`). La cartella `data/tiles` si può cancellare in qualsiasi momento.

## Scansiona scontrino (🧾)

Fotografa lo scontrino e l'app compila la spesa da sola: negozio, totale, data e categoria. La categoria la sceglie il modello che impara dalle tue spese. Funziona:
- dalla **web app**: pulsante **+** → *Scansiona scontrino*, oppure *Scansiona scontrino* in Spese, o la fotocamera nella chat. Scegli **«Scatta una foto»** o **«Carica dalla galleria»**; prima di salvare controlli e correggi i dati, oppure la trasformi in una spesa condivisa.

**Come funziona:** il servizio `vision` gira in un **container separato**:
1. OpenCV trova lo scontrino nella foto, lo raddrizza e ne migliora il contrasto;
2. Tesseract legge il testo in italiano;
3. delle regole pensate per gli scontrini italiani estraggono TOTALE (anche "TOTALE COMPLESSIVO" o "IMPORTO PAGATO", ignorando subtotali, sconti e resto), data e ora, P.IVA, negozio (riconosce le catene più comuni) e articoli.

Ogni campo ha un indice di sicurezza: quelli incerti sono segnati *da controllare*. **Le foto non vengono salvate.**

**Avviarlo:**
- **Con Docker** (cloud, o tutto in Docker): `docker compose up -d` avvia sia il tracker sia `vision`, già collegati (`VISION_URL=http://vision:8090` è il valore standard). `deploy.sh up` e `update.sh` avviano **prima** `vision`, aspettano che risponda e alla fine controllano che la lettura funzioni («Scontrini da foto: attivi»).
- **Su Windows con `Avvia Tracker.bat`**:
  1. installa **Docker Desktop**;
  2. nella cartella del progetto esegui `docker compose up -d vision`;
  3. nel `.env` c'è già `VISION_URL=http://localhost:8090` (valore standard): riavvia il tracker. `Avvia per test.bat` avvia `vision` da solo.

  Senza il servizio `vision` l'app funziona normalmente: semplicemente non compare *Scansiona scontrino*.

> Consigli per la foto: scontrino intero nell'inquadratura, su uno sfondo scuro, ben illuminato, senza pieghe sul totale.

---

### Impostazioni

In fondo al menu: **Cestino** e **Impostazioni**. In Impostazioni trovi:
- **Aspetto**: *Automatico* (segue il telefono/PC), *Chiaro*, *Scuro* o **Luce calda** (carta avorio, inchiostro marrone, niente blu: riposa la vista); con **«Luce calda la sera»** passa da solo alla luce calda negli orari scelti, anche sopra lo scuro. Più la **dimensione del testo**. La scelta vale su tutti i tuoi dispositivi.
- **Istruzioni**: tutta la guida in un posto, con la ricerca («come elimino un allenamento?»), i primi passi e una scheda per sezione; da qui riattivi i suggerimenti chiusi e rivedi il tour.
- **Notifiche e promemoria**, **Personalizza menu**, **Profilo**, **Esporta / Importa backup**.
- solo il root: **Messaggi dalla pagina di accesso** (chi riceve «Scrivi all'amministratore»).

Ogni sezione ha un pulsante **?** accanto al titolo con le istruzioni passo passo, un **suggerimento** in alto (si chiude per sempre con ✕) e, quando è vuota, una spiegazione con i pulsanti per iniziare ed esempi da toccare che riempiono l'assistente.

Tutte le modifiche (nuova colonna, opzioni di un elenco, categorie, rinomina, date del viaggio, obiettivo della palestra…) si fanno in **moduli dell'app**, mai con le finestre del browser: le opzioni di una colonna «Scelta da elenco» e le categorie di spesa si scrivono **una per riga con il tasto +**, con colore e ordine (trascinando). Rinominare un'opzione o una categoria aggiorna anche i dati già scritti.

### Privacy e condizioni d'uso

- **/privacy** (informativa, artt. 13-14 GDPR) e **/condizioni** (condizioni d'uso della versione di prova) sono pagine pubbliche, collegate dal piè di pagina, dalla pagina di accesso, dalle Istruzioni e dalle email d'invito. I testi sono in `legal.py`: sono una bozza scritta sul funzionamento reale dell'app, da far rivedere a un professionista.
- I **dati del titolare** (nome, indirizzo, email per la privacy) li inserisce il root in **Impostazioni → Dati del titolare**, oppure `PRIVACY_OWNER`, `PRIVACY_ADDRESS`, `PRIVACY_EMAIL` nel `.env`. Finché mancano, nelle pagine compare un segnaposto arancione.
- Chi chiede l'accesso conferma tre cose: informativa e 18 anni, condizioni d'uso, approvazione specifica delle clausole (artt. 1341-1342 c.c.). Data e versione vengono salvate. Quando i testi cambiano (`legal.VERSION`), a ogni utente viene chiesto di accettare di nuovo.
- **Garmin e file .FIT** richiedono il consenso esplicito ai dati sulla salute (art. 9 GDPR). «Scollega» revoca il consenso e cancella passi, battito e attività importate.
- Eliminando un account si cancellano anche gli inviti che aveva mandato. Gli inviti si cancellano comunque da soli 12 mesi dopo l'appuntamento (`INVITE_RETENTION_DAYS`).

### Versione di prova e contatto con l'amministratore

- In fondo a ogni pagina (e al login) il piè di pagina **«Versione di prova»** ricorda che l'app è in test e senza garanzie; **«Leggi la nota»** apre il testo completo. Chi chiede l'accesso deve confermare di averla letta. Il testo è una proposta di buon senso, non una consulenza legale.
- Nella pagina di accesso **«Scrivi all'amministratore»** apre un modulo (nome, email, messaggio). Il messaggio parte **dal server**: l'indirizzo dell'amministratore non compare mai nella pagina né nel codice. Arriva all'email scelta dal root in Impostazioni (o `CONTACT_EMAIL` nel `.env`, oppure l'email del root), con *Rispondi a* su chi ha scritto. Contro lo spam: un campo trappola per i bot e al massimo 5 messaggi l'ora per rete e per indirizzo.

## Assistente e Insight (🧠)

- **Aggiungi o chiedi**: è il modo più veloce per usare l'app (tutti i comandi sono nella tabella «Cosa scrivere all'assistente»).
  - **Telefono**: tocca il **+**. In cima scrivi quello che vuoi («12,50 pizza», «dentista venerdì alle 15», «fatto acqua», «quanto ho speso questo mese?»); sotto ci sono 6 scorciatoie (spesa, appuntamento, da fare, scontrino, allenamento, diario), alcune domande pronte e «Tutte le azioni».
  - **PC**: scrivi nella barra **«Scrivi o chiedi…»** in alto (o premi **Ctrl K**, **⌘ K** su Mac). La risposta si apre nel pannello a destra e resti nella pagina in cui sei: se registri una spesa mentre guardi Spese, la vedi subito nell'elenco.
  - Quello che scrivi viene eseguito subito; nella risposta trovi **Annulla** e, per le spese, **Cambia categoria**.
  - Tutta la conversazione è in **Cronologia** (dal pannello) o «Conversazione con l'assistente» (dal +).
- **🧠 L'assistente ha notato**, nella Home sotto «Oggi» e «Abitudini» (sul telefono una scheda alla volta, da scorrere); ogni scheda ha un'azione, per esempio «Dove spendo di più» o «Analizza»:
  - previsione delle uscite a fine mese;
  - categorie sopra la tua media;
  - spese insolite;
  - spese ricorrenti e abbonamenti;
  - costanza delle abitudini e giorni in cui le salti;
  - scadenze superate.

  Le stesse analisi le ottieni scrivendo `analisi`.

**Come "impara":**
- **Categorie**: un classificatore *Naive Bayes* addestrato sulle tue spese sceglie la categoria in automatico (es. "pizzeria da gino" → Svago) e ti dice su quante spese simili si basa e con che sicurezza. Se sbaglia, premi **🏷 Cambia categoria**: la correzione diventa un nuovo esempio e la volta dopo sceglie giusto.
- **Refusi**: "abitudnii", "progeti" o "spessa 12 pizza" vengono capiti lo stesso, grazie alla somiglianza tra parole.
- **Domande**: "quanto ho speso in svago a ottobre?", "quanto ho speso di benzina negli ultimi 90 giorni?", "quanto ho guadagnato quest'anno?". La risposta riporta il confronto con la tua media e un mini grafico degli ultimi 6 mesi.
- Tutto viene calcolato sul tuo server, con i tuoi dati, senza servizi esterni.

---

## Cosa scrivere all'assistente

| Messaggio | Cosa fa |
|---|---|
| `spesa 12,50 pizza` oppure `12,50 pizza` | Uscita (categoria scelta in automatico: pizza → Svago) |
| `ieri 40 benzina e 12 pranzo` | Più spese in un messaggio |
| `analisi` | Previsioni, anomalie, abbonamenti, abitudini |
| `quanto ho speso in svago a ottobre?` | Domande sulle tue spese (periodi: oggi, ieri, questa settimana, mese scorso, «a settembre», «ultimi 30 giorni», quest'anno…) |
| `-30 benzina` | Uscita veloce |
| `entrata 1500 stipendio` oppure `+1500 stipendio` | Entrata |
| `spesa 45 regalo #altro` | Sceglie la categoria (se non esiste la crea) |
| `spesa 20 benzina ieri` · `spesa 8 bar 28/10` | Con la data |
| `condivisa 60 cena con marco e giulia` | Spesa condivisa pagata da te, divisa in parti uguali (le persone nuove vengono aggiunte da sole) |
| `condivisa 60 cena` | Divisa con tutte le persone della lista |
| `condivisa 90 cena con tony 1/3` | A quote: Tony 1/3, il resto (2/3) è tuo. Vanno bene anche `40%`, `30€` o parti (`tony 2 e io 1`) |
| `condivisa 90 cena tony 1/3 denis 2/3` | Quote esplicite per tutti (il tuo nome si imposta in Spese → Condivise, cliccando su «Tu») |
| `marco ha pagato 40 benzina` · `condivisa 30 spesa pagato da giulia con marco` | Spesa pagata da un altro |
| `conti` · `/conti` | Chi ti deve e a chi devi, con pulsanti per saldare |
| `saldato marco` · `marco mi ha dato 20` · `ho dato 5 a giulia` | Registra un pagamento tra voi |
| `cancella ultima spesa` · `cancella pizza` · `cancella spesa di ieri da 12` | Elimina una spesa: chiede conferma o ti fa scegliere tra le spese trovate. `annulla` la ripristina |
| `viaggi` · `meteo lisbona` · `che tempo fa a roma` | Prossimi viaggi e previsioni del tempo |
| `fatto acqua` | Segna l'abitudine di oggi (basta una parte del nome) |
| `abitudini` · `/abitudini` | Lista con pulsanti da toccare |
| `abitudini ieri` | Segna quelle di ieri |
| `prezzo VWCE 110,5` | Aggiorna il prezzo di un investimento |
| `progetti` | Cosa c'è da fare nel tracker «Progetti», ordinato per scadenza, con i pulsanti ✓ per segnare le voci come fatte |
| `progetti: comprare vernice` | Aggiunge una voce al tracker «Progetti» |
| `tracker` · `/tracker` | Elenco di tutti i tuoi tracker con quante voci restano |
| `crea tracker giochi` | Crea un nuovo tracker con colonne adatte (giochi, libri, film/serie, viaggi, regali, ricette, spesa, palestra, idee…) |
| `crea tracker corsi: piattaforma, costo (numero), stato (da iniziare/in corso/completato), inizio (data)` | Nuovo tracker con le colonne che vuoi tu: tipo tra parentesi, opzioni separate da `/` |
| `elimina tracker giochi` | Elimina un tracker (chiede conferma; `annulla` lo recupera) |
| `da fare` · `/dafare` | Tutto quello che ti resta: abitudini di oggi + voci aperte di tutti i tracker |
| `oggi` · `mese` | Riepiloghi (`oggi` include anche le scadenze vicine) |
| `annulla` | Annulla l'ultima operazione fatta dall'assistente |

Le altre schede aperte (e gli altri dispositivi) si aggiornano da sole entro pochi secondi.

**Spese condivise.** In *Spese → 👥 Condivise* aggiungi le persone e registri le spese da dividere: chi ha pagato, tra chi dividere e come: **in parti uguali**, **a quote** (1/3 e 2/3, 40% e 60%, oppure parti 2:1) o con **importi esatti in €**. Chi lasci vuoto si divide il resto. Vedi sempre chi ti deve e a chi devi, e con **Salda** registri i rimborsi. La tua quota viene aggiunta automaticamente alle spese personali, nella categoria scelta, così il budget del mese resta corretto. Cliccando su una persona vedi solo le spese con lei.

**Come l'assistente capisce se una voce è "fatta":** nel tracker serve una colonna *Selezione* con un'opzione tipo **Fatto** / **Completato**, come la colonna «Stato» del tracker Progetti, oppure una colonna *Casella* (es. «Letto», «Fatto»). Se c'è una colonna *Data*, preferibilmente chiamata «Scadenza», le voci vengono ordinate per data e quelle in ritardo vengono evidenziate.

---

## Portare i dati dalla versione file (tracker.html)

1. Nel vecchio `tracker.html` premi **Esporta backup** (file `.json`).
2. Su http://localhost:8080 premi **Importa backup** e scegli quel file.

## Backup e log

- Tutti i dati di tutti gli utenti sono nel database `data\tracker.db` (SQLite).
- Ogni giorno viene salvata una copia in `data\backup\`, conservando gli ultimi 30 giorni.
- Ogni utente può anche esportare i propri dati dal **Profilo**.
- Il registro di cosa succede (avvio, notifiche, errori) è in `data\server.log`.
- Il **registro attività** (accessi e operazioni sugli account, con l'indirizzo IP) si conserva **12 mesi**: ogni ora le voci più vecchie vengono cancellate. Per cambiare la durata: `AUDIT_RETENTION_DAYS` nel `.env` (minimo 30).

## Problemi comuni

### Le notifiche non arrivano

1. **Profilo → Notifiche**: lo stato deve essere *Attive su questo dispositivo*. Se dice *Bloccate*, riattivale dalle impostazioni del browser o del telefono (Notifiche → consenti per Tacuìn).
2. **iPhone/iPad**: le notifiche funzionano solo con Tacuìn **aggiunto alla schermata Home** e aperto dall'icona (iOS 16.4+).
3. Serve **HTTPS** (in cloud con il dominio) oppure `localhost` sul PC: da `http://192.168.…` non funzionano.
4. Prova con **«Manda una notifica di prova»**; nel log cerca «Notifica non consegnata».
5. Sul server deve esserci la libreria `cryptography` (nel log all'avvio: «Notifiche push: attive»).

### Errore "CERTIFICATE_VERIFY_FAILED" / "self-signed certificate"

Riguarda le connessioni che il tracker fa verso internet (Garmin, meteo, mappe, notifiche). Vuol dire che un programma sul PC, quasi sempre **l'antivirus** (Avast, AVG, Kaspersky, ESET, Bitdefender…) oppure la **rete aziendale**, "ispeziona" le connessioni HTTPS con un proprio certificato.

Questa versione usa automaticamente **gli stessi certificati di Windows usati da Chrome ed Edge**, quindi nella maggior parte dei casi l'errore sparisce da solo. Serve Python 3.10 o superiore. Se dovesse restare:

1. **Più semplice:** nelle impostazioni dell'antivirus disattiva la *scansione HTTPS* (si chiama anche *scansione connessioni crittografate* o *Web Shield → HTTPS*), oppure aggiungi un'eccezione per Python.
2. **Oppure usa il certificato dell'antivirus:**
   1. Apri Chrome su `https://api.open-meteo.com`, clicca l'icona a sinistra dell'indirizzo → *La connessione è sicura* → *Il certificato è valido*.
   2. Nella scheda *Dettagli* seleziona il certificato **più in alto** nella gerarchia (avrà il nome dell'antivirus) → *Esporta* → formato *Base64 (.cer)*.
   3. Salvalo nella cartella del tracker come `antivirus.cer`.
   4. Nel `.env` aggiungi `SSL_CA_FILE=C:\Users\<tuo nome>\tracker-server\antivirus.cer` e riavvia il tracker.

### Altri problemi

| Problema | Soluzione |
|---|---|
| "Python 3 non trovato" | Reinstalla Python da python.org spuntando **Add python.exe to PATH** |
| Il file si chiama `.env.txt` | Il Blocco note ha aggiunto `.txt`: rinominalo in `.env`. In Esplora file attiva *Visualizza → Mostra → Estensioni nomi file* |
| "La porta 8080 è già in uso" | Il tracker è già avviato (forse in automatico): apri http://localhost:8080. Oppure cambia `PORT` nel `.env` |
| "Il tuo account è in attesa di approvazione" | Un admin deve approvarlo in 🛡 Utenti |
| La pagina dice "Impossibile contattare il server" | Il tracker non è avviato: apri `Avvia Tracker.bat` |

---

## GitHub

Non vengono mai caricati:
- il file `.env`, con token e password;
- la cartella `data`, con i tuoi dati, i backup e i log;
- i certificati (`*.cer`).

Tutto è escluso tramite `.gitignore`.

**Aggiornare GitHub dopo una modifica.** Con GitHub Desktop: scrivi una descrizione, poi *Commit to main* → *Push origin*. Da riga di comando:
```bat
git add -A
git commit -m "descrizione della modifica"
git push
```

**Immagine Docker automatica.** A ogni push su `main`, GitHub Actions controlla il codice e pubblica le immagini `ghcr.io/<utente>/<repository>` e `…-vision` (per questo progetto `ghcr.io/denzel18/tacuin`). L'avanzamento si vede nella scheda *Actions* del repository, e il pacchetto compare in *Packages*. Sul server cloud puoi usare direttamente quell'immagine: basta cambiare `docker-compose.yml` come indicato nel file stesso. Le immagini di questo progetto sono pubbliche. Se usi un tuo fork, imposta `TACUIN_IMAGE=ghcr.io/<tuo-utente>/<repository>` nel file `.env`; se il fork è privato, sul server serve prima `docker login ghcr.io` con un token GitHub che abbia il permesso `read:packages`.

---

## Cloud con Docker (quando vorrai)

Su un server Linux (una VPS) o anche su Windows con **Docker Desktop**:

```bash
cp .env.example .env        # su Windows: copy .env.example .env
# inserisci ROOT_EMAIL, ROOT_PASSWORD, SMTP_*, PUBLIC_URL e COOKIE_SECURE=1 nel .env
docker compose up -d --build
```

- La web app è sulla porta **8080** e i dati restano nella cartella `./data`.
- In Docker l'account root si crea da `.env` con **`ROOT_EMAIL` e `ROOT_PASSWORD`**: per sicurezza la creazione dalla pagina web funziona solo da `localhost`. Dopo il primo avvio puoi togliere la password dal `.env`.
- Con HTTPS imposta `COOKIE_SECURE=1`, e `TRUST_PROXY=1` se c'è un reverse proxy davanti.
- Per HTTPS con un dominio metti davanti un reverse proxy (Caddy, Traefik, nginx o Apache).
- **Non far girare insieme** la copia sul PC e quella in cloud: i promemoria partirebbero due volte. Esegui `Disattiva avvio automatico.bat`, copia `data\tracker.db` nella cartella `data` del server e avvia il container.
- Su Linux, se il container non riesce a scrivere in `./data`, esegui `sudo chown -R 1000:1000 data`.

### Pronto per la produzione: cartella `deploy/`

Contiene tutto per un server raggiungibile da remoto con HTTPS, usando le immagini già pubblicate da GitHub Actions (amd64 e arm64):

| File | A cosa serve |
|---|---|
| `docker-compose.yml` | Tracker + scontrini + **Caddy** (HTTPS automatico) oppure **Cloudflare Tunnel** (nessuna porta aperta) |
| `Caddyfile` | HTTPS con Let's Encrypt, HSTS, upload fino a 25 MB |
| `.env.example` | dominio, profilo (`https` o `tunnel`), email (Aruba o Brevo), scontrini, backup |
| `setup.sh` | prepara un Ubuntu/Debian nuovo: aggiornamenti automatici, firewall, swap, Docker, backup notturno |
| `backup.sh` | copia coerente del database + file, cifratura facoltativa, copia fuori dal server via rsync e/o su S3 |
| `aws-s3-backup-policy.json` | permesso AWS minimo per i backup su S3 (solo scrittura) |
| `restore.sh` | ripristina un backup (anche cifrato) |
| `update.sh` | backup, poi scarica l'ultima versione e riavvia |

In breve, su una VPS nuova:

```bash
scp -r deploy root@IP_DEL_SERVER:/root/        # dal tuo PC
ssh root@IP_DEL_SERVER
bash /root/deploy/setup.sh                     # una volta sola
nano /opt/tracker/.env                         # DOMAIN, ROOT_*, SMTP_*
cd /opt/tracker && docker compose up -d
```

Prima di avviare, crea nel DNS del dominio un record **A** `tracker` → IP del server (con il tunnel non serve: lo crea Cloudflare). Per spostare i dati dal PC copia `data\tracker.db` in `/opt/tracker/data/` e poi `chown -R 1000:1000 /opt/tracker/data`.

### Su Google Cloud (consigliato: Milano)

Tutto è automatico con `deploy/gcp/`: **Terraform** crea l'infrastruttura e `deploy.sh` prepara la VM e avvia Tacuìn.

**Architettura**: schema, componenti, flusso delle richieste e degli aggiornamenti e confini di sicurezza sono in [deploy/gcp/README.md](deploy/gcp/README.md).

Cosa viene creato: una VM **e2-small** (2 GB) a **Milano** (`europe-west8`) con Ubuntu 24.04 e IP statico; firewall con **solo 80/443 aperti a internet** (SSH solo tramite **IAP**, cioè con il tuo account Google: nessuna porta 22 esposta, nessuna chiave da custodire); snapshot giornalieri del disco (14 giorni); un bucket privato per i backup cifrati, su cui la VM può **solo scrivere**; i container non possono leggere il token della VM. Costo indicativo: circa **14 $/mese** di VM + **3,65 $** di IP + pochi centesimi di disco e backup (i nuovi account hanno 300 $ di credito per 90 giorni).

1. **Account e progetto**: [console.cloud.google.com](https://console.cloud.google.com) → crea un progetto (es. `tacuin`) e collega la fatturazione. Annota l'**ID del progetto**. Attiva la verifica in due passaggi sul tuo account Google.
2. Apri **Cloud Shell** (icona `>_` in alto a destra): ha già `gcloud`, `terraform` e `git`.
3. ```bash
   git clone https://github.com/Denzel18/tacuin.git && cd tacuin/deploy/gcp
   ./deploy.sh up       # la prima volta chiede l'ID del progetto e crea terraform.tfvars
   ```
   Lo script mostra il piano di Terraform (rispondi `yes`), poi chiede dominio, email e la casella email (Aruba o Brevo) e salva tutto in `deploy/gcp/app.env` (fuori da GitHub). **Salva nel gestore di password la passphrase dei backup** che ti mostra. Infine chiede un token GitHub con il solo permesso `read:packages`.
4. **DNS**: record **A** del tuo dominio → l'IP che lo script stampa alla fine. Dopo qualche minuto il sito è su `https://` con il certificato.
5. Da quel momento:

| Comando (in Cloud Shell, da `deploy/gcp`) | Cosa fa |
|---|---|
| `./deploy.sh update` | backup, scarica l'ultima versione e riavvia |
| `./deploy.sh ssh` | entra nella VM (o pulsante **SSH** in *Compute Engine → Istanze VM*) |
| `./deploy.sh logs` / `status` | log in tempo reale / stato |
| `./deploy.sh backup` | backup subito |
| `./deploy.sh stop` / `start` | spegne la VM (con backup prima) / la riaccende: da spenta paghi solo disco e IP |

**Accesso da remoto**: l'app si usa dal browser o dal telefono su `https://tuodominio` (meglio con la verifica in due passaggi attiva nel profilo). La VM invece si gestisce solo da Cloud Shell o dalla console, dopo il login Google. Per far entrare un'altra persona in SSH aggiungi la sua email in `admin_members` in `terraform.tfvars` e rilancia `./deploy.sh up`.

**Ripristino**: scarica l'archivio dal bucket (*Cloud Storage → Bucket → backup/*), copialo nella VM e usa `sudo /opt/tracker/restore.sh archivio`. Per un disastro completo c'è anche lo snapshot del disco (*Compute Engine → Snapshot*).

**Cancellare tutto**: in `terraform.tfvars` metti `deletion_protection = false`, `./deploy.sh up`, poi `terraform destroy`. Il bucket dei backup non si cancella se contiene file (scelta voluta).

### Su AWS (Lightsail)

La via più semplice su AWS è **Lightsail**: prezzo fisso, IP pubblico, disco e traffico inclusi. La cartella `deploy/` si usa così com'è.

1. **Account AWS**: attiva l'MFA sull'utente root e crea un avviso di spesa (*Billing → Budgets*, per esempio 20 $). Se scegli il *Free plan* (crediti iniziali), passa al *Paid plan* prima dei 6 mesi: alla scadenza il Free plan chiude l'account.
2. **Lightsail → Create instance**: regione **Frankfurt** (o Parigi: Milano non ha Lightsail) → *Linux/Unix* → *OS Only* → **Ubuntu 24.04 LTS** → piano **2 GB** (con la scansione scontrini; 1 GB basta senza). Scarica la chiave SSH.
3. **Networking** dell'istanza: *Create static IP* (gratis finché è collegato) e nel firewall IPv4 tieni SSH (22) e aggiungi **HTTP (80)** e **HTTPS (443)**. Attiva anche gli **snapshot automatici** giornalieri.
4. **DNS**: record **A** `tracker` → IP statico (anche con le *DNS zones* di Lightsail, gratuite).
5. **Backup su S3** (facoltativo ma consigliato):
   - S3 → crea un bucket nella stessa regione (resta privato: *Block all public access*);
   - IAM → utente `tracker-backup` senza accesso alla console, con una policy in linea uguale a `deploy/aws-s3-backup-policy.json` (sostituisci `NOME-DEL-BUCKET`): può solo **scrivere** i backup, non leggerli né cancellarli;
   - crea una *access key* per quell'utente e mettila in `BACKUP_S3_ACCESS_KEY` / `BACKUP_S3_SECRET_KEY`, con `BACKUP_S3_BUCKET` e `BACKUP_S3_REGION`. Imposta anche `BACKUP_PASSPHRASE`: gli archivi arrivano su S3 già cifrati.
6. **Server**:
   ```bash
   scp -i chiave-lightsail.pem -r deploy ubuntu@IP_STATICO:~
   ssh -i chiave-lightsail.pem ubuntu@IP_STATICO
   sudo bash ~/deploy/setup.sh
   sudo nano /opt/tracker/.env       # DOMAIN, ROOT_*, SMTP_*, BACKUP_*
   cd /opt/tracker && sudo docker compose up -d
   ```
7. **Email**: va bene Brevo (gratis). Se vuoi tutto su AWS usa **SES**: `SMTP_HOST=email-smtp.eu-central-1.amazonaws.com`, porta 587, con le *SMTP credentials* di SES; serve verificare il dominio e chiedere l'uscita dalla *sandbox*.

Ogni notte alle 03:30 il backup va in `/opt/tracker/backups` e, se configurato, su S3 in `tracker/backup/`. Per ripristinare: scarica l'archivio dal bucket (con la console) e usa `./restore.sh`.

## Comandi utili

```bat
py server.py                                 :: avvia dal Prompt dei comandi
curl http://localhost:8080/api/health        :: stato del server, delle notifiche e delle email
docker compose logs -f                       :: log in Docker
```

> Il progetto funziona anche su Mac e Linux: `python3 server.py`.

## Licenza

Tacuìn è distribuito con licenza **GNU Affero General Public License v3.0** (AGPL-3.0): puoi usarlo, studiarlo, modificarlo e ridistribuirlo. Se metti online una versione modificata, devi offrire ai suoi utenti il codice sorgente delle tue modifiche, con la stessa licenza: basta impostare `SOURCE_URL` nel file `.env` con il link al tuo fork (compare in fondo a ogni pagina, alla voce «Codice sorgente»). Testo completo in [LICENSE](LICENSE).

Logo e simbolo hanno una licenza a parte, in [brand/LICENSE.md](brand/LICENSE.md). Le librerie in `web/vendor` mantengono le loro licenze.

Per segnalare un problema di sicurezza leggi [SECURITY.md](SECURITY.md).
