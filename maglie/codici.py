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
    # Puma: 763432-01
    "puma": [re.compile(r"^\d{6}-\d{2}$")],
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

    # Puma a volte è stampato senza separatore: 76343201 -> 763432-01
    if m == "puma" and re.fullmatch(r"\d{8}", c):
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
