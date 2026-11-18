"""Accesso a Vinted tramite vinted_scraper (sessione anonima, nessun login).

Tutto quello che tocca Vinted passa da qui, così se Vinted cambia qualcosa
c'è un solo file da sistemare.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
import threading
from urllib.parse import parse_qsl, urlparse

import httpx
import httpx._decoders
import vinted_scraper._base_wrapper as _vs_base
from vinted_scraper import VintedWrapper

log = logging.getLogger(__name__)


def _accept_encoding_supportati() -> str:
    """Le compressioni che httpx sa davvero decodificare su questo PC.

    vinted_scraper chiede sempre "gzip, deflate, br, zstd". Se il modulo `brotli` non si carica
    (es. Windows con il Controllo intelligente delle app, che blocca le DLL non firmate) o manca
    il supporto zstd, il server potrebbe rispondere in un formato che httpx non sa leggere.
    """
    return ", ".join(c for c in ("gzip", "deflate", "br", "zstd") if c in httpx._decoders.SUPPORTED_DECODERS)


def _correggi_header(funzione):
    def avvolta(*args, **kwargs):
        h = funzione(*args, **kwargs)
        if "Accept-Encoding" in h:
            h["Accept-Encoding"] = _accept_encoding_supportati()
        return h
    return avvolta


# vinted_scraper costruisce gli header con queste due funzioni: le avvolgiamo una volta sola
if not getattr(_vs_base.get_cookie_headers, "_corretta", False):
    for _nome in ("get_cookie_headers", "get_curl_headers"):
        _f = _correggi_header(getattr(_vs_base, _nome))
        _f._corretta = True
        setattr(_vs_base, _nome, _f)

STATUS_BLOCCO = {403, 429}

# Parametri dell'URL del sito -> parametri dell'API catalogo usata da vinted_scraper
MAPPA_PARAMETRI = {
    "catalog[]": "attribute_ids[catalog]",
    "catalog_ids[]": "attribute_ids[catalog]",
    "brand_ids[]": "attribute_ids[brand]",
    "size_ids[]": "attribute_ids[size]",
    "status_ids[]": "attribute_ids[status]",
    "color_ids[]": "attribute_ids[color]",
    "material_ids[]": "attribute_ids[material]",
}
PARAMETRI_DIRETTI = {"search_text", "price_from", "price_to", "currency", "order"}


class VintedErrore(Exception):
    def __init__(self, messaggio: str, status: int | None = None):
        super().__init__(messaggio)
        self.status = status

    @property
    def bloccato(self) -> bool:
        return self.status in STATUS_BLOCCO


def _status_da_messaggio(msg: str) -> int | None:
    m = re.search(r"(?:error code|status code):\s*(\d{3})", msg)
    return int(m.group(1)) if m else None


def parametri_da_ricerca(testo_o_url: str, per_pagina: int = 20) -> dict:
    """Converte un URL di ricerca copiato dal browser (o un testo) nei parametri dell'API."""
    testo_o_url = testo_o_url.strip()
    params: dict[str, str] = {}
    if testo_o_url.startswith("http"):
        multi: dict[str, list[str]] = {}
        for chiave, valore in parse_qsl(urlparse(testo_o_url).query, keep_blank_values=False):
            if chiave in MAPPA_PARAMETRI:
                multi.setdefault(MAPPA_PARAMETRI[chiave], []).append(valore)
            elif chiave in PARAMETRI_DIRETTI:
                params[chiave] = valore
            # gli altri (time, page, search_id...) li ignoriamo
        for chiave, valori in multi.items():
            params[chiave] = ",".join(valori)
    else:
        params["search_text"] = testo_o_url
    params["order"] = "newest_first"  # sempre: ci servono i più recenti
    params["per_page"] = str(per_pagina)
    params["page"] = "1"
    return params


def _prezzo(raw) -> str:
    if isinstance(raw, dict):
        importo = raw.get("amount")
        valuta = raw.get("currency_code", "")
        return f"{importo} {valuta}".strip() if importo is not None else ""
    return str(raw) if raw is not None else ""


def _da_etichetta_accessibile(testo: str, campo: str) -> str:
    # "..., Brand: adidas, Condizioni: Buone, Taglia: XS, 5.00 €, ..."
    m = re.search(rf"{campo}:\s*([^,]+)", testo or "")
    return m.group(1).strip() if m else ""


