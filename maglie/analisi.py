"""Analisi di un singolo annuncio: foto -> etichetta -> codice -> ricerca -> verdetto."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .ai import AnalistaAI
from .codici import normalizza_codice_stile, normalizza_upc
from .db import Archivio
from .telegram import e
from .vinted import ClientVinted, estrai_descrizione, estrai_foto_da_pagina

log = logging.getLogger(__name__)


@dataclass
class Risultato:
    esito: str
    motivo: str = ""
    codice_stile: str | None = None
    upc: str | None = None
    taglia_etichetta: str | None = None
    prodotto: dict = field(default_factory=dict)
    lettura: dict = field(default_factory=dict)
    foto_analizzate: int = 0

    @property
    def nome_prodotto(self) -> str:
        p = self.prodotto
        parti = [p.get("nome_prodotto") or p.get("squadra"), p.get("stagione"), p.get("tipo_kit")]
        return " · ".join(str(x) for x in parti if x)


def _nome_file(url: str) -> str:
    return url.split("?")[0].rsplit("/", 1)[-1]


def raccogli_foto(client: ClientVinted, annuncio: dict, max_foto: int) -> list[tuple[bytes, str]]:
    """Scarica la pagina dell'annuncio, aggiorna la descrizione e scarica le foto."""
    pagina = client.pagina_annuncio(annuncio["id"])
    descrizione = estrai_descrizione(pagina)
    if descrizione:
        annuncio["descrizione"] = descrizione
    urls = estrai_foto_da_pagina(pagina, max_foto)
    principale = annuncio.get("foto_principale")
    if principale and _nome_file(principale) not in {_nome_file(u) for u in urls}:
        urls = [principale] + urls[: max_foto - 1]
    foto = []
    for url in urls:
        try:
            foto.append(client.scarica_foto(url))
        except Exception as ex:  # una foto che non si scarica non deve bloccare tutto
            log.warning("Foto non scaricata (%s): %s", url, ex)
    return foto


def analizza_annuncio(annuncio: dict, foto: list[tuple[bytes, str]], ai: AnalistaAI,
                      archivio: Archivio) -> Risultato:
    if not foto:
        return Risultato("nessuna_etichetta", "nessuna foto scaricabile")

    lettura = ai.leggi_etichetta(foto, annuncio)
    n = len(foto)
    if not lettura.get("etichetta_trovata"):
        return Risultato("nessuna_etichetta", "nessuna foto dell'etichetta con i codici",
                         lettura=lettura, foto_analizzate=n)

    marca = lettura.get("marca") or annuncio.get("marca")
    cs = normalizza_codice_stile(lettura.get("codice_stile"), marca)
    up = normalizza_upc(lettura.get("upc"))
    base = dict(codice_stile=cs.codice, upc=up.codice, taglia_etichetta=lettura.get("taglia_etichetta"),
                lettura=lettura, foto_analizzate=n)
    nota_upc = f" UPC: {up.motivo}." if lettura.get("upc") and not up.valido else ""

    if cs.codice is None:
        return Risultato("illeggibile", "etichetta presente ma codice articolo non leggibile" + nota_upc, **base)
    if not cs.valido:
        return Risultato("codice_anomalo", cs.motivo + nota_upc, **base)

    chiave = f"{cs.codice}|{up.codice if up.valido else ''}"
    prodotto = archivio.cache_codice(chiave)
    if prodotto is None:
        prodotto = ai.cerca_codice(cs.codice, up.codice if up.valido else None, marca)
        if prodotto.get("trovato"):  # i "non trovato" non li mettiamo in cache: potrebbe andare meglio dopo
            archivio.salva_cache_codice(chiave, prodotto)
    else:
        log.info("Codice %s preso dalla cache", chiave)

    if not prodotto.get("trovato"):
        return Risultato("codice_non_trovato", (prodotto.get("note") or "nessuna fonte affidabile") + nota_upc,
                         prodotto=prodotto, **base)

    verdetto = ai.confronta(annuncio, {**lettura, "codice_stile": cs.codice}, prodotto)
    motivo = verdetto.get("motivo", "")
    if verdetto.get("taglia_coerente") is False:
        motivo += " Taglie non coerenti."
    if prodotto.get("upc_coerente") is False:
        motivo += " Il codice a barre risulta di un altro prodotto."
    esito = verdetto.get("esito", "incerto")
    # Un codice a barre di un altro prodotto è un campanello d'allarme: niente "corrisponde"
    if esito == "corrisponde" and (prodotto.get("upc_coerente") is False or verdetto.get("taglia_coerente") is False):
        esito = "incerto"
    return Risultato(esito, (motivo + nota_upc).strip(), prodotto=prodotto, **base)


# --- messaggi Telegram -----------------------------------------------------

def messaggio_nuovo(annuncio: dict, nome_ricerca: str) -> str:
    dettagli = " · ".join(x for x in (annuncio.get("prezzo"), annuncio.get("taglia"), annuncio.get("marca")) if x)
    return (
        f"🆕 <b>{e(annuncio.get('titolo'))}</b>\n"
        f"{e(dettagli)}\n"
        f"🔎 {e(nome_ricerca)}\n"
        f"<a href=\"{e(annuncio.get('url'))}\">Apri su Vinted</a>\n"
        "⏳ Controllo etichetta in corso…"
    )


INTESTAZIONI = {
    "corrisponde": "✅ <b>Etichetta OK</b>",
    "non_corrisponde": "⚠️ <b>Codice di un'altra maglia</b>",
    "incerto": "❔ <b>Da verificare a mano</b>",
    "codice_non_trovato": "❔ <b>Codice non trovato online</b>",
    "codice_anomalo": "⚠️ <b>Codice in formato anomalo</b>",
    "illeggibile": "❔ <b>Etichetta presente, codice illeggibile</b>",
    "nessuna_etichetta": "➖ <b>Nessuna foto dell'etichetta</b>",
}


def messaggio_esito(r: Risultato, annuncio: dict, con_intestazione_annuncio: bool) -> str:
    righe = []
    if con_intestazione_annuncio:
        righe.append(f"<b>{e(annuncio.get('titolo'))}</b> · {e(annuncio.get('prezzo'))}")
        righe.append(f"<a href=\"{e(annuncio.get('url'))}\">Apri su Vinted</a>")
    righe.append(INTESTAZIONI.get(r.esito, e(r.esito)))
    if r.codice_stile:
        codice = f"<code>{e(r.codice_stile)}</code>"
        if r.upc:
            codice += f" · UPC <code>{e(r.upc)}</code>"
        if r.taglia_etichetta:
            codice += f" · taglia {e(r.taglia_etichetta)}"
        righe.append(codice)
    if r.nome_prodotto:
        righe.append(f"Il codice è: {e(r.nome_prodotto)}")
    if r.motivo:
        righe.append(e(r.motivo))
    fonti = r.prodotto.get("fonti") or []
    if fonti:
        righe.append(f"<a href=\"{e(fonti[0])}\">Fonte</a>")
    if r.esito == "corrisponde":
        righe.append("📋 Aggiunta alla lista")
    return "\n".join(righe)
