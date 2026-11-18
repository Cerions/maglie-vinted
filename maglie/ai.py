"""Le tre chiamate a Claude: leggere l'etichetta, cercare il codice, confrontare.

Ogni chiamata restituisce JSON tramite un "tool" con schema fisso, così non
dobbiamo fare parsing di testo libero.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import anthropic

log = logging.getLogger(__name__)


@dataclass
class Prezzi:
    """Prezzi in dollari. I default sono quelli di Claude Haiku 5.5: se cambi modello, aggiornali in config.toml."""
    input_mtok: float = 0.10
    output_mtok: float = 0.50
    ricerca_web: float = 0.01
    # Haiku 5.5 costa di più se il prompt di una singola richiesta supera questa soglia
    soglia_prompt_lungo: int = 100_000
    input_mtok_lungo: float = 0.50
    output_mtok_lungo: float = 2.50


@dataclass
class Consumo:
    token_in: int = 0
    token_out: int = 0
    ricerche_web: int = 0
    costo: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.token_in or self.token_out or self.ricerche_web)

    def __str__(self) -> str:
        return (f"{self.token_in:,} in / {self.token_out:,} out · {self.ricerche_web} ricerche web · "
                f"${self.costo:.4f}").replace(",", ".")


def costo_richiesta(prezzi: Prezzi, token_in: int, token_out: int, ricerche_web: int) -> float:
    lungo = token_in > prezzi.soglia_prompt_lungo
    p_in = prezzi.input_mtok_lungo if lungo else prezzi.input_mtok
    p_out = prezzi.output_mtok_lungo if lungo else prezzi.output_mtok
    return token_in / 1e6 * p_in + token_out / 1e6 * p_out + ricerche_web * prezzi.ricerca_web

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
    def __init__(self, api_key: str, modello: str, ricerche_web: int = 3, prezzi: Prezzi | None = None):
        if not api_key:
            raise ValueError("Manca ANTHROPIC_API_KEY nel file .env")
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3)
        self.modello = modello
        self.ricerche_web = ricerche_web
        self.prezzi = prezzi or Prezzi()
        self.consumo = Consumo()

    def prendi_consumo(self) -> Consumo:
        """Restituisce il consumo accumulato dall'ultima chiamata a questo metodo e lo azzera."""
        c, self.consumo = self.consumo, Consumo()
        return c

    def _crea(self, **kwargs):
        r = self.client.messages.create(**kwargs)
        uso = getattr(r, "usage", None)
        if uso is not None:
            t_in = (getattr(uso, "input_tokens", 0) or 0) + (getattr(uso, "cache_creation_input_tokens", 0) or 0) \
                + (getattr(uso, "cache_read_input_tokens", 0) or 0)
            t_out = getattr(uso, "output_tokens", 0) or 0
            server = getattr(uso, "server_tool_use", None)
            ricerche = (getattr(server, "web_search_requests", 0) or 0) if server is not None else 0
            self.consumo.token_in += t_in
            self.consumo.token_out += t_out
            self.consumo.ricerche_web += ricerche
            self.consumo.costo += costo_richiesta(self.prezzi, t_in, t_out, ricerche)
        return r

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
        r = self._crea(
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
            r = self._crea(
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
        r = self._crea(
            model=self.modello, max_tokens=512, tools=[TOOL_VERDETTO],
            tool_choice={"type": "tool", "name": "riporta_verdetto"},
            messages=[{"role": "user", "content": testo}],
        )
        return _tool_input(r, "riporta_verdetto") or {"esito": "incerto", "motivo": "nessuna risposta"}
