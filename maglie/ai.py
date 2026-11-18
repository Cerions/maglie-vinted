"""Le tre chiamate a Claude: leggere l'etichetta, cercare il codice, confrontare.

Ogni chiamata restituisce JSON tramite un "tool" con schema fisso, così non
dobbiamo fare parsing di testo libero.
"""

from __future__ import annotations

import base64
import logging

import anthropic

log = logging.getLogger(__name__)

TOOL_ETICHETTA = {
    "name": "riporta_etichetta",
    "description": "Riporta cosa hai trovato nelle foto dell'annuncio.",
    "input_schema": {
        "type": "object",
        "properties": {
            "etichetta_trovata": {"type": "boolean", "description": "C'è almeno una foto dell'etichetta interna con i codici (codice articolo / codice a barre)?"},
            "indice_foto": {"type": ["integer", "null"], "description": "Numero della foto con l'etichetta (da 1)."},
            "marca": {"type": ["string", "null"], "description": "Marca dell'etichetta: nike, adidas, puma, ..."},
            "codice_stile": {"type": ["string", "null"], "description": "Codice articolo esattamente come stampato, es. CV8413-101, GM4614, 763432-01."},
            "upc": {"type": ["string", "null"], "description": "Codice a barre numerico (12-13 cifre) se presente."},
            "taglia_etichetta": {"type": ["string", "null"], "description": "Taglia stampata sull'etichetta dei codici."},
            "leggibilita": {"type": "string", "enum": ["buona", "parziale", "scarsa"]},
            "descrizione_maglia": {"type": "string", "description": "Cosa mostrano le foto: squadra/stemma, colori principali, sponsor, prima/seconda/terza maglia se deducibile, eventuale stagione."},
            "note": {"type": "string"},
        },
        "required": ["etichetta_trovata", "leggibilita", "descrizione_maglia"],
    },
}

TOOL_PRODOTTO = {
    "name": "riporta_prodotto",
    "description": "Riporta a quale prodotto corrisponde il codice, in base alle ricerche.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trovato": {"type": "boolean", "description": "Le fonti identificano con sicurezza il prodotto con quel codice?"},
            "nome_prodotto": {"type": ["string", "null"]},
            "squadra": {"type": ["string", "null"]},
            "stagione": {"type": ["string", "null"], "description": "Es. 2020/21"},
            "tipo_kit": {"type": ["string", "null"], "description": "home, away, third, portiere, allenamento, altro"},
            "colori": {"type": ["string", "null"]},
            "versione": {"type": ["string", "null"], "description": "stadium/replica, vapor/match/authentic, se indicato"},
            "taglia_da_upc": {"type": ["string", "null"], "description": "Taglia associata al codice a barre, solo se una fonte la riporta."},
            "upc_coerente": {"type": ["boolean", "null"], "description": "Il codice a barre risulta associato allo stesso prodotto? null se non verificabile."},
            "fonti": {"type": "array", "items": {"type": "string"}, "description": "URL delle pagine usate."},
            "note": {"type": "string"},
        },
        "required": ["trovato", "fonti"],
    },
}

TOOL_VERDETTO = {
    "name": "riporta_verdetto",
    "description": "Dai il verdetto sul confronto tra annuncio ed etichetta.",
    "input_schema": {
        "type": "object",
        "properties": {
            "esito": {"type": "string", "enum": ["corrisponde", "non_corrisponde", "incerto"]},
            "taglia_coerente": {"type": ["boolean", "null"]},
            "motivo": {"type": "string", "description": "Una o due frasi, in italiano."},
        },
        "required": ["esito", "motivo"],
    },
}


def _tool_input(risposta, nome: str) -> dict | None:
    for blocco in risposta.content:
        if blocco.type == "tool_use" and blocco.name == nome:
            return dict(blocco.input)
    return None


