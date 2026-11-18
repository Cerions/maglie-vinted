# Vinted Maglie

Controlla le ricerche Vinted ogni 60-120 secondi. Quando esce una maglia nuova ti avvisa
subito su Telegram, poi cerca la foto dell'etichetta interna, legge il codice articolo e il
codice a barre, verifica online a quale maglia corrispondono e ti risponde con l'esito.
Le maglie che superano il controllo finiscono in una lista.

> ⚠️ "Etichetta OK" vuol dire che il codice è coerente con la maglia dell'annuncio, **non** che
> la maglia è originale: i falsi possono avere codici veri. È un filtro, non una certificazione.

## Come funziona

```
scanner (ogni 60-120 s, jitter)          analizzatore (in coda, nel DB)
  ricerca Vinted ──► annuncio nuovo? ──►  pagina annuncio ──► foto
                         │                      │
                         ▼                      ▼
                  🆕 avviso Telegram     Claude: trova etichetta, legge codici
                                                │  (codice validato: formato + cifra di controllo UPC)
                                                ▼
                                         Claude + ricerca web: a che maglia corrisponde il codice?
                                                │  (risultato in cache: stesso codice = niente nuova ricerca)
                                                ▼
                                         confronto con l'annuncio ──► risposta all'avviso ✅ ⚠️ ❔ ➖
```

- **Primo giro di ogni ricerca:** gli annunci già presenti vengono segnati come visti, senza avvisi.
- **Annunci vecchi:** quelli ripubblicati o promossi ricompaiono tra i "più recenti" ma hanno un ID molto più
  basso dell'ultimo visto: vengono ignorati (`margine_id_vecchi`). Se lo script resta fermo più di 30 minuti,
  al giro dopo quello che è uscito nel frattempo viene segnato come visto senza avvisi (`eta_massima_annuncio_minuti`).
- **Confronto con le foto:** il verdetto si basa sulle foto della maglia, non sul testo del venditore, che
  sbaglia spesso il nome del kit (terza/quarta, home/away). Gli errori del testo vengono segnalati con 📝.
- **Backoff:** al primo 403/429 lo scanner butta via la sessione e aspetta 5 minuti, poi 10, 20, 40, fino a 60.
  Dopo 3 errori di fila ti arriva un messaggio 🔴 su Telegram, e uno 🟢 quando Vinted torna a rispondere.
- Nessun login: lo script usa una sessione anonima, il tuo account Vinted non viene toccato.

## Installazione (Windows, PowerShell)

Serve Python 3.11 o più recente.

```powershell
cd vinted-maglie
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy config.example.toml config.toml
copy .env.example .env
```

Usa `python -m pip` e non `pip`: con il **Controllo intelligente delle app** di Windows attivo, `pip.exe`
nel venv viene bloccato perché non è firmato. Per lo stesso motivo il progetto evita le librerie con
moduli compilati: anche la chiamata a Claude è fatta con httpx, senza l'SDK `anthropic`.

### Chiave Anthropic
Creala nella Console Anthropic (platform.claude.com → Settings → API keys → Create key → "Somewhere else")
e mettila in `.env` come `ANTHROPIC_API_KEY`. Serve anche credito prepagato (Billing).

La verifica del codice usa la ricerca web di Claude: controlla che in Settings → Capabilities
"Allow web search" sia attivo (di solito lo è già), altrimenti le analisi falliscono.

### Bot Telegram
1. Su Telegram scrivi a **@BotFather** → `/newbot` → copia il token in `.env` (`TELEGRAM_BOT_TOKEN`).
2. Manda un messaggio qualsiasi al tuo bot.
3. `python main.py telegram-chat-id` → copia il numero in `.env` (`TELEGRAM_CHAT_ID`).
4. `python main.py telegram-prova` → ti arriva un messaggio di prova.

Senza Telegram lo script funziona lo stesso: gli avvisi finiscono solo nel log.

## Ricerche

Su vinted.it imposta i filtri dal browser (categoria, marca, taglia, prezzo), poi copia l'URL in
`config.toml`:

