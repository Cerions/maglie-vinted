"""Le tre chiamate a Claude: leggere l'etichetta, cercare il codice, confrontare.

Ogni chiamata restituisce JSON tramite un "tool" con schema fisso, così non
dobbiamo fare parsing di testo libero.
"""

from __future__ import annotations

import base64
import logging
import random
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
STATUS_DA_RIPROVARE = {408, 409, 429, 500, 502, 503, 504, 529}


class ErroreAPI(Exception):
    def __init__(self, messaggio: str, status: int | None = None):
        super().__init__(messaggio)
        self.status = status


class Oggetto(dict):
    """dict con accesso anche come attributo (r.content, blocco.type, uso.input_tokens...).

    Resta un dict, quindi i blocchi della risposta si possono rimandare all'API così come sono.
    """

    def __getattr__(self, nome):
        try:
            return self[nome]
        except KeyError:
            raise AttributeError(nome) from None


def _in_oggetti(x):
    if isinstance(x, dict):
        return Oggetto({k: _in_oggetti(v) for k, v in x.items()})
    if isinstance(x, list):
        return [_in_oggetti(v) for v in x]
    return x


class _Messaggi:
    def __init__(self, client: "ClientClaude"):
        self._c = client

    def create(self, **corpo):
        return self._c.richiesta(corpo)


class ClientClaude:
    """Client minimo per la Messages API, solo httpx.

    Sostituisce l'SDK ufficiale `anthropic`, che richiede moduli compilati (jiter, pydantic-core)
    che il Controllo intelligente delle app di Windows blocca.
    """

    def __init__(self, api_key: str, max_tentativi: int = 4, timeout: float = 180):
        self.api_key = api_key
        self.max_tentativi = max_tentativi
        self.http = httpx.Client(timeout=timeout)
        self.messages = _Messaggi(self)

    def richiesta(self, corpo: dict) -> Oggetto:
        intestazioni = {"x-api-key": self.api_key, "anthropic-version": API_VERSION,
                        "content-type": "application/json"}
        for tentativo in range(1, self.max_tentativi + 1):
            try:
                r = self.http.post(API_URL, json=corpo, headers=intestazioni)
            except httpx.HTTPError as ex:
                if tentativo == self.max_tentativi:
                    raise ErroreAPI(f"Errore di rete verso l'API: {ex}") from ex
                time.sleep(2 ** tentativo + random.random())
                continue
            if r.status_code == 200:
                return _in_oggetti(r.json())
            try:
                dettaglio = r.json().get("error", {}).get("message", r.text)
            except ValueError:
                dettaglio = r.text
            if r.status_code in STATUS_DA_RIPROVARE and tentativo < self.max_tentativi:
                attesa = float(r.headers.get("retry-after", 0) or 0) or 2 ** tentativo + random.random()
                log.warning("API %s (%s): riprovo tra %.0f s", r.status_code, dettaglio, attesa)
                time.sleep(min(attesa, 60))
                continue
            raise ErroreAPI(f"API {r.status_code}: {dettaglio}", r.status_code)
        raise ErroreAPI("API: tentativi esauriti")


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
            "aspetto": {"type": ["string", "null"],
                        "description": "Com'è fatta la maglia secondo le fonti: colore base, grafica/pattern, "
                                       "sponsor, colletto, dettagli riconoscibili."},
            "versione": {"type": ["string", "null"], "description": "stadium/replica, vapor/match/authentic, se indicato"},
            "taglia_da_upc": {"type": ["string", "null"], "description": "Taglia associata al codice a barre, solo se una fonte la riporta."},
            "upc_coerente": {"type": ["boolean", "null"], "description": "Il codice a barre risulta associato allo stesso prodotto? null se non verificabile."},
            "fonti": {"type": "array", "items": {"type": "string"}, "description": "URL delle pagine usate."},
            "note": {"type": "string"},
        },
        # tutti obbligatori (null se non noti): così il modello non può rispondere solo {"trovato": true}
        "required": ["trovato", "nome_prodotto", "squadra", "stagione", "tipo_kit", "colori", "aspetto", "versione",
                     "taglia_da_upc", "upc_coerente", "fonti", "note"],
    },
}

TOOL_VERDETTO = {
    "name": "riporta_verdetto",
    "description": "Dai il verdetto sul confronto tra annuncio ed etichetta.",
    "input_schema": {
        "type": "object",
        "properties": {
            "esito": {"type": "string", "enum": ["corrisponde", "non_corrisponde", "incerto"]},
            "taglia_coerente": {"type": ["boolean", "null"],
                                "description": "false solo se due taglie note sono in conflitto; null se mancano dati."},
            "motivo": {"type": "string", "description": "Una o due frasi, in italiano."},
            "nota_annuncio": {"type": ["string", "null"],
                              "description": "Errori nel testo del venditore (es. la chiama terza maglia ma è la "
                                             "quarta), null se non ce ne sono."},
        },
        "required": ["esito", "taglia_coerente", "motivo", "nota_annuncio"],
    },
}


def _blocco_tool(risposta, nome: str):
    for blocco in risposta.content:
        if blocco.type == "tool_use" and blocco.name == nome:
            return blocco
    return None


def _tool_input(risposta, nome: str) -> dict | None:
    b = _blocco_tool(risposta, nome)
    return dict(b.input) if b is not None else None


def prodotto_completo(p: dict | None) -> bool:
    """Un risultato "trovato" serve solo se dice almeno squadra, stagione o nome e da dove viene."""
    return bool(p and p.get("trovato") and p.get("squadra") and (p.get("stagione") or p.get("nome_prodotto"))
                and p.get("fonti"))


