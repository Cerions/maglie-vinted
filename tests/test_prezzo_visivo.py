"""Valutazione dalle foto (senza codice) e confronto di prezzo."""

import pytest

from maglie.analisi import OpzioniAnalisi, analizza_annuncio, messaggio_esito, prezzo_annuncio_eur, valuta_prezzo
from maglie.db import Archivio
from tests.test_servizio import AIFinta, annuncio_raw

FOTO = [(b"1", "image/jpeg"), (b"2", "image/jpeg"), (b"3", "image/jpeg")]


def lettura(**extra):
    base = {"tipo_articolo": "maglia_da_calcio", "etichetta_trovata": False, "leggibilita": "scarsa",
            "descrizione_maglia": "maglia nerazzurra", "marca": "nike",
            "identificazione": {"squadra": "Inter", "stagione": "2009/10", "tipo_kit": "home",
                                "versione": "replica", "confidenza": "alta"},
            "originalita": {"giudizio": "probabile_originale",
                            "indizi_originale": ["stemma ricamato pulito", "etichette Nike dell'epoca"],
                            "indizi_falso": []},
            "stima_prezzo_usato_eur": {"min": 50, "max": 80}}
    return base | extra


@pytest.fixture
def archivio(tmp_path):
    return Archivio(tmp_path / "t.db")


def annuncio(prezzo="25.0 EUR"):
    return annuncio_raw(1) | {"prezzo": prezzo, "condizione": "Ottime"}


# --- prezzo ---------------------------------------------------------------

def test_prezzo_annuncio():
    assert prezzo_annuncio_eur({"prezzo": "25.0 EUR"}) == 25.0
    assert prezzo_annuncio_eur({"prezzo": "12,50 EUR"}) == 12.5
    assert prezzo_annuncio_eur({"prezzo": "30 GBP"}) is None
    assert prezzo_annuncio_eur({"prezzo": ""}) is None


def test_valuta_prezzo_tre_fasce():
    prezzi = {"usato_min_eur": 40, "usato_max_eur": 60, "nuovo_eur": 90}
    g, testo, tipico = valuta_prezzo(20, prezzi, "ricerca")
    assert g == "sotto" and "60% in meno" in testo and "🔥" in testo and tipico == "40 €–60 €"
    assert valuta_prezzo(50, prezzi, "ricerca")[0] == "media"
    g, testo, _ = valuta_prezzo(75, prezzi, "ricerca")
    assert g == "sopra" and "50% in più" in testo
    assert "stima, senza ricerca" in valuta_prezzo(20, prezzi, "stima")[1]


def test_valuta_prezzo_solo_nuovo_o_niente():
    g, testo, _ = valuta_prezzo(30, {"usato_min_eur": None, "usato_max_eur": None, "nuovo_eur": 100}, "ricerca")
    assert g == "indicativo" and "30% del prezzo da nuova" in testo
    assert valuta_prezzo(30, None, None) == (None, "", None)
    assert valuta_prezzo(None, {"usato_min_eur": 1, "usato_max_eur": 2}, "ricerca")[0] is None


# --- valutazione visiva -----------------------------------------------------

def test_senza_etichetta_valutazione_dalle_foto_e_prezzo_cercato(archivio):
    ai = AIFinta(lettura=lettura())
    r = analizza_annuncio(annuncio("25.0 EUR"), FOTO, ai, archivio)
    assert r.esito == "visivo_originale"
    assert ai.chiamate["cerca"] == 0 and ai.chiamate["prezzi"] == 1  # 25 € < stima max 80 € -> vale la pena
    assert r.giudizio_prezzo == "sotto" and r.fonte_prezzi == "ricerca"
    msg = messaggio_esito(r, annuncio(), con_intestazione_annuncio=False)
    assert msg.startswith("🔥 <b>Costa meno del solito</b>")
    assert "🟢" in msg and "✔️ stemma ricamato pulito" in msg and "Sembra: Inter · 2009/10" in msg
    assert "📋 Aggiunta alla lista" in msg


def test_prezzo_sopra_la_stima_niente_ricerca(archivio):
    ai = AIFinta(lettura=lettura())
    r = analizza_annuncio(annuncio("120.0 EUR"), FOTO, ai, archivio)
    assert ai.chiamate["prezzi"] == 0
    assert r.fonte_prezzi == "stima" and r.giudizio_prezzo == "sopra"
    assert "stima, senza ricerca" in r.testo_prezzo


def test_controllo_prezzo_sempre_e_mai(archivio):
    ai = AIFinta(lettura=lettura())
    analizza_annuncio(annuncio("120.0 EUR"), FOTO, ai, archivio, OpzioniAnalisi(controllo_prezzo="sempre"))
    assert ai.chiamate["prezzi"] == 1
    ai2 = AIFinta(lettura=lettura())
    r = analizza_annuncio(annuncio("25.0 EUR"), FOTO, ai2, archivio, OpzioniAnalisi(controllo_prezzo="mai"))
    assert ai2.chiamate["prezzi"] == 0 and r.testo_prezzo == ""