```toml
[[ricerche]]
nome = "Nike calcio"
url = "https://www.vinted.it/catalog?search_text=maglia%20calcio&brand_ids[]=53&order=newest_first"
```

Oppure aggiungile all'avvio, anche più di una:

```powershell
python main.py avvia --ricerca "maglia inter vintage" --ricerca "https://www.vinted.it/catalog?..."
```

Ogni ricerca costa una richiesta a Vinted a ogni giro: meglio poche ricerche ben filtrate che tante.

## Primo avvio: prova in quest'ordine

```powershell
python main.py prova-ricerca                        # una ricerca: vedi se Vinted risponde e se i filtri funzionano
python main.py prova-annuncio https://www.vinted.it/items/...   # le foto trovate: aprile e verifica che siano di QUELL'annuncio
python main.py prova-annuncio https://www.vinted.it/items/... --ai   # analisi completa di un annuncio con l'etichetta
python main.py avvia                                # si parte (Ctrl+C per fermare)
```

`prova-annuncio` è importante: la lettura delle foto dalla pagina dell'annuncio non l'ho potuta
provare sul sito vero. Se in output vedi foto di altri annunci, o nessuna foto, la funzione da
sistemare è `estrai_foto_da_pagina` in `maglie/vinted.py`.

Con `-v` (`python main.py -v avvia`) il log diventa dettagliato, comprese le richieste HTTP.

## La lista

```powershell
python main.py lista                    # maglie con etichetta OK
python main.py lista --tutti            # anche dubbie e negative
python main.py lista --csv maglie.csv   # esporta (si apre con Excel), con il costo di ogni analisi
```

## Costi

Ogni analisi scrive nel log quanto è costata e il totale del giorno:

```
14:32:10 INFO    analizzatore Esito 4567: corrisponde CV8413-101 · 21.840 in / 612 out · 2 ricerche web · $0.0225 (oggi $0.3140) · ...
```

```powershell
python main.py costi              # spesa per giorno, ultimi 7 giorni
python main.py costi --giorni 30
```

Vengono contati anche i tentativi falliti, perché i token si pagano lo stesso. Il costo è calcolato
con i prezzi in `config.toml` (default: Haiku 5.5). È una stima: il dato ufficiale è nella Console Anthropic.
La ricerca su Vinted non costa nulla: si paga solo l'analisi degli annunci nuovi.

## Originalità senza codice e prezzo

La prima chiamata a Claude (quella che legge l'etichetta, senza ricerche web, ~0,1 centesimi) fa anche:
- **tipo di articolo:** felpe, maglie da allenamento ecc. vengono scartate subito (`solo_maglie_da_calcio`);
- **identificazione** della maglia dalla grafica (squadra, stagione, kit, versione);
- **valutazione dell'originalità dalle foto:** stemma e sponsor, font, etichette interne, cuciture. Si usa quando
  non c'è un codice utilizzabile (niente foto dell'etichetta, codice illeggibile o non trovato online);
- **stima del prezzo** tipico da usata.

Il **confronto di prezzo** con una ricerca web (eBay, Vinted, negozi vintage) parte solo per le maglie con esito
✅ o 🟢 e, con `controllo_prezzo = "occasioni"`, solo se la stima dice che l'annuncio costa meno della media. I prezzi trovati restano in cache per `giorni_cache` giorni. Se la maglia ha un codice valido, i prezzi
arrivano già con la ricerca del codice, senza costi in più.

> La valutazione dalle foto è un indizio, non una perizia: un buon falso in foto piccole può passare.

### La guida r/KitSwap

