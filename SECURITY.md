# Sicurezza

## Segnalare una vulnerabilità

Se trovi un problema di sicurezza **non aprire una issue pubblica**. Usa la segnalazione privata di GitHub: scheda **Security** del repository → **Report a vulnerability**.

Scrivi cosa hai trovato, come riprodurlo e quali dati o installazioni potrebbe toccare. Rispondo appena possibile; una volta pubblicata la correzione, la segnalazione può diventare pubblica, con il tuo nome se lo desideri.

## Versioni coperte

Solo l'ultima versione sul ramo `main`. Chi ha installato Tacuìn aggiorna con `./update.sh` (server con Docker) o `./deploy.sh update` (Google Cloud).

## Cosa fa già Tacuìn

- Password con PBKDF2 (600.000 iterazioni), verifica in due passaggi facoltativa, blocco dopo tentativi sbagliati.
- Cookie di sessione `HttpOnly`, `SameSite=Lax` e `Secure` dietro HTTPS; controllo dell'origine su ogni richiesta che modifica dati.
- Content Security Policy con *nonce*: nella pagina parte solo il codice di quella risposta.
- Ogni utente vede solo i propri dati; root e admin gestiscono gli account ma non leggono spese, diario o abitudini degli altri.
- Container in sola lettura, senza privilegi, con utente non root; backup cifrati in un bucket dove il server può solo scrivere.
- Scudo contro raffiche di richieste e scanner: limite per indirizzo IP, blocco temporaneo automatico (15 minuti, 6 ore, 24 ore), registro e avviso al root; tetto alle connessioni contemporanee; tempi massimi in Caddy contro le connessioni lente.
- Controlli automatici su GitHub a ogni modifica e ogni settimana: Bandit (codice), pip-audit (librerie), Trivy (immagine Docker), Dependabot (aggiornamenti di sicurezza).

Chi installa Tacuìn è responsabile della propria installazione: aggiornamenti, backup, password del root e configurazione del server.
