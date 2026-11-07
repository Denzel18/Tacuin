"""
Testi legali di Tacuìn: informativa privacy (artt. 13-14 GDPR) e condizioni d'uso della versione di prova.

Pagine pubbliche /privacy e /condizioni. I dati del titolare (nome, indirizzo, email per la privacy) li sceglie
il root in Impostazioni oppure arrivano dal file .env (PRIVACY_OWNER, PRIVACY_ADDRESS, PRIVACY_EMAIL).
Quando cambia il testo in modo sostanziale si alza VERSION: agli utenti viene chiesto di accettare di nuovo.

Bozza scritta sul funzionamento reale dell'app: non sostituisce il parere di un professionista.
"""
import html

VERSION = "2026-11-04"
UPDATED = "3 novembre 2026"


def _e(x):
    return html.escape(str(x or ""))


def _ph(value, label):
    """Valore del titolare, oppure un segnaposto evidente se non è ancora stato compilato."""
    return _e(value) if value else f'<mark class="todo">[{_e(label)}]</mark>'


PRIVACY = """
<h1>Informativa sul trattamento dei dati personali</h1>
<p class="sub">ai sensi degli artt. 13 e 14 del Regolamento (UE) 2016/679 («GDPR») e del D.lgs. 196/2003 come modificato dal D.lgs. 101/2018 · Ultimo aggiornamento: {updated}</p>

<h2>1. Titolare del trattamento</h2>
<p>Il titolare del trattamento è <b>{owner}</b>, persona fisica, {address}, che gestisce il servizio Tacuìn raggiungibile all'indirizzo {site} (di seguito «Tacuìn» o il «Servizio»). Per ogni richiesta relativa ai dati personali: <b>{email}</b>, oppure il modulo «Scrivi all'amministratore» nella pagina di accesso. Il titolare non ha nominato un Responsabile della protezione dei dati (DPO), non essendovi tenuto ai sensi dell'art. 37 GDPR.</p>

<h2>2. Categorie di dati trattati</h2>
<table>
<tr><th>Categoria</th><th>Dati</th><th>Fonte</th></tr>
<tr><td>Dati dell'account</td><td>nome, nome utente pubblico (@), indirizzo email, come si è conosciuto il Servizio (facoltativo, visibile agli amministratori), ruolo, stato dell'account, data di creazione e ultimo accesso, accettazione dell'informativa e delle condizioni; la password è conservata solo in forma derivata non reversibile (PBKDF2-SHA256, 600.000 iterazioni); se attivata, la chiave della verifica in due passaggi e i codici di recupero (questi ultimi in forma derivata)</td><td>l'interessato</td></tr>
<tr><td>Dati di accesso e sicurezza</td><td>identificativo della sessione (conservato in forma derivata), indirizzo IP, tipo di browser e dispositivo, data e ora degli accessi e delle operazioni sull'account</td><td>generati dall'uso del Servizio</td></tr>
<tr><td>Contenuti inseriti</td><td>spese ed entrate, spese condivise e nomi delle persone con cui sono divise, investimenti, abitudini, pagine del diario e umore, appuntamenti, cose da fare, viaggi e luoghi, progetti, allenamenti e corsi</td><td>l'interessato</td></tr>
<tr><td>Dati relativi alla salute</td><td>allenamenti, attività, passi e frequenza cardiaca importati dall'account Garmin collegato o da file .FIT; eventuali informazioni sulla salute scritte dall'interessato nel diario o nelle note</td><td>l'interessato, Garmin su sua richiesta</td></tr>
<tr><td>Fotografie degli scontrini</td><td>immagine dello scontrino, da cui si ricavano esercente, data, importo e categoria</td><td>l'interessato</td></tr>
<tr><td>Dati di terzi inseriti dall'utente</td><td>nome e indirizzo email dei partecipanti invitati agli appuntamenti e loro eventuale risposta; nomi delle persone nelle spese condivise e, se l'utente le indica con il loro nome utente, l'avviso inviato loro (nome di chi condivide, descrizione, importo e quota)</td><td>l'utente che li inserisce</td></tr>
<tr><td>Notifiche</td><td>indirizzo tecnico fornito dal servizio di notifiche del browser, chiavi di cifratura e nome del dispositivo</td><td>il browser dell'interessato</td></tr>
<tr><td>Preferenze</td><td>aspetto, promemoria, ordine del menu</td><td>l'interessato</td></tr>
<tr><td>Messaggi all'amministratore</td><td>nome, indirizzo email e testo del messaggio inviato dalla pagina di accesso</td><td>il mittente</td></tr>
</table>
<p>Il Servizio non registra l'elenco delle pagine visitate né il contenuto dei messaggi scritti all'assistente nei registri tecnici. La cronologia della conversazione con l'assistente è conservata solo nel browser del dispositivo dell'interessato.</p>

<h2>3. Finalità e basi giuridiche</h2>
<table>
<tr><th>Finalità</th><th>Base giuridica</th></tr>
<tr><td>Creare e gestire l'account, fornire le funzioni del Servizio (registrazione dei contenuti, assistente, calendario, scansione degli scontrini, sincronizzazione tra dispositivi)</td><td>esecuzione del contratto di cui l'interessato è parte (art. 6, par. 1, lett. b)</td></tr>
<tr><td>Inviare email di servizio (approvazione dell'account, reimpostazione della password), promemoria e notifiche scelti dall'interessato</td><td>esecuzione del contratto (art. 6, par. 1, lett. b)</td></tr>
<tr><td>Importare e mostrare i dati relativi alla salute provenienti da Garmin o da file .FIT</td><td>consenso esplicito dell'interessato (art. 9, par. 2, lett. a), revocabile in ogni momento scollegando Garmin</td></tr>
<tr><td>Recapitare gli inviti agli appuntamenti e raccogliere le risposte dei partecipanti</td><td>legittimo interesse dell'utente che invita e del titolare a consentire l'organizzazione di appuntamenti (art. 6, par. 1, lett. f)</td></tr>
<tr><td>Garantire la sicurezza del Servizio, prevenire abusi e accessi non autorizzati, tenere il registro delle attività sugli account</td><td>legittimo interesse del titolare (art. 6, par. 1, lett. f)</td></tr>
<tr><td>Rispondere ai messaggi inviati all'amministratore</td><td>misure richieste dall'interessato (art. 6, par. 1, lett. b) o legittimo interesse (lett. f)</td></tr>
<tr><td>Adempiere obblighi di legge e difendere un diritto in sede giudiziaria</td><td>obbligo legale (art. 6, par. 1, lett. c) e legittimo interesse (lett. f)</td></tr>
</table>
<p>I dati non sono usati per pubblicità, non sono venduti né ceduti a terzi per finalità proprie e non sono oggetto di profilazione a fini commerciali. La scelta automatica della categoria di una spesa avviene sui soli dati dell'interessato, sul server del Servizio, e non produce decisioni con effetti giuridici ai sensi dell'art. 22 GDPR.</p>
<p>Il conferimento dei dati dell'account è necessario per usare il Servizio; tutti gli altri dati sono facoltativi e dipendono dalle funzioni che l'interessato sceglie di usare.</p>

<h2>4. Destinatari e responsabili del trattamento</h2>
<p><b>Nome utente e ricerca.</b> Il nome utente (@) scelto dall'interessato e il suo nome sono visibili agli altri utenti del Servizio, che possono cercarlo per nome utente per dividere con lui una spesa; l'indirizzo email non è mai mostrato. Con l'opzione «Non farti trovare» del Profilo l'interessato compare solo a chi scrive il suo nome utente esatto, e può bloccare gli avvisi di singoli utenti. Chi viene indicato in una spesa riceve soltanto una notifica: nei suoi dati non viene scritto nulla.</p>
<p>I dati sono trattati dal titolare. Gli amministratori del Servizio gestiscono gli account ma, per come il Servizio è costruito, non hanno accesso ai contenuti degli altri utenti. I dati possono essere trattati, per conto del titolare e sulla base di accordi ai sensi dell'art. 28 GDPR, da:</p>
<ul>
<li><b>Google Cloud</b>: infrastruttura su cui gira il Servizio, con server e copie di sicurezza nella regione di Milano (europe-west8);</li>
<li><b>Aruba S.p.A.</b>: invio delle email del Servizio.</li>
</ul>
<p>Altri soggetti ricevono dati in qualità di autonomi titolari, solo quando l'interessato usa la funzione corrispondente:</p>
<ul>
<li><b>servizi di notifica dei browser</b> (Apple, Google, Mozilla, Microsoft, secondo il dispositivo): recapitano le notifiche, il cui contenuto è cifrato in modo che possa leggerlo solo il dispositivo dell'interessato;</li>
<li><b>Garmin</b>: solo se l'interessato collega il proprio account Garmin;</li>
<li><b>Google, Microsoft o Apple</b>: solo se il partecipante a un invito sceglie di aggiungere l'appuntamento al proprio calendario su quei servizi.</li>
</ul>
<p>Per meteo e mappe il server interroga Open-Meteo e OpenStreetMap con le sole coordinate dei luoghi; per i prezzi di borsa, se attivi, interroga Yahoo Finance e OpenFIGI con i soli codici ISIN e simboli dei titoli. In nessun caso invia dati che identifichino l'interessato.</p>

<h2>5. Trasferimenti fuori dallo Spazio economico europeo</h2>
<p>Il Servizio e le copie di sicurezza risiedono in Italia. I servizi di notifica dei browser e i servizi di calendario scelti dal partecipante possono comportare un trasferimento verso Paesi terzi, in particolare gli Stati Uniti, sulla base della decisione di adeguatezza della Commissione europea del 10 luglio 2023 (EU-U.S. Data Privacy Framework) per le società aderenti, o delle clausole contrattuali tipo. Il contenuto delle notifiche viaggia cifrato.</p>

<h2>6. Periodo di conservazione</h2>
<table>
<tr><th>Dati</th><th>Conservazione</th></tr>
<tr><td>Account e contenuti</td><td>fino alla cancellazione dell'account da parte dell'interessato o del titolare; gli elementi eliminati restano nel Cestino per 30 giorni</td></tr>
<tr><td>Copie di sicurezza</td><td>copie giornaliere sul server per 30 giorni; copie cifrate in un archivio separato fino a 365 giorni; istantanee del disco per 14 giorni. Dopo la cancellazione dell'account i dati possono restare in queste copie fino alla loro scadenza, senza essere usati se non per il ripristino del Servizio</td></tr>
<tr><td>Sessioni di accesso</td><td>12 ore, oppure 30 giorni con «Resta collegato», o fino all'uscita</td></tr>
<tr><td>Registro delle attività sugli account</td><td>12 mesi, poi cancellazione automatica</td></tr>
<tr><td>Registri tecnici</td><td>a rotazione, entro circa 4 MB complessivi</td></tr>
<tr><td>Inviti e risposte</td><td>finché esiste l'account di chi invita, e comunque non oltre {inv_months} mesi dalla data dell'appuntamento, poi cancellazione automatica</td></tr>
<tr><td>Iscrizioni alle notifiche</td><td>finché l'interessato non le disattiva o il browser non le revoca</td></tr>
<tr><td>Messaggi all'amministratore</td><td>nella casella email del titolare per il tempo necessario a rispondere e comunque non oltre 24 mesi</td></tr>
</table>

<h2>7. Diritti dell'interessato</h2>
<p>L'interessato può esercitare in ogni momento i diritti di accesso, rettifica, cancellazione, limitazione, portabilità e opposizione (artt. 15-22 GDPR) e revocare il consenso, senza pregiudizio per la liceità del trattamento basata sul consenso prima della revoca. Molti diritti si esercitano direttamente nell'app: in <b>Profilo</b> l'interessato modifica nome ed email, <b>esporta tutti i propri dati</b> in formato JSON ed <b>elimina il proprio account</b>. Per il resto può scrivere a {email}; il titolare risponde entro un mese (art. 12, par. 3 GDPR).</p>
<p>L'interessato ha diritto di proporre reclamo al <b>Garante per la protezione dei dati personali</b> (<a href="https://www.garanteprivacy.it" target="_blank" rel="noopener">www.garanteprivacy.it</a>) o all'autorità di controllo dello Stato membro in cui risiede o lavora.</p>

<h2>8. Sicurezza</h2>
<p>Il titolare adotta misure tecniche e organizzative adeguate al rischio (art. 32 GDPR), tra cui: password conservate solo in forma derivata e verifica in due passaggi facoltativa; separazione dei dati tra utenti, senza accesso degli amministratori ai contenuti; connessione cifrata HTTPS e cookie protetti; notifiche cifrate per il singolo dispositivo; server in Italia con accesso amministrativo solo tramite identità verificata, firewall e aggiornamenti di sicurezza automatici; copie di sicurezza giornaliere e copie cifrate (AES-256) in un archivio separato; fotografie degli scontrini elaborate in memoria e non conservate; limiti contro gli abusi. Nessuna misura elimina del tutto il rischio: in caso di violazione dei dati personali il titolare la notifica al Garante entro 72 ore quando richiesto dall'art. 33 GDPR e la comunica agli interessati nei casi previsti dall'art. 34.</p>

<h2>9. Cookie e archiviazione locale</h2>
<p>Tacuìn usa <b>un solo cookie, tecnico</b>, necessario per mantenere l'accesso (12 ore, oppure 30 giorni con «Resta collegato»). Non usa cookie di profilazione, di statistica o di terze parti: per questo non è richiesto il consenso (art. 122 del Codice privacy e Linee guida del Garante del 10 giugno 2021). Nel browser restano inoltre le preferenze (aspetto, suggerimenti chiusi, ultima sezione aperta) e la cronologia della conversazione con l'assistente; un <i>service worker</i> serve solo a ricevere le notifiche. Su un dispositivo condiviso conviene uscire dall'account.</p>

<h2>10. Minori</h2>
<p>Il Servizio è riservato a persone che hanno compiuto <b>18 anni</b>. Il titolare non raccoglie consapevolmente dati di minori; se ne viene a conoscenza, li cancella.</p>

<h2>11. Modifiche</h2>
<p>La presente informativa può essere aggiornata. La versione in vigore è sempre disponibile a questo indirizzo con la data dell'ultimo aggiornamento; delle modifiche sostanziali gli utenti sono informati nell'app, dove viene chiesta una nuova presa visione.</p>
"""