La valutazione segue la [guida di r/KitSwap per riconoscere i falsi](https://www.reddit.com/r/KitSwap/wiki/spottingfakes/),
riassunta in `maglie/guida.py`: regole generali più una scheda per marca (adidas, Nike, Puma, Lotto, Macron, Jako,
New Balance, Hummel) e consigli per le maglie vecchie. A Claude va solo la scheda della marca dell'annuncio
(~600 token in più a chiamata). Dalle foto vengono letti anche il codice del cartellino, la coerenza delle taglie
tra le etichette e il contatore di produzione (Nike/Puma); da questi partono controlli automatici:

- codice del cartellino diverso da quello dell'etichetta interna → mai esito positivo;
- taglia diversa tra le etichette → mai esito positivo;
- contatore di produzione sotto 1.000.000 (Nike/Puma) → indizio di falso, segnalato con ✖️.

Le foto di esempio della guida (vero vs falso, su imgur) sono elencate in `ESEMPI` dentro `maglie/guida.py`
per consultarle a mano. La guida è del 2018 e non ha ancora la sezione Umbro.

### Spendere meno

- La spesa vera sono le **ricerche web** (1 centesimo l'una più i token delle pagine lette): la prima chiamata
  senza ricerche costa circa 0,1 centesimi. `ricerche_web_per_codice` e `ricerche_web_per_prezzi` le limitano.
- `parole_escluse`: annunci con quelle parole nel titolo (felpa, tuta, ...) vengono scartati gratis, senza avviso.
- I filtri di Vinted (categoria, prezzo massimo, taglia) costano zero: meno annunci nuovi = meno analisi.
- `budget_giornaliero_usd` è il tetto di sicurezza.

## Esiti

| Esito | Significato |
|---|---|
| ✅ Etichetta OK | Il codice esiste ed è coerente con squadra, stagione, kit e taglia → entra nella lista |
| ⚠️ Codice di un'altra maglia | Il codice appartiene a un'altra squadra, stagione o kit: segnale forte di falso |
| ⚠️ Codice in formato anomalo | Formato che non torna per quella marca (o lettura sbagliata) |
| ❔ Da verificare a mano | Dati insufficienti, oppure codice a barre o taglia incoerenti |
| ❔ Codice non trovato online | Nessuna fonte: può essere un codice raro come un codice inventato |
| ❔ Codice illeggibile | L'etichetta c'è ma la foto non permette di leggerla |
| 🟢 Probabile originale | Senza codice, ma le foto non mostrano indizi di falso → entra nella lista |
| ❔ Originalità dubbia | Senza codice, le foto non bastano o qualcosa non torna |
| ⚠️ Probabile falso | Indizi concreti di falso nelle foto |
| ➖ Nessuna foto dell'etichetta | Nessun codice e foto non valutabili |
| ➖ Non è una maglia da gara | Felpa, allenamento, altro: scartata subito |

Se il prezzo è sotto quello tipico, il messaggio inizia con **🔥 Costa meno del solito** e riporta il confronto
(es. "💰 25 € · di solito 40–60 € usata → circa il 50% in meno").

## Note pratiche

- **Il PC non deve andare in sospensione** (Impostazioni → Sistema → Alimentazione), altrimenti lo script si ferma.
- **Costi:** ogni annuncio nuovo costa una chiamata con le foto, una chiamata di confronto e, se il codice non è in
  cache, una ricerca web. `max_analisi_al_giorno` fa da tetto, `max_foto` riduce il costo per annuncio.
- Lo scraping va contro i termini di Vinted: tienilo a uso personale e non abbassare troppo l'intervallo.
- I dati stanno in `maglie.db` (SQLite) e i log in `logs/`: se chiudi e riapri lo script, gli annunci in coda
  vengono ripresi e quelli già visti non vengono avvisati di nuovo.

## Struttura

```
main.py              comandi (avvia, prova-ricerca, prova-annuncio, lista, costi, telegram-chat-id, telegram-prova)
maglie/config.py     config.toml + .env
maglie/vinted.py     tutto ciò che tocca Vinted (ricerca, pagina annuncio, foto)
maglie/servizio.py   scanner e analizzatore (thread), jitter e backoff
maglie/analisi.py    flusso di analisi di un annuncio + testi dei messaggi
maglie/ai.py         le tre chiamate a Claude (etichetta, ricerca codice, confronto)
maglie/codici.py     validazione codici articolo e UPC
maglie/guida.py      checklist anti-falsi per marca (dalla guida r/KitSwap)
maglie/db.py         SQLite
maglie/telegram.py   Bot API
tests/               test con Vinted, Claude e Telegram finti (pytest)
```

Test: `pip install -r requirements-dev.txt` e poi `pytest`.