class AnalistaAI:
    def __init__(self, api_key: str, modello: str, ricerche_web: int = 3):
        if not api_key:
            raise ValueError("Manca ANTHROPIC_API_KEY nel file .env")
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3)
        self.modello = modello
        self.ricerche_web = ricerche_web

    # 1) Vision: trova l'etichetta e leggi i codici --------------------------
    def leggi_etichetta(self, foto: list[tuple[bytes, str]], annuncio: dict) -> dict:
        contenuto: list[dict] = []
        for i, (dati, tipo) in enumerate(foto, start=1):
            contenuto.append({"type": "text", "text": f"Foto {i}:"})
            contenuto.append({"type": "image", "source": {
                "type": "base64", "media_type": tipo, "data": base64.b64encode(dati).decode()}})
        contenuto.append({"type": "text", "text": (
            "Queste sono le foto di un annuncio Vinted di una maglia da calcio.\n"
            f"Titolo: {annuncio.get('titolo')}\nMarca dichiarata: {annuncio.get('marca')}\n\n"
            "Cerca la foto dell'etichetta interna con i codici (di solito una piccola etichetta bianca "
            "con codice articolo tipo CV8413-101 e un codice a barre numerico). Trascrivi i codici "
            "carattere per carattere: se una cifra non è leggibile con sicurezza, scrivi null invece "
            "di indovinare. Non confondere l'etichetta di composizione/lavaggio con quella dei codici. "
            "Descrivi anche la maglia mostrata nelle altre foto."
        )})
        r = self.client.messages.create(
            model=self.modello, max_tokens=1024, tools=[TOOL_ETICHETTA],
            tool_choice={"type": "tool", "name": "riporta_etichetta"},
            messages=[{"role": "user", "content": contenuto}],
        )
        return _tool_input(r, "riporta_etichetta") or {"etichetta_trovata": False, "leggibilita": "scarsa",
                                                         "descrizione_maglia": ""}

    # 2) Ricerca web del codice ------------------------------------------------
    def cerca_codice(self, codice: str, upc: str | None, marca: str | None) -> dict:
        richiesta = (
            f"Identifica la maglia da calcio {marca or ''} con codice articolo {codice}"
            + (f" e codice a barre (UPC/EAN) {upc}" if upc else "")
            + ". Cerca il codice esatto sul web (negozi, database di maglie come footballkitarchive, "
              "siti di rivenditori). Riporta squadra, stagione, tipo di kit e colori SOLO se le fonti "
              "lo confermano per quel codice esatto. "
            + ("Se trovi il codice a barre, indica a quale prodotto e taglia è associato. " if upc else "")
            + "Alla fine chiama sempre riporta_prodotto."
        )
        messaggi: list[dict] = [{"role": "user", "content": richiesta}]
        strumenti = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": self.ricerche_web},
            TOOL_PRODOTTO,
        ]
        for tentativo in range(5):
            forza = tentativo >= 3
            r = self.client.messages.create(
                model=self.modello, max_tokens=2048, tools=strumenti, messages=messaggi,
                tool_choice={"type": "tool", "name": "riporta_prodotto"} if forza else {"type": "auto"},
            )
            risultato = _tool_input(r, "riporta_prodotto")
            if risultato is not None:
                return risultato
            messaggi.append({"role": "assistant", "content": r.content})
            if r.stop_reason != "pause_turn":  # ha finito senza chiamare il tool: sollecitiamo
                messaggi.append({"role": "user", "content": "Ora chiama riporta_prodotto con quello che hai trovato."})
        return {"trovato": False, "fonti": [], "note": "nessuna risposta strutturata"}

    # 3) Confronto annuncio <-> prodotto --------------------------------------
    def confronta(self, annuncio: dict, lettura: dict, prodotto: dict) -> dict:
        testo = (
            "Devi dire se il codice sull'etichetta corrisponde alla maglia venduta nell'annuncio.\n\n"
            f"ANNUNCIO\nTitolo: {annuncio.get('titolo')}\nDescrizione: {annuncio.get('descrizione')}\n"
            f"Marca: {annuncio.get('marca')}\nTaglia annuncio: {annuncio.get('taglia')}\n"
            f"Cosa si vede nelle foto: {lettura.get('descrizione_maglia')}\n"
            f"Taglia sull'etichetta: {lettura.get('taglia_etichetta')}\n\n"
            f"PRODOTTO DEL CODICE {lettura.get('codice_stile')}\n{prodotto}\n\n"
            "'corrisponde' = squadra, stagione/tipo di kit e colori del codice sono coerenti con la "
            "maglia dell'annuncio. 'non_corrisponde' = il codice appartiene chiaramente a un'altra "
            "maglia (altra squadra, altra stagione, altro kit o colore). 'incerto' = dati insufficienti. "
            "Controlla anche che taglia etichetta, taglia annuncio ed eventuale taglia da UPC siano coerenti."
        )
        r = self.client.messages.create(
            model=self.modello, max_tokens=512, tools=[TOOL_VERDETTO],
            tool_choice={"type": "tool", "name": "riporta_verdetto"},
            messages=[{"role": "user", "content": testo}],
        )
        return _tool_input(r, "riporta_verdetto") or {"esito": "incerto", "motivo": "nessuna risposta"}