class AnalistaAI:
    def __init__(self, api_key: str, modello: str, ricerche_web: int = 3, prezzi: Prezzi | None = None):
        if not api_key:
            raise ValueError("Manca ANTHROPIC_API_KEY nel file .env")
        self.client = ClientClaude(api_key)
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
            f"Marca dichiarata: {annuncio.get('marca')}\n\n"
            "Cerca la foto dell'etichetta interna con i codici (di solito una piccola etichetta bianca "
            "con codice articolo tipo CV8413-101 e un codice a barre numerico). Trascrivi i codici "
            "carattere per carattere: se una cifra non è leggibile con sicurezza, scrivi null invece "
            "di indovinare. Non confondere l'etichetta di composizione/lavaggio con quella dei codici. "
            "Descrivi anche la maglia mostrata nelle altre foto, SOLO in base a ciò che vedi (stemma, "
            "colori, grafica, sponsor, colletto): non dedurre stagione o tipo di kit da testi o supposizioni."
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
            + "Alla fine chiama sempre riporta_prodotto compilando TUTTI i campi con quello che hai "
              "trovato (null solo se davvero sconosciuto) e mettendo in fonti gli URL usati. "
              "Se le fonti non identificano il prodotto, trovato=false."
        )
        messaggi: list[dict] = [{"role": "user", "content": richiesta}]
        strumenti = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": self.ricerche_web},
            TOOL_PRODOTTO,
        ]
        gia_corretto = False
        forza = False
        for tentativo in range(5):
            forza = forza or tentativo >= 3
            r = self._crea(
                model=self.modello, max_tokens=2048, tools=strumenti, messages=messaggi,
                tool_choice={"type": "tool", "name": "riporta_prodotto"} if forza else {"type": "auto"},
            )
            blocco = _blocco_tool(r, "riporta_prodotto")
            if blocco is not None:
                risultato = dict(blocco.input)
                if not risultato.get("trovato") or prodotto_completo(risultato) or gia_corretto:
                    return risultato
                # "trovato" ma senza dettagli: una sola richiesta di correzione, senza nuove ricerche
                gia_corretto, forza = True, True
                log.info("Ricerca codice %s: risposta incompleta, chiedo di completarla", codice)
                messaggi.append({"role": "assistant", "content": r.content})
                messaggi.append({"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": getattr(blocco, "id", None) or "", "is_error": True,
                    "content": "Risposta incompleta: compila nome_prodotto, squadra, stagione, tipo_kit, colori e "
                               "fonti (URL) con quello che hai già trovato nelle ricerche. Se non li conosci, "
                               "imposta trovato=false.",
                }]})
                continue
            messaggi.append({"role": "assistant", "content": r.content})
            if r.stop_reason != "pause_turn":  # ha finito senza chiamare il tool: sollecitiamo
                messaggi.append({"role": "user", "content": "Ora chiama riporta_prodotto con quello che hai trovato."})
        return {"trovato": False, "fonti": [], "note": "nessuna risposta strutturata"}

    # 3) Confronto annuncio <-> prodotto --------------------------------------
    def confronta(self, annuncio: dict, lettura: dict, prodotto: dict,
                  foto_maglia: list[tuple[bytes, str]] | None = None) -> dict:
        contenuto: list[dict] = []
        for i, (dati, tipo) in enumerate(foto_maglia or [], start=1):
            contenuto.append({"type": "text", "text": f"Foto della maglia in vendita {i}:"})
            contenuto.append({"type": "image", "source": {
                "type": "base64", "media_type": tipo, "data": base64.b64encode(dati).decode()}})
        contenuto.append({"type": "text", "text": (
            "Devi dire se il codice sull'etichetta corrisponde alla maglia venduta nell'annuncio.\n\n"
            f"PRODOTTO DEL CODICE {lettura.get('codice_stile')} (dalle fonti web)\n{prodotto}\n\n"
            "MAGLIA IN VENDITA\n"
            + ("Le foto qui sopra sono la prova principale.\n" if foto_maglia else "")
            + f"Descrizione delle foto: {lettura.get('descrizione_maglia')}\n"
            f"Taglia sull'etichetta: {lettura.get('taglia_etichetta')}\n\n"
            f"TESTO DEL VENDITORE (può contenere errori)\nTitolo: {annuncio.get('titolo')}\n"
            f"Descrizione: {annuncio.get('descrizione')}\nMarca: {annuncio.get('marca')}\n"
            f"Taglia annuncio: {annuncio.get('taglia')}\n\n"
            "Regole:\n"
            "- 'corrisponde': la maglia nelle FOTO è quella del codice (stessa squadra, stessa grafica e colori "
            "descritti dalle fonti).\n"
            "- 'non_corrisponde': le FOTO mostrano una maglia diversa da quella del codice (altra squadra, "
            "altra grafica, altri colori, altro sponsor).\n"
            "- 'incerto': le foto non bastano per decidere.\n"
            "- Il testo del venditore sbaglia spesso il nome del kit (terza/quarta, home/away, stagione): "
            "una differenza SOLO nel testo non è un motivo per 'non_corrisponde'. Segnalala in nota_annuncio.\n"
            "- Dati mancanti (taglia annuncio vuota, nessuna taglia associata al codice a barre) non sono "
            "conflitti: in quel caso taglia_coerente = null."
        )})
        r = self._crea(
            model=self.modello, max_tokens=700, tools=[TOOL_VERDETTO],
            tool_choice={"type": "tool", "name": "riporta_verdetto"},
            messages=[{"role": "user", "content": contenuto}],
        )
        return _tool_input(r, "riporta_verdetto") or {"esito": "incerto", "motivo": "nessuna risposta"}
