"""Normalizzazione e validazione dei codici letti dall'etichetta.

Serve a scartare le letture sbagliate del modello di visione PRIMA di spendere
una ricerca web, e a segnalare codici in formato anomalo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Formati noti dei codici articolo (style code) per marca.
FORMATI_STILE: dict[str, list[re.Pattern]] = {
    # Nike: CV8413-101 (attuale), 776929-100 (più vecchio)
    "nike": [re.compile(r"^[A-Z]{2}\d{4}-\d{3}$"), re.compile(r"^\d{6}-\d{3}$")],
    # adidas: GM4614, H35917, IA7224
    "adidas": [re.compile(r"^[A-Z]{1,2}\d{4,5}$")],
    # Puma: 763432-01 (colore a 2 o 3 cifre)
    "puma": [re.compile(r"^\d{6}-\d{2,3}$")],
}
FORMATO_GENERICO = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,14}$")


@dataclass
class CodiceVerificato:
    valido: bool
    codice: str | None
    motivo: str = ""


def marca_normalizzata(marca: str | None) -> str | None:
    if not marca:
        return None
    m = marca.strip().lower()
    for nota in FORMATI_STILE:
        if nota in m:
            return nota
    return m


def normalizza_codice_stile(codice: str | None, marca: str | None) -> CodiceVerificato:
    if not codice:
        return CodiceVerificato(False, None, "codice non letto")
    c = codice.upper().strip()
    c = re.sub(r"[‐‑‒–—_ ]", "-", c)   # trattini "strani" e spazi -> '-'
    c = re.sub(r"-+", "-", c).strip("-")
    m = marca_normalizzata(marca)
    if "?" in c:
        return CodiceVerificato(False, c, "codice letto solo in parte")

    # Puma a volte è stampato senza separatore: 76343201 -> 763432-01
    if m == "puma" and re.fullmatch(r"\d{8,9}", c):
        c = f"{c[:6]}-{c[6:]}"
    # Nike senza trattino: CV8413101 -> CV8413-101
    if m == "nike" and re.fullmatch(r"[A-Z]{2}\d{7}", c):
        c = f"{c[:6]}-{c[6:]}"

    if m in FORMATI_STILE:
        if any(p.fullmatch(c) for p in FORMATI_STILE[m]):
            return CodiceVerificato(True, c)
        return CodiceVerificato(False, c, f"formato non tipico per {m}")
    if FORMATO_GENERICO.fullmatch(c):
        return CodiceVerificato(True, c)
    return CodiceVerificato(False, c, "formato non riconosciuto")


def gtin_valido(cifre: str) -> bool:
    """Controlla la cifra di controllo di un UPC-A (12) o EAN-13 (13)."""
    if not re.fullmatch(r"\d{12,13}", cifre):
        return False
    corpo, controllo = cifre[:-1], int(cifre[-1])
    totale = 0
    # Da destra: pesi 3,1,3,1... a partire dalla cifra accanto al controllo
    for i, ch in enumerate(reversed(corpo)):
        totale += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - totale % 10) % 10 == controllo


def normalizza_upc(upc: str | None) -> CodiceVerificato:
    if not upc:
        return CodiceVerificato(False, None, "UPC non letto")
    c = re.sub(r"\D", "", upc)
    if len(c) not in (12, 13):
        return CodiceVerificato(False, c or None, f"UPC di {len(c)} cifre invece di 12/13")
    if not gtin_valido(c):
        return CodiceVerificato(False, c, "cifra di controllo UPC sbagliata (lettura errata o etichetta falsa)")
    return CodiceVerificato(True, c)


# --- colore nel codice Nike ---------------------------------------------------
# Nei codici Nike le 3 cifre finali sono il colore e la prima indica la famiglia (CV8413-101 = bianco,
# DX2763-409 = blu). Un falso che riusa il codice di un'altra maglia spesso ha la famiglia sbagliata:
# es. DM1840-452 (Barcellona home, blu) cucito su una Inter away bianca.
# Gli insiemi sono larghi apposta: meglio non segnalare un caso dubbio che segnalare un originale.
COLORI_BASE = ["nero", "grigio", "bianco", "beige", "marrone", "oro", "giallo", "arancione", "rosso", "bordeaux",
               "rosa", "viola", "verde", "verde_acqua", "azzurro", "blu", "blu_scuro", "multicolore"]

FAMIGLIE_COLORE_NIKE: dict[str, tuple[str, set[str]]] = {
    "0": ("nero/grigio", {"nero", "grigio"}),
    "1": ("bianco", {"bianco", "beige", "grigio"}),
    "2": ("marrone/beige", {"marrone", "beige", "oro"}),
    "3": ("verde", {"verde", "verde_acqua"}),
    "4": ("blu", {"blu", "blu_scuro", "azzurro", "verde_acqua"}),
    "5": ("viola", {"viola", "bordeaux"}),
    "6": ("rosso/rosa", {"rosso", "bordeaux", "rosa", "arancione"}),
    "7": ("giallo/oro", {"giallo", "oro", "arancione"}),
    "8": ("arancione", {"arancione"}),
    # "9" = multicolore: non si controlla
}


def colore_codice_nike(codice: str | None) -> tuple[str, str] | None:
    """(suffisso colore, famiglia) di un codice Nike, anche se letto solo in parte (DM18?0-452).

    None se il suffisso non è leggibile o la famiglia non è controllabile.
    """
    if not codice:
        return None
    c = re.sub(r"[‐‑‒–—_ ]", "-", codice.upper().strip())
    m = re.search(r"(?:^|[A-Z0-9?])-(\d{3})$", c) or re.fullmatch(r"[A-Z]{2}\d{4}(\d{3})", c)
    if not m:
        return None
    suffisso = m.group(1)
    if suffisso[0] not in FAMIGLIE_COLORE_NIKE:
        return None
    return suffisso, suffisso[0]


def incoerenza_colore_nike(codice: str | None, marca: str | None, colori_maglia: list | None) -> str | None:
    """Testo dell'indizio se la famiglia di colore del codice non c'entra con il colore base della maglia."""
    if marca_normalizzata(marca) != "nike" or not colori_maglia:
        return None
    colori = [str(x).strip().lower().replace(" ", "_") for x in colori_maglia if x]
    if not colori or "multicolore" in colori or any(x not in COLORI_BASE for x in colori):
        return None
    cc = colore_codice_nike(codice)
    if cc is None:
        return None
    suffisso, cifra = cc
    nome, ammessi = FAMIGLIE_COLORE_NIKE[cifra]
    if ammessi & set(colori):
        return None
    visti = " e ".join(x.replace("_", " ") for x in colori)
    return (f"il codice finisce con -{suffisso} (colore {nome}) ma il colore base della maglia è {visti}: "
            "probabile codice di un'altra maglia")
