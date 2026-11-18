"""Vinted Maglie — avvisi per maglie da calcio con etichetta verificata.

Comandi:
  python main.py avvia [--ricerca URL_O_TESTO ...]
  python main.py prova-ricerca [--ricerca URL_O_TESTO]
  python main.py prova-annuncio URL_O_ID [--ai]
  python main.py telegram-chat-id
  python main.py lista [--tutti] [--csv FILE]
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from maglie.config import Config, Ricerca, carica_config
from maglie.db import Archivio
from maglie.telegram import Telegram
from maglie.vinted import ClientVinted, VintedErrore, estrai_foto_da_pagina, parametri_da_ricerca

log = logging.getLogger("maglie")


def configura_log(cfg: Config, verboso: bool = False) -> None:
    cfg.cartella_log.mkdir(exist_ok=True)
    formato = logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)-12s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formato)
    file = RotatingFileHandler(cfg.cartella_log / "maglie.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)-12s %(name)s: %(message)s"))
    radice = logging.getLogger()
    radice.setLevel(logging.DEBUG if verboso else logging.INFO)
    radice.addHandler(console)
    radice.addHandler(file)
    for rumoroso in ("httpx", "httpcore", "anthropic", "vinted_scraper"):
        logging.getLogger(rumoroso).setLevel(logging.DEBUG if verboso else logging.WARNING)


def aggiungi_ricerche(cfg: Config, extra: list[str] | None) -> None:
    for r in extra or []:
        nome = r if not r.startswith("http") else f"ricerca {len(cfg.ricerche) + 1}"
        cfg.ricerche.append(Ricerca(nome=nome, url=r))


# --- comandi ----------------------------------------------------------------

def cmd_avvia(cfg: Config, args) -> int:
    from maglie.ai import AnalistaAI
    from maglie.servizio import Analizzatore, Scanner

    aggiungi_ricerche(cfg, args.ricerca)
    if not cfg.ricerche:
        print("Nessuna ricerca: aggiungila in config.toml o con --ricerca \"URL o testo\"")
        return 1
    ai = AnalistaAI(cfg.anthropic_api_key, cfg.analisi.modello, cfg.analisi.ricerche_web_per_codice)
    telegram = Telegram(cfg.telegram.bot_token, cfg.telegram.chat_id)
    if not telegram.attivo:
        log.warning("Telegram non configurato: gli avvisi finiscono solo nel log")
    client = ClientVinted(cfg.vinted.dominio)
    stop, sveglia = threading.Event(), threading.Event()

    scanner = Scanner(cfg, client, telegram, stop, sveglia)
    analizzatore = Analizzatore(cfg, client, ai, telegram, stop, sveglia)
    thread = [
        threading.Thread(target=scanner.esegui, name="scanner", daemon=True),
        threading.Thread(target=analizzatore.esegui, name="analizzatore", daemon=True),
    ]
    for r in cfg.ricerche:
        log.info("Ricerca attiva: %s -> %s", r.nome, parametri_da_ricerca(r.url))
    log.info("Intervallo %d-%d s. Ctrl+C per fermare.", cfg.vinted.intervallo_min_secondi, cfg.vinted.intervallo_max_secondi)
    for t in thread:
        t.start()
    try:
        while all(t.is_alive() for t in thread):
            time.sleep(1)
        log.error("Un thread si è fermato da solo, chiudo. Guarda logs/maglie.log")
        return 1
    except KeyboardInterrupt:
        log.info("Chiusura in corso…")
        stop.set()
        sveglia.set()
        for t in thread:
            t.join(timeout=15)
        return 0


def cmd_prova_ricerca(cfg: Config, args) -> int:
    from maglie.servizio import eta_minuti

    aggiungi_ricerche(cfg, args.ricerca)
    if not cfg.ricerche:
        print("Nessuna ricerca da provare")
        return 1
    client = ClientVinted(cfg.vinted.dominio)
    for i, r in enumerate(cfg.ricerche):
        params = parametri_da_ricerca(r.url, cfg.vinted.risultati_per_ricerca)
        print(f"\n== {r.nome}\nparametri: {params}")
        try:
            annunci = client.cerca(params)
        except VintedErrore as ex:
            print(f"ERRORE: {ex}")
            return 1
        for a in annunci[:15]:
            eta = eta_minuti(a)
            eta_txt = f"{eta:6.0f} min fa" if eta is not None else "   età ?   "
            print(f"  {a['id']:>12}  {eta_txt}  {a['prezzo']:>10}  {a['taglia']:>5}  {a['marca'][:12]:12}  {a['titolo'][:50]}")
        print(f"  ({len(annunci)} risultati)")
        if i < len(cfg.ricerche) - 1:
            time.sleep(5)
    return 0


def _id_da_url(testo: str) -> int:
    m = re.search(r"/items/(\d+)", testo) or re.fullmatch(r"\s*(\d+)\s*", testo)
    if not m:
        raise SystemExit(f"Non trovo l'ID dell'annuncio in: {testo}")
    return int(m.group(1))


def cmd_prova_annuncio(cfg: Config, args) -> int:
    client = ClientVinted(cfg.vinted.dominio)
    id_annuncio = _id_da_url(args.annuncio)
    try:
        pagina = client.pagina_annuncio(id_annuncio)
    except VintedErrore as ex:
        print(f"ERRORE: {ex}")
        return 1
    foto = estrai_foto_da_pagina(pagina, cfg.analisi.max_foto)
    print(f"Foto trovate ({len(foto)}) — aprile e controlla che siano tutte di QUESTO annuncio:")
    for u in foto:
        print("  ", u)
    if not args.ai:
        return 0

    from maglie.ai import AnalistaAI
    from maglie.analisi import analizza_annuncio, raccogli_foto

    ai = AnalistaAI(cfg.anthropic_api_key, cfg.analisi.modello, cfg.analisi.ricerche_web_per_codice)
    titolo = re.search(r'property="og:title"[^>]+content="([^"]*)"', pagina)
    annuncio = {"id": id_annuncio, "titolo": titolo.group(1) if titolo else "", "marca": "", "taglia": "",
                "url": f"{cfg.vinted.dominio}/items/{id_annuncio}", "foto_principale": ""}
    archivio = Archivio(cfg.percorso_db)
    immagini = raccogli_foto(client, annuncio, cfg.analisi.max_foto)
    r = analizza_annuncio(annuncio, immagini, ai, archivio)
    print(f"\nLettura etichetta: {r.lettura}")
    print(f"Prodotto: {r.prodotto}")
    print(f"\nESITO: {r.esito}\n{r.motivo}")
    return 0


def cmd_telegram_chat_id(cfg: Config, args) -> int:
    if not cfg.telegram.bot_token:
        print("Metti prima TELEGRAM_BOT_TOKEN nel file .env")
        return 1
    chat = Telegram(cfg.telegram.bot_token, "x").chat_recenti()
    if not chat:
        print("Nessun messaggio trovato: scrivi qualcosa al tuo bot su Telegram e rilancia il comando.")
        return 1
    for c in chat:
        nome = c.get("title") or c.get("username") or c.get("first_name")
        print(f"chat_id = {c['id']}   ({nome})")
    print("\nCopia il chat_id in .env come TELEGRAM_CHAT_ID")
    return 0


def cmd_lista(cfg: Config, args) -> int:
    archivio = Archivio(cfg.percorso_db)
    esiti = ("corrisponde", "incerto", "non_corrisponde", "codice_non_trovato", "codice_anomalo") if args.tutti else ("corrisponde",)
    righe = archivio.lista(esiti)
    colonne = ["analizzato_il", "esito", "titolo", "prezzo", "taglia", "codice_stile", "upc", "prodotto", "url", "motivo"]
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: Excel lo apre bene
            w = csv.writer(f, delimiter=";")
            w.writerow(colonne)
            for r in righe:
                w.writerow([r[c] for c in colonne])
        print(f"{len(righe)} righe salvate in {args.csv}")
    else:
        for r in righe:
            print(f"[{r['esito']}] {r['titolo']} · {r['prezzo']} · {r['codice_stile']} · {r['url']}")
        print(f"({len(righe)} maglie)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Avvisi Vinted per maglie con etichetta verificata")
    p.add_argument("--config", default="config.toml")
    p.add_argument("-v", "--verboso", action="store_true")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("avvia", help="Avvia scanner + analizzatore")
    s.add_argument("--ricerca", action="append", help="URL di ricerca Vinted o testo (ripetibile)")
    s = sub.add_parser("prova-ricerca", help="Una sola ricerca, stampa i risultati")
    s.add_argument("--ricerca", action="append")
    s = sub.add_parser("prova-annuncio", help="Mostra le foto trovate in un annuncio")
    s.add_argument("annuncio", help="URL o ID dell'annuncio")
    s.add_argument("--ai", action="store_true", help="Esegue anche l'analisi completa (costa una chiamata API)")
    sub.add_parser("telegram-chat-id", help="Trova il chat_id da mettere nel .env")
    s = sub.add_parser("lista", help="Mostra le maglie con etichetta OK")
    s.add_argument("--tutti", action="store_true", help="Include anche gli esiti dubbi e negativi")
    s.add_argument("--csv", help="Esporta in CSV")

    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # console Windows: niente crash su emoji/accenti
        sys.stdout.reconfigure(errors="replace")
    cfg = carica_config(args.config)
    configura_log(cfg, args.verboso)
    comandi = {
        "avvia": cmd_avvia, "prova-ricerca": cmd_prova_ricerca, "prova-annuncio": cmd_prova_annuncio,
        "telegram-chat-id": cmd_telegram_chat_id, "lista": cmd_lista,
    }
    return comandi[args.comando](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