def test_prezzi_in_cache_per_la_stessa_maglia(archivio):
    ai = AIFinta(lettura=lettura())
    analizza_annuncio(annuncio(), FOTO, ai, archivio)
    analizza_annuncio(annuncio(), FOTO, ai, archivio)
    assert ai.chiamate["prezzi"] == 1


def test_identificazione_incerta_niente_ricerca_prezzi(archivio):
    ident = lettura()["identificazione"] | {"confidenza": "bassa"}
    ai = AIFinta(lettura=lettura(identificazione=ident))
    r = analizza_annuncio(annuncio(), FOTO, ai, archivio)
    assert ai.chiamate["prezzi"] == 0 and r.fonte_prezzi == "stima"


def test_probabile_falso_niente_prezzo(archivio):
    orig = {"giudizio": "probabile_falso", "indizi_originale": [], "indizi_falso": ["stemma sbavato"]}
    ai = AIFinta(lettura=lettura(originalita=orig))
    r = analizza_annuncio(annuncio(), FOTO, ai, archivio)
    assert r.esito == "visivo_falso" and ai.chiamate["prezzi"] == 0 and r.testo_prezzo == ""
    assert "✖️ stemma sbavato" in messaggio_esito(r, annuncio(), False)


def test_non_valutabile_resta_nessuna_etichetta(archivio):
    orig = {"giudizio": "non_valutabile", "indizi_originale": [], "indizi_falso": []}
    r = analizza_annuncio(annuncio(), FOTO, AIFinta(lettura=lettura(originalita=orig)), archivio)
    assert r.esito == "nessuna_etichetta"


def test_codice_letto_in_parte_va_alla_valutazione_visiva(archivio):
    ai = AIFinta(lettura=lettura(etichetta_trovata=True, codice_stile="CV84?3-101", indice_foto=3))
    r = analizza_annuncio(annuncio(), FOTO, ai, archivio)
    assert r.esito == "visivo_originale" and "CV84?3-101" in r.motivo
    assert ai.chiamate["cerca"] == 0


def test_codice_anomalo_mai_piu_che_dubbia(archivio):
    ai = AIFinta(lettura=lettura(etichetta_trovata=True, codice_stile="CV84X3-1O1", indice_foto=3))
    r = analizza_annuncio(annuncio(), FOTO, ai, archivio)
    assert r.esito == "visivo_dubbia" and "formato anomalo" in r.motivo


def test_codice_non_trovato_usa_le_foto(archivio):
    ai = AIFinta(lettura=lettura(etichetta_trovata=True, codice_stile="CV8413-101", upc=None, indice_foto=3),
                 prodotto={"trovato": False, "fonti": []})
    r = analizza_annuncio(annuncio(), FOTO, ai, archivio)
    assert r.esito == "visivo_originale" and "non trovato online" in r.motivo


def test_non_maglia_saltata_senza_spese(archivio):
    ai = AIFinta(lettura=lettura(tipo_articolo="felpa_o_giacca"))
    r = analizza_annuncio(annuncio(), FOTO, ai, archivio)
    assert r.esito == "non_maglia" and ai.chiamate == {"leggi": 1, "cerca": 0, "confronta": 0, "prezzi": 0}
    r2 = analizza_annuncio(annuncio(), FOTO, AIFinta(lettura=lettura(tipo_articolo="felpa_o_giacca")), archivio,
                           OpzioniAnalisi(solo_maglie_da_calcio=False))
    assert r2.esito == "visivo_originale"


def test_percorso_codice_usa_i_prezzi_della_ricerca_codice(archivio):
    prodotto = AIFinta().prodotto | {"prezzi": {"usato_min_eur": 30, "usato_max_eur": 45, "nuovo_eur": 90,
                                               "fonti": ["https://p.it"], "note": None}}
    ai = AIFinta(lettura=lettura(etichetta_trovata=True, codice_stile="CV8413-101", upc="194502132830",
                                 indice_foto=3), prodotto=prodotto)
    r = analizza_annuncio(annuncio("25.0 EUR"), FOTO, ai, archivio)
    assert r.esito == "corrisponde" and ai.chiamate["prezzi"] == 0
    assert r.giudizio_prezzo == "sotto" and r.prezzo_tipico == "30 €–45 €"


def test_cache_codice_scade(archivio):
    archivio.salva_cache_codice("k", {"a": 1})
    assert archivio.cache_codice("k", 7) == {"a": 1}
    archivio.conn.execute("UPDATE cache_codici SET il='2026-01-01T00:00:00+00:00'")
    assert archivio.cache_codice("k", 7) is None
    assert archivio.cache_codice("k") == {"a": 1}  # senza scadenza


def test_dubbia_niente_ricerca_prezzi(archivio):
    orig = {"giudizio": "dubbia", "indizi_originale": [], "indizi_falso": []}
    ai = AIFinta(lettura=lettura(originalita=orig))
    r = analizza_annuncio(annuncio("10.0 EUR"), FOTO, ai, archivio, OpzioniAnalisi(controllo_prezzo="sempre"))
    assert r.esito == "visivo_dubbia" and ai.chiamate["prezzi"] == 0 and r.testo_prezzo == ""
