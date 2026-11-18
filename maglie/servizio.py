"""I due processi che girano in parallelo.

- Scanner: interroga le ricerche ogni 60-120 s (jitter), con backoff se Vinted blocca.
  Appena vede un annuncio nuovo manda l'avviso immediato e lo mette in coda.
- Analizzatore: prende gli annunci dalla coda (nel DB, quindi sopravvive ai riavvii),
  li analizza e risponde all'avviso con l'esito.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timezone

from .analisi import OpzioniAnalisi, analizza_annuncio, messaggio_esito, messaggio_nuovo, raccogli_foto
from .config import Config, Ricerca
from .db import Archivio
from .telegram import Telegram, e
from .vinted import ClientVinted, VintedErrore, parametri_da_ricerca

log = logging.getLogger(__name__)

MAX_TENTATIVI_ANALISI = 3


def eta_minuti(annuncio: dict, ora: float | None = None) -> float | None:
    ts = annuncio.get("timestamp")
    if not ts:
        return None
    ts = float(ts)
    if ts > 1e12:  # millisecondi
        ts /= 1000
    return ((ora or time.time()) - ts) / 60


def minuti_da(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 60


def opzioni_da_config(cfg: Config) -> OpzioniAnalisi:
    a = cfg.analisi
    return OpzioniAnalisi(controllo_prezzo=a.controllo_prezzo, solo_maglie_da_calcio=a.solo_maglie_da_calcio,
                          giorni_cache=a.giorni_cache)


def attesa_backoff(cfg: Config, fallimenti: int, bloccato: bool) -> float:
    base = cfg.vinted.backoff_blocco_minuti if bloccato else cfg.vinted.backoff_errore_minuti
    minuti = min(base * 2 ** (fallimenti - 1), cfg.vinted.backoff_max_minuti)
    return minuti * 60 * random.uniform(0.8, 1.2)


class Scanner:
    def __init__(self, cfg: Config, client: ClientVinted, telegram: Telegram,
                 stop: threading.Event, sveglia: threading.Event):
        self.cfg, self.client, self.telegram = cfg, client, telegram
        self.stop, self.sveglia = stop, sveglia
        self.fallimenti = 0
        self._avvisato_blocco = False

    def giro_ricerca(self, archivio: Archivio, ricerca: Ricerca) -> int:
        """Una richiesta a Vinted per una ricerca. Ritorna quanti annunci nuovi ha messo in coda."""
        params = parametri_da_ricerca(ricerca.url, self.cfg.vinted.risultati_per_ricerca)
        annunci = self.client.cerca(params)
        chiave = ricerca.url
        v = self.cfg.vinted
        max_pagina = max((a["id"] for a in annunci), default=None)

        stato = archivio.stato_ricerca(chiave)
        pausa = minuti_da(stato["ultimo_giro"]) if stato is not None else None
        if stato is None or pausa is None or pausa > v.eta_massima_annuncio_minuti:
            # Primo giro, oppure lo script è rimasto fermo (PC spento, blocco di Vinted):
            # segno tutto come già visto, niente avvisi su annunci usciti mentre non guardavamo
            for a in annunci:
                archivio.inserisci_annuncio(a, ricerca.nome, "baseline")
            archivio.aggiorna_ricerca(chiave, max_pagina)
            motivo = "prima scansione" if stato is None else (
                f"fermo da {pausa:.0f} min" if pausa is not None else "ripartenza")
            log.info("[%s] %s: %d annunci presenti segnati come visti, da ora avviso solo i nuovi",
                     ricerca.nome, motivo, len(annunci))
            return 0

        max_visto = stato["max_id"] or 0
        visti = archivio.ids_gia_visti([a["id"] for a in annunci])
        nuovi = sorted((a for a in annunci if a["id"] not in visti), key=lambda a: a["id"])
        in_coda = 0
        for a in nuovi:
            eta = eta_minuti(a)
            vecchio_per_id = max_visto and a["id"] < max_visto - v.margine_id_vecchi
            if vecchio_per_id or (eta is not None and eta > v.eta_massima_annuncio_minuti):
                # annuncio "ripubblicato" o promosso: per noi è nuovo, ma è in vendita da tempo
                archivio.inserisci_annuncio(a, ricerca.nome, "vecchio")
                log.debug("[%s] ignoro annuncio vecchio %s (%s)", ricerca.nome, a["id"], a["titolo"])
                continue
            if not archivio.inserisci_annuncio(a, ricerca.nome, "in_coda"):
                continue
            in_coda += 1
            log.info("[%s] NUOVO: %s · %s · %s · %s", ricerca.nome, a["titolo"], a["prezzo"], a["taglia"], a["url"])
            if self.cfg.telegram.avviso_immediato:
                mid = self.telegram.invia(messaggio_nuovo(a, ricerca.nome), foto=a.get("foto_principale") or None)
                archivio.imposta_tg_message_id(a["id"], mid)
            self.sveglia.set()
        archivio.aggiorna_ricerca(chiave, max_pagina)
        return in_coda

    def esegui(self) -> None:
        archivio = Archivio(self.cfg.percorso_db)
        v = self.cfg.vinted
        try:
            while not self.stop.is_set():
                try:
                    for i, ricerca in enumerate(self.cfg.ricerche):
                        self.giro_ricerca(archivio, ricerca)
                        if i < len(self.cfg.ricerche) - 1 and self.stop.wait(random.uniform(*v.pausa_tra_ricerche_secondi)):
                            return
                    if self.fallimenti:
                        log.info("Vinted risponde di nuovo dopo %d errori", self.fallimenti)
                        if self._avvisato_blocco:
                            self.telegram.invia("🟢 Vinted risponde di nuovo, scansione ripresa.")
                    self.fallimenti, self._avvisato_blocco = 0, False
                    attesa = random.uniform(v.intervallo_min_secondi, v.intervallo_max_secondi)
                except VintedErrore as ex:
                    self.fallimenti += 1
                    self.client.nuova_sessione()
                    attesa = attesa_backoff(self.cfg, self.fallimenti, ex.bloccato)
                    log.warning("Vinted: %s (errore %d di fila%s). Riprovo tra %.1f min",
                                ex, self.fallimenti, ", BLOCCO" if ex.bloccato else "", attesa / 60)
                    if self.fallimenti == 3 and not self._avvisato_blocco:
                        self._avvisato_blocco = True
                        self.telegram.invia(
                            f"🔴 Vinted non risponde da 3 tentativi ({e(ex)}). "
                            "Continuo a riprovare con attese crescenti.")
                self.stop.wait(attesa)
        finally:
            archivio.chiudi()


class Analizzatore:
    def __init__(self, cfg: Config, client: ClientVinted, ai, telegram: Telegram,
                 stop: threading.Event, sveglia: threading.Event):
        self.cfg, self.client, self.ai, self.telegram = cfg, client, ai, telegram
        self.stop, self.sveglia = stop, sveglia

    def _rispondi(self, riga, testo: str) -> None:
        self.telegram.invia(testo, in_risposta_a=riga["tg_message_id"])

    def _registra_consumo(self, archivio: Archivio, id_annuncio: int) -> str:
        """Salva i token usati in questo tentativo e restituisce la riga per il log."""
        c = self.ai.prendi_consumo()
        if not c:
            return "nessun consumo API"
        archivio.registra_consumo(id_annuncio, c.token_in, c.token_out, c.ricerche_web, c.costo)
        return f"{c} (oggi ${archivio.costo_oggi():.4f})"

    def elabora(self, archivio: Archivio, riga) -> None:
        annuncio = dict(riga)
        self.ai.prendi_consumo()  # azzera eventuali residui
        if archivio.analisi_oggi() >= self.cfg.analisi.max_analisi_al_giorno:
            archivio.segna_saltato(riga["id"], "limite giornaliero di analisi raggiunto")
            self._rispondi(riga, "⏸ Limite giornaliero di analisi raggiunto: questa non la controllo.")
            return
        spesa = archivio.costo_oggi()
        if spesa >= self.cfg.analisi.budget_giornaliero_usd:
            archivio.segna_saltato(riga["id"], f"budget giornaliero raggiunto (${spesa:.2f})")
            self._rispondi(riga, f"⏸ Budget giornaliero di ${self.cfg.analisi.budget_giornaliero_usd:.2f} "
                                 "raggiunto: questa non la controllo. Si riparte domani (ora UTC).")
            return
        try:
            foto = raccogli_foto(self.client, annuncio, self.cfg.analisi.max_foto)
            risultato = analizza_annuncio(annuncio, foto, self.ai, archivio, opzioni_da_config(self.cfg))
        except VintedErrore as ex:
            n = archivio.segna_tentativo_fallito(riga["id"], str(ex), MAX_TENTATIVI_ANALISI)
            log.warning("Analisi %s: Vinted %s (tentativo %d) · %s", riga["id"], ex, n,
                        self._registra_consumo(archivio, riga["id"]))
            if n >= MAX_TENTATIVI_ANALISI:
                self._rispondi(riga, f"❌ Non riesco ad aprire l'annuncio ({e(ex)}). Controllalo a mano.")
            self.stop.wait(300 if ex.bloccato else 30)
            return
        except Exception as ex:  # errori API, rete, ecc.
            n = archivio.segna_tentativo_fallito(riga["id"], repr(ex), MAX_TENTATIVI_ANALISI)
            log.exception("Analisi %s fallita (tentativo %d) · %s", riga["id"], n,
                          self._registra_consumo(archivio, riga["id"]))
            if n >= MAX_TENTATIVI_ANALISI:
                self._rispondi(riga, f"❌ Analisi non riuscita: {e(ex)}")
            self.stop.wait(30)
            return

        archivio.salva_esito(
            riga["id"], risultato.esito, codice_stile=risultato.codice_stile, upc=risultato.upc,
            taglia_etichetta=risultato.taglia_etichetta, prodotto=risultato.nome_prodotto or None,
            motivo=risultato.motivo + (f" Nota: {risultato.nota_annuncio}" if risultato.nota_annuncio else ""),
            giudizio_prezzo=risultato.giudizio_prezzo, prezzo_tipico=risultato.prezzo_tipico,
        )
        log.info("Esito %s: %s %s · %s · %s", riga["id"], risultato.esito, risultato.codice_stile or "",
                 self._registra_consumo(archivio, riga["id"]), risultato.motivo)
        self._rispondi(riga, messaggio_esito(risultato, annuncio, con_intestazione_annuncio=not riga["tg_message_id"]))

    def esegui(self) -> None:
        archivio = Archivio(self.cfg.percorso_db)
        try:
            while not self.stop.is_set():
                riga = archivio.prossimo_in_coda()
                if riga is None:
                    self.sveglia.wait(30)
                    self.sveglia.clear()
                    continue
                self.elabora(archivio, riga)
        finally:
            archivio.chiudi()