def normalizza_annuncio(raw: dict, dominio: str) -> dict:
    foto = raw.get("photo") or {}
    alta = foto.get("high_resolution") or {}
    box = raw.get("item_box") or {}
    etichetta = box.get("accessibility_label") or ""
    # second_line = "L · Ottime" oppure "5 anni / 110 cm · Buone"
    parti = [p.strip() for p in (box.get("second_line") or "").split("·")]
    url = raw.get("url") or f"{dominio}/items/{raw['id']}"
    if url.startswith("/"):
        url = dominio + url
    return {
        "id": int(raw["id"]),
        "titolo": raw.get("title") or "",
        "descrizione": raw.get("description") or "",
        "prezzo": _prezzo(raw.get("price")),
        "taglia": raw.get("size_title") or _da_etichetta_accessibile(etichetta, "Taglia") or (parti[0] if parti[0] else ""),
        "marca": raw.get("brand_title") or box.get("first_line") or _da_etichetta_accessibile(etichetta, "Brand"),
        "condizione": raw.get("status") if isinstance(raw.get("status"), str) else (
            _da_etichetta_accessibile(etichetta, "Condizioni") or (parti[1] if len(parti) > 1 else "")),
        "url": url,
        "foto_principale": foto.get("url") or foto.get("full_size_url") or "",
        # Vinted non dà più l'orario della foto nella ricerca: se c'è lo usiamo, altrimenti
        # gli annunci vecchi si riconoscono dall'ID (vedi Scanner)
        "timestamp": alta.get("timestamp"),
        "promosso": bool(raw.get("promoted")),
    }


_RE_FOTO = re.compile(r"https://images\d*\.vinted\.net/[^\s\"'<>\\]+")
_RE_OG_DESC = re.compile(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', re.I)


def estrai_foto_da_pagina(pagina: str, max_foto: int) -> list[str]:
    """Estrae gli URL delle foto dalla pagina HTML dell'annuncio, nell'ordine in cui compaiono.

    Preferisce la versione f800 di ogni foto e scarta i duplicati (stessa foto in più formati).
    """
    testo = html_lib.unescape(pagina).replace("\\/", "/").replace("\\u0026", "&")
    viste: dict[str, str] = {}  # chiave foto -> url migliore
    ordine: list[str] = []
    for url in _RE_FOTO.findall(testo):
        percorso = url.split("?")[0]
        # .../t/<token>/<formato>/<nome>.jpeg -> chiave = nome file
        nome = percorso.rsplit("/", 1)[-1]
        if not re.search(r"\.(jpe?g|webp|png)$", nome, re.I):
            continue
        if nome not in viste:
            viste[nome] = url
            ordine.append(nome)
        elif "/f800/" in url and "/f800/" not in viste[nome]:
            viste[nome] = url
    return [viste[n] for n in ordine][:max_foto]


def estrai_descrizione(pagina: str) -> str:
    m = _RE_OG_DESC.search(pagina)
    return html_lib.unescape(m.group(1)).strip() if m else ""


class ClientVinted:
    def __init__(self, dominio: str):
        self.dominio = dominio.rstrip("/")
        self._wrapper: VintedWrapper | None = None
        # scanner e analizzatore condividono il client: una richiesta a Vinted alla volta
        self._lock = threading.RLock()

    def _w(self) -> VintedWrapper:
        if self._wrapper is None:
            try:
                self._wrapper = VintedWrapper(self.dominio)
            except RuntimeError as e:
                raise VintedErrore(str(e), _status_da_messaggio(str(e))) from e
            except httpx.HTTPError as e:
                raise VintedErrore(f"Errore di rete: {e}") from e
        return self._wrapper

    def nuova_sessione(self) -> None:
        """Butta via cookie e sessione: alla prossima richiesta se ne crea una nuova."""
        with self._lock:
            self._wrapper = None

    def cerca_grezzo(self, params: dict) -> dict:
        """La risposta JSON della ricerca così come arriva da Vinted."""
        try:
            with self._lock:
                return self._w().search(params)
        except RuntimeError as e:
            raise VintedErrore(str(e), _status_da_messaggio(str(e))) from e
        except httpx.HTTPError as e:
            raise VintedErrore(f"Errore di rete: {e}") from e

    def cerca(self, params: dict) -> list[dict]:
        dati = self.cerca_grezzo(params)
        return [normalizza_annuncio(r, self.dominio) for r in dati.get("items", [])]

    def pagina_annuncio(self, id_annuncio: int) -> str:
        """Scarica la pagina pubblica dell'annuncio (serve per avere TUTTE le foto)."""
        try:
            with self._lock:
                w = self._w()
                # metodi "privati" di vinted_scraper: se aggiorni la libreria e si rompe, guarda qui
                r = w._client.get(f"/items/{id_annuncio}", headers=w._build_page_headers())
        except httpx.HTTPError as e:
            raise VintedErrore(f"Errore di rete: {e}") from e
        if r.status_code != 200:
            raise VintedErrore(f"Pagina annuncio {id_annuncio}: HTTP {r.status_code}", r.status_code)
        return r.text

    @staticmethod
    def scarica_foto(url: str, timeout: float = 15) -> tuple[bytes, str]:
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.vinted.it/"})
        r.raise_for_status()
        tipo = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if tipo not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            tipo = "image/jpeg"
        return r.content, tipo
