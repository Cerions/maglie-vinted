"""Analisi di un singolo annuncio: foto -> etichetta -> codice -> ricerca -> verdetto."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .ai import AnalistaAI, prodotto_completo
from .codici import normalizza_codice_stile, normalizza_upc
from .db import Archivio
from .telegram import e
from .vinted import ClientVinted, estrai_descrizione, estrai_foto_da_pagina

log = logging.getLogger(__name__)

MAX_FOTO_CONFRONTO = 3


@dataclass
class OpzioniAnalisi:
    controllo_prezzo: str = "occasioni"   # occasioni | sempre | mai
    solo_maglie_da_calcio: bool = True
    giorni_cache: float = 7


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
    nota_annuncio: str | None = None  # errori nel testo del venditore (es. "terza" invece di "quarta")
    prezzi: dict | None = None
    fonte_prezzi: str | None = None   # "ricerca" | "stima"
    giudizio_prezzo: str | None = None  # sotto | media | sopra | indicativo
    testo_prezzo: str = ""
    prezzo_tipico: str | None = None

    @property
    def nome_prodotto(self) -> str:
        p = self.prodotto
        parti = [p.get("nome_prodotto") or p.get("squadra"), p.get("stagione"), p.get("tipo_kit")]
        return " · ".join(str(x) for x in parti if x)

    @property
    def nome_identificato(self) -> str:
        i = self.lettura.get("identificazione") or {}
        parti = [i.get("squadra"), i.get("stagione"), i.get("tipo_kit"), i.get("versione")]
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


ESITI_DA_VALUTARE_PREZZO = {"corrisponde", "incerto", "visivo_originale", "visivo_dubbia"}
GIUDIZIO_VISIVO = {
    "probabile_originale": "visivo_originale",
    "dubbia": "visivo_dubbia",
    "probabile_falso": "visivo_falso",
}


def _numero(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def prezzo_annuncio_eur(annuncio: dict) -> float | None:
    """'25.0 EUR' -> 25.0 (None se in un'altra valuta o assente)."""
    testo = str(annuncio.get("prezzo") or "").strip()
    parti = testo.split()
    if len(parti) == 2 and parti[1].upper() != "EUR":
        return None
    return _numero(parti[0].replace(",", ".")) if parti else None


def _euro(v: float) -> str:
    return f"{v:.0f} €" if v >= 10 else f"{v:.2f} €".replace(".", ",")


def valuta_prezzo(prezzo: float | None, prezzi: dict | None, fonte: str | None) -> tuple[str | None, str, str | None]:
    """Confronta il prezzo dell'annuncio con quelli tipici. Ritorna (giudizio, testo, prezzo_tipico)."""
    if prezzo is None or not prezzi:
        return None, "", None
    mn, mx, nuovo = (_numero(prezzi.get(k)) for k in ("usato_min_eur", "usato_max_eur", "nuovo_eur"))
    if mn and mx and mn > mx:
        mn, mx = mx, mn
    stima = " (stima, senza ricerca)" if fonte == "stima" else ""
    if mn and mx:
        media = (mn + mx) / 2
        diff = round((1 - prezzo / media) * 100)
        tipico = f"{_euro(mn)}–{_euro(mx)}"
        testo = f"💰 {_euro(prezzo)} · di solito {tipico} usata" + (f", nuova ~{_euro(nuovo)}" if nuovo else "")
        if prezzo < mn:
            return "sotto", f"{testo} → circa il {diff}% in meno del solito 🔥{stima}", tipico
        if prezzo <= mx:
            return "media", f"{testo} → nella media{stima}", tipico
        return "sopra", f"{testo} → circa il {-diff}% in più del solito{stima}", tipico
    if nuovo:
        quota = round(prezzo / nuovo * 100)
        return "indicativo", f"💰 {_euro(prezzo)} · nuova costa ~{_euro(nuovo)} ({quota}% del prezzo da nuova){stima}", None
    return None, "", None


def _chiave_maglia(identificazione: dict | None, nome_prodotto: str | None) -> str | None:
    if nome_prodotto:
        base = nome_prodotto
    elif identificazione and identificazione.get("squadra") and identificazione.get("stagione"):
        base = " ".join(str(identificazione.get(k) or "") for k in ("squadra", "stagione", "tipo_kit", "versione"))
    else:
        return None
    return "prezzi|" + " ".join(base.lower().split())


def _aggiungi_prezzo(r: Risultato, annuncio: dict, ai: AnalistaAI, archivio: Archivio, opz: OpzioniAnalisi) -> None:
    """Completa il risultato con il confronto di prezzo (ricerca web solo se serve)."""
    if opz.controllo_prezzo == "mai" or r.esito not in ESITI_DA_VALUTARE_PREZZO:
        return
    prezzo = prezzo_annuncio_eur(annuncio)
    prezzi, fonte = None, None

    trovati = r.prodotto.get("prezzi") if r.prodotto else None
    if trovati and (trovati.get("usato_min_eur") or trovati.get("nuovo_eur")):
        prezzi, fonte = trovati, "ricerca"  # arrivati gratis con la ricerca del codice
    else:
        stima = r.lettura.get("stima_prezzo_usato_eur") or {}
        stima_min, stima_max = _numero(stima.get("min")), _numero(stima.get("max"))
        identificazione = r.lettura.get("identificazione") or {}
        nome = r.nome_prodotto or r.nome_identificato
        chiave = _chiave_maglia(identificazione, r.prodotto.get("nome_prodotto") if r.prodotto else None)
        # "occasioni": la ricerca web (qualche centesimo) si fa solo se, secondo la stima gratuita della
        # prima chiamata, l'annuncio costa meno della media: lì vale la pena verificare con prezzi veri
        media_stima = (stima_min + stima_max) / 2 if stima_min and stima_max else stima_max
        conviene_cercare = (
            chiave is not None
            and (opz.controllo_prezzo == "sempre" or media_stima is None or prezzo is None or prezzo < media_stima)
            and (r.prodotto or identificazione.get("confidenza") in ("alta", "media"))
        )
        if conviene_cercare:
            prezzi = archivio.cache_codice(chiave, opz.giorni_cache)
            if prezzi is None:
                prezzi = ai.cerca_prezzi(nome, annuncio)
                if prezzi.get("usato_min_eur") or prezzi.get("nuovo_eur"):
                    archivio.salva_cache_codice(chiave, prezzi)
            else:
                log.info("Prezzi di '%s' presi dalla cache", nome)
            fonte = "ricerca"
        if not (prezzi and (prezzi.get("usato_min_eur") or prezzi.get("nuovo_eur"))) and stima_min and stima_max:
            prezzi, fonte = {"usato_min_eur": stima_min, "usato_max_eur": stima_max, "nuovo_eur": None, "fonti": []}, "stima"
    r.prezzi, r.fonte_prezzi = prezzi, fonte
    r.giudizio_prezzo, r.testo_prezzo, r.prezzo_tipico = valuta_prezzo(prezzo, prezzi, fonte)


def _valutazione_visiva(base_esito: str, motivo: str, lettura: dict, declassa: bool = False) -> tuple[str, str]:
    """Senza un codice utilizzabile: l'esito viene dalla valutazione delle foto."""
    orig = lettura.get("originalita") or {}
    esito = GIUDIZIO_VISIVO.get(orig.get("giudizio"))
    if esito is None:
        return base_esito, motivo  # non valutabile dalle foto
    if declassa and esito == "visivo_originale":
        esito = "visivo_dubbia"
    return esito, motivo


def analizza_annuncio(annuncio: dict, foto: list[tuple[bytes, str]], ai: AnalistaAI,
                      archivio: Archivio, opzioni: OpzioniAnalisi | None = None) -> Risultato:
    opz = opzioni or OpzioniAnalisi()
    r = _analizza_originalita(annuncio, foto, ai, archivio, opz)
    _aggiungi_prezzo(r, annuncio, ai, archivio, opz)
    return r


def _analizza_originalita(annuncio: dict, foto: list[tuple[bytes, str]], ai: AnalistaAI,
                          archivio: Archivio, opz: OpzioniAnalisi) -> Risultato:
    if not foto:
        return Risultato("nessuna_etichetta", "nessuna foto scaricabile")

    lettura = ai.leggi_etichetta(foto, annuncio)
    n = len(foto)
    tipo = lettura.get("tipo_articolo") or "maglia_da_calcio"
    if opz.solo_maglie_da_calcio and tipo != "maglia_da_calcio":
        return Risultato("non_maglia", f"sembra {tipo.replace('_', ' ')}", lettura=lettura, foto_analizzate=n)

    if not lettura.get("etichetta_trovata"):
        esito, motivo = _valutazione_visiva("nessuna_etichetta", "nessuna foto dell'etichetta con i codici", lettura)
        return Risultato(esito, motivo, lettura=lettura, foto_analizzate=n)

    marca = lettura.get("marca") or annuncio.get("marca")
    cs = normalizza_codice_stile(lettura.get("codice_stile"), marca)
    up = normalizza_upc(lettura.get("upc"))
    base = dict(codice_stile=cs.codice, upc=up.codice, taglia_etichetta=lettura.get("taglia_etichetta"),
                lettura=lettura, foto_analizzate=n)
    nota_upc = f" UPC: {up.motivo}." if lettura.get("upc") and not up.valido else ""

    if cs.codice is None or "?" in (cs.codice or ""):
        parziale = f" (letto solo in parte: {cs.codice})" if cs.codice else ""
        esito, motivo = _valutazione_visiva("illeggibile", f"codice dell'etichetta illeggibile{parziale}" + nota_upc,
                                            lettura)
        return Risultato(esito, motivo, **base)
    if not cs.valido:
        esito, motivo = _valutazione_visiva("codice_anomalo", f"codice in formato anomalo: {cs.motivo}" + nota_upc,
                                            lettura, declassa=True)
        return Risultato(esito, motivo, **base)

    chiave = f"{cs.codice}|{up.codice if up.valido else ''}"
    prodotto = archivio.cache_codice(chiave, opz.giorni_cache)
    if not prodotto_completo(prodotto):  # in cache solo risultati completi (le vecchie voci vuote si rifanno)
        prodotto = ai.cerca_codice(cs.codice, up.codice if up.valido else None, marca,
                                   con_prezzi=opz.controllo_prezzo != "mai")
        if prodotto_completo(prodotto):  # i "non trovato" non li mettiamo in cache: potrebbe andare meglio dopo
            archivio.salva_cache_codice(chiave, prodotto)
    else:
        log.info("Codice %s preso dalla cache", chiave)

    if not prodotto.get("trovato"):
        esito, motivo = _valutazione_visiva(
            "codice_non_trovato", f"codice {cs.codice} non trovato online" + nota_upc, lettura)
        return Risultato(esito, motivo, prodotto=prodotto, **base)
    if not prodotto_completo(prodotto):
        return Risultato("incerto", "il codice esiste online ma la ricerca non ha detto a che maglia corrisponde"
                         + nota_upc, prodotto=prodotto, **base)

    # le foto della maglia (non quella dell'etichetta), per il confronto visivo
    indice_etichetta = lettura.get("indice_foto")
    foto_maglia = [f for i, f in enumerate(foto, start=1) if i != indice_etichetta][:MAX_FOTO_CONFRONTO]
    verdetto = ai.confronta(annuncio, {**lettura, "codice_stile": cs.codice}, prodotto, foto_maglia)
    motivo = verdetto.get("motivo", "")
    if verdetto.get("taglia_coerente") is False:
        motivo += " Taglie non coerenti."
    if prodotto.get("upc_coerente") is False:
        motivo += " Il codice a barre risulta di un altro prodotto."
    esito = verdetto.get("esito", "incerto")
    # Un codice a barre di un altro prodotto è un campanello d'allarme: niente "corrisponde"
    if esito == "corrisponde" and (prodotto.get("upc_coerente") is False or verdetto.get("taglia_coerente") is False):
        esito = "incerto"
    return Risultato(esito, (motivo + nota_upc).strip(), prodotto=prodotto,
                     nota_annuncio=verdetto.get("nota_annuncio") or None, **base)


# --- messaggi Telegram -----------------------------------------------------

def messaggio_nuovo(annuncio: dict, nome_ricerca: str) -> str:
    dettagli = " · ".join(x for x in (annuncio.get("prezzo"), annuncio.get("taglia"), annuncio.get("marca"),
                                       annuncio.get("condizione")) if x)
    return (
        f"🆕 <b>{e(annuncio.get('titolo'))}</b>\n"
        f"{e(dettagli)}\n"
        f"🔎 {e(nome_ricerca)}\n"
        f"<a href=\"{e(annuncio.get('url'))}\">Apri su Vinted</a>\n"
        "⏳ Controllo in corso…"
    )


INTESTAZIONI = {
    "corrisponde": "✅ <b>Etichetta OK</b>",
    "non_corrisponde": "⚠️ <b>Codice di un'altra maglia</b>",
    "incerto": "❔ <b>Da verificare a mano</b>",
    "codice_non_trovato": "❔ <b>Codice non trovato online</b>",
    "codice_anomalo": "⚠️ <b>Codice in formato anomalo</b>",
    "illeggibile": "❔ <b>Etichetta presente, codice illeggibile</b>",
    "nessuna_etichetta": "➖ <b>Nessuna foto dell'etichetta</b>",
    "visivo_originale": "🟢 <b>Probabile originale</b> (valutata dalle foto, senza codice)",
    "visivo_dubbia": "❔ <b>Originalità dubbia</b> (valutata dalle foto)",
    "visivo_falso": "⚠️ <b>Probabile falso</b> (valutata dalle foto)",
    "non_maglia": "➖ <b>Non è una maglia da gara</b>",
}
ESITI_POSITIVI = {"corrisponde", "visivo_originale"}


def messaggio_esito(r: Risultato, annuncio: dict, con_intestazione_annuncio: bool) -> str:
    righe = []
    if r.giudizio_prezzo == "sotto" and r.esito in ESITI_POSITIVI:
        righe.append("🔥 <b>Costa meno del solito</b>")
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
    elif r.nome_identificato and r.esito != "non_maglia":
        confidenza = (r.lettura.get("identificazione") or {}).get("confidenza")
        righe.append(f"Sembra: {e(r.nome_identificato)}" + (f" (confidenza {e(confidenza)})" if confidenza else ""))
    if r.motivo:
        righe.append(e(r.motivo))
    if r.esito.startswith("visivo_"):
        orig = r.lettura.get("originalita") or {}
        for indizio in (orig.get("indizi_originale") or [])[:3]:
            righe.append(f"✔️ {e(indizio)}")
        for indizio in (orig.get("indizi_falso") or [])[:3]:
            righe.append(f"✖️ {e(indizio)}")
    if r.nota_annuncio:
        righe.append(f"📝 {e(r.nota_annuncio)}")
    if r.testo_prezzo:
        righe.append(e(r.testo_prezzo))
    fonti = (r.prodotto.get("fonti") or []) if r.prodotto else []
    if fonti:
        righe.append(f"<a href=\"{e(fonti[0])}\">Fonte</a>")
    fonti_prezzi = (r.prezzi or {}).get("fonti") or []
    if fonti_prezzi and r.fonte_prezzi == "ricerca" and (not fonti or fonti_prezzi[0] != fonti[0]):
        righe.append(f"<a href=\"{e(fonti_prezzi[0])}\">Fonte prezzi</a>")
    if r.esito in ESITI_POSITIVI:
        righe.append("📋 Aggiunta alla lista")
    return "\n".join(righe)