TERMS = """
<h1>Condizioni d'uso della versione di prova</h1>
<p class="sub">Ultimo aggiornamento: {updated}</p>

<h2>1. Oggetto</h2>
<p>Le presenti condizioni regolano l'uso di Tacuìn, servizio offerto da <b>{owner}</b> (il «Gestore»), <b>a titolo gratuito e in versione di prova</b> («beta»). Richiedendo l'accesso, l'utente dichiara di aver letto e di accettare le presenti condizioni e di aver preso visione dell'<a href="/privacy">informativa sul trattamento dei dati personali</a>.</p>

<h2>2. Natura sperimentale del Servizio</h2>
<p>Il Servizio è in fase di sviluppo e collaudo. L'utente prende atto che può contenere errori e malfunzionamenti, che le sue funzioni possono essere modificate, sospese o rimosse in qualsiasi momento, anche senza preavviso, e che può essere temporaneamente non disponibile. L'accesso è concesso previa approvazione del Gestore e può essere revocato.</p>

<h2>3. Esclusione di garanzie</h2>
<p>Nei limiti massimi consentiti dalla legge applicabile, il Servizio è fornito <b>«così com'è» e «come disponibile»</b>, senza garanzie di alcun tipo, espresse o implicite, comprese quelle di continuità, assenza di errori, esattezza dei calcoli e delle informazioni mostrate (ad esempio totali, previsioni, dati letti dagli scontrini o importati da servizi terzi), puntualità o recapito di promemoria, notifiche ed email.</p>

<h2>4. Limitazione di responsabilità</h2>
<p>Nei limiti massimi consentiti dalla legge, il Gestore non risponde di danni diretti o indiretti derivanti dall'uso o dal mancato uso del Servizio, tra cui la perdita o l'alterazione di dati, il mancato o ritardato recapito di promemoria, avvisi o inviti, appuntamenti o scadenze mancati, decisioni prese sulla base delle informazioni mostrate.</p>
<p>Resta ferma la responsabilità del Gestore per <b>dolo o colpa grave</b> e negli altri casi in cui non può essere esclusa o limitata per legge (art. 1229 c.c.), nonché i diritti che la legge riconosce in modo inderogabile all'utente consumatore.</p>
<p>L'utente si impegna a non affidarsi al Servizio come unico strumento per scadenze o informazioni importanti (ad esempio visite mediche, pagamenti, documenti) e a conservare una propria copia dei dati rilevanti, anche tramite la funzione «Esporta i miei dati».</p>

<h2>5. Obblighi dell'utente</h2>
<p>L'utente si impegna a:</p>
<ol>
<li>fornire dati veritieri e mantenere riservate le proprie credenziali, rispondendo dell'uso del proprio account;</li>
<li>usare il Servizio per finalità personali e lecite, senza violare diritti di terzi;</li>
<li>inserire dati di altre persone (partecipanti agli appuntamenti, persone delle spese condivise) solo se ne è legittimato e, per gli inviti, solo verso persone che si aspettano di riceverli;</li>
<li>non tentare di accedere a dati o account altrui, aggirare le misure di sicurezza o i limiti del Servizio, sovraccaricarlo o usarlo per inviare comunicazioni indesiderate.</li>
</ol>

<h2>6. Inviti a terzi</h2>
<p>Gli inviti sono inviati dal Servizio per conto dell'utente, che ne è l'autore e ne risponde. Il Gestore può limitare o disattivare l'invio di inviti da un account in caso di abuso.</p>

<h2>7. Sospensione e cessazione</h2>
<p>L'utente può eliminare il proprio account in ogni momento dal Profilo. Il Gestore può sospendere o chiudere un account in caso di violazione delle presenti condizioni, e può interrompere il Servizio dandone, ove possibile, un preavviso ragionevole per consentire l'esportazione dei dati.</p>

<h2>8. Modifiche alle condizioni</h2>
<p>Le condizioni possono essere modificate; le modifiche sostanziali sono comunicate nell'app, dove viene chiesta una nuova accettazione, e si applicano dalla data indicata.</p>

<h2>9. Legge applicabile e foro competente</h2>
<p>Le presenti condizioni sono regolate dalla legge italiana. Per l'utente consumatore è competente il foro del luogo in cui ha la residenza o il domicilio (art. 66-bis del Codice del consumo).</p>

<h2>10. Approvazione specifica</h2>
<p>Ai sensi degli artt. 1341 e 1342 c.c., l'utente dichiara di approvare specificamente le clausole di cui ai punti 2 (natura sperimentale), 3 (esclusione di garanzie), 4 (limitazione di responsabilità) e 7 (sospensione e cessazione).</p>
<p>Contatti: {email}</p>
"""

PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Tacuìn</title><link rel="icon" href="/vendor/brand/favicon.svg" type="image/svg+xml"><style>
:root{{--bg:#f4f2ec;--card:#fffefb;--ink:#2a2a27;--sub:#66655e;--line:#dcd8cc;--acc:#c65a12}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--card:#1c1c1a;--ink:#f3f2ee;--sub:#a6a49b;--line:#33322e;--acc:#7cc6ae}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,Segoe UI,Helvetica,Arial,sans-serif}}
.w{{max-width:820px;margin:0 auto;padding:28px max(16px,env(safe-area-inset-left)) 60px}}
.top{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}}
.brand{{font-weight:300;font-size:22px;text-decoration:none;color:var(--ink)}}.brand span{{color:#c65a12}}
nav a{{color:var(--acc);font-weight:600;text-decoration:none;margin-left:14px}}nav a.on{{color:var(--ink)}}
.c{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:8px 26px 26px}}
h1{{font-size:28px;line-height:1.2;margin:22px 0 4px}}h2{{font-size:18px;margin:28px 0 8px}}.sub{{color:var(--sub);font-size:14px;margin:0 0 10px}}
p,li{{max-width:72ch}}a{{color:var(--acc)}}
table{{border-collapse:collapse;width:100%;font-size:14.5px;margin:6px 0}}th,td{{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}}
th{{font-size:12.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--sub)}}
.tw{{overflow-x:auto}}mark.todo{{background:#f6e6da;color:#a2552b;border-radius:4px;padding:0 4px}}
.beta{{margin:22px 0 0;font-size:13px;color:var(--sub)}}
</style></head><body><div class="w">
<div class="top"><a class="brand" href="/">tacu<span>ì</span>n</a><nav><a href="/privacy" class="{p_on}">Privacy</a><a href="/condizioni" class="{t_on}">Condizioni d'uso</a><a href="/">Apri Tacuìn</a></nav></div>
<div class="c">{body}</div>
<p class="beta">Versione di prova · documento versione {version}</p>
</div></body></html>"""


def page(kind, owner="", address="", email="", site="", inv_days=365):
    vals = {"owner": _ph(owner, "Nome e cognome del titolare"), "address": _ph(address, "indirizzo"),
            "email": f'<a href="mailto:{_e(email)}">{_e(email)}</a>' if email else _ph("", "email per la privacy"),
            "site": _e(site), "updated": UPDATED, "inv_months": max(1, round(inv_days / 30.4))}
    body = (PRIVACY if kind == "privacy" else TERMS).format(**vals).replace("<table>", '<div class="tw"><table>').replace("</table>", "</table></div>")
    return PAGE.format(title="Informativa privacy" if kind == "privacy" else "Condizioni d'uso", body=body, version=VERSION,
                       p_on="on" if kind == "privacy" else "", t_on="on" if kind != "privacy" else "")
