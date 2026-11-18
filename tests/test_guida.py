"""Guida r/KitSwap: schede per marca e controlli oggettivi."""

import pytest

from maglie.analisi import analizza_annuncio, controlli_guida, messaggio_esito
from maglie.codici import normalizza_codice_stile
from maglie.db import Archivio
from maglie.guida import ESEMPI, MARCHE, scheda_marca
from maglie.guida import testo_guida as guida_per
from tests.test_prezzo_visivo import FOTO, annuncio, lettura
from tests.test_servizio import AIFinta


@pytest.fixture
def archivio(tmp_path):
    return Archivio(tmp_path / "t.db")


def test_scheda_per_marca():
    assert scheda_marca("Nike")[0] == "nike"
    assert scheda_marca("adidas Originals")[0] == "adidas"
    assert scheda_marca("New Balance")[0] == "new balance"
    assert scheda_marca("Umbro") == (None, None)
    assert scheda_marca(None) == (None, None)


def test_testo_guida_contiene_solo_la_marca_giusta():
    nike = guida_per("Nike")
    assert "contatore di produzione" in nike.lower() and "Regole generali" in nike and "Maglie vecchie" in nike
    assert "spalla sinistra" not in nike  # niente scheda adidas
    assert "indicazioni specifiche" in guida_per("Umbro")
    # resta corto: la scheda più lunga + generale + vecchie sotto ~1.000 token (≈4 caratteri a token)
    assert max(len(guida_per(m)) for m in MARCHE) < 4000


def test_esempi_hanno_link_imgur():
    assert all(u.startswith("https://") and "imgur.com" in u for g in ESEMPI.values() for u in g.values())


def test_puma_colore_a_tre_cifre():
    assert normalizza_codice_stile("763432-001", "puma").valido
    assert normalizza_codice_stile("763432001", "puma").codice == "763432-001"


def test_controlli_guida():
    forti, deboli = controlli_guida({"codice_cartellino": "CV8413-100", "taglie_coerenti": False,
                                     "contatore_produzione": 487_301}, "Nike", "CV8413-101")
    assert len(forti) == 2 and "CV8413-100" in forti[0] and "taglia" in forti[1]
    assert deboli and "487.301" in deboli[0]
    assert controlli_guida({"codice_cartellino": "CV8413-101", "taglie_coerenti": True,
                            "contatore_produzione": 2_345_678}, "Nike", "CV8413-101") == ([], [])
    # contatore basso conta solo per Nike e Puma
    assert controlli_guida({"contatore_produzione": 1000}, "adidas", None) == ([], [])


def test_indizio_forte_impedisce_probabile_originale(archivio):
    ai = AIFinta(lettura=lettura(taglie_coerenti=False))
    r = analizza_annuncio(annuncio(), FOTO, ai, archivio)
    assert r.esito == "visivo_dubbia"
    assert "✖️ taglia diversa tra le etichette" in messaggio_esito(r, annuncio(), False)


def test_cartellino_diverso_nel_percorso_codice(archivio):
    ai = AIFinta(lettura=lettura(etichetta_trovata=True, codice_stile="CV8413-101", upc="194502132830",
                                 indice_foto=3, codice_cartellino="CV8413-100"))
    r = analizza_annuncio(annuncio(), FOTO, ai, archivio)
    assert r.esito == "incerto" and "cartellino" in r.motivo
    assert "✖️ codice del cartellino" in messaggio_esito(r, annuncio(), False)


def test_la_guida_della_marca_finisce_nel_prompt():
    from types import SimpleNamespace as NS

    from tests.test_ai import ai_con, tool

    ai = ai_con([NS(content=[tool("riporta_etichetta", {"tipo_articolo": "maglia_da_calcio"})], stop_reason="tool_use")])
    ai.leggi_etichetta([(b"a", "image/jpeg")], {"marca": "Puma", "taglia": "L", "condizione": "Ottime"})
    testo = ai.client.messages.chiamate[0]["messages"][0]["content"][-1]["text"]
    assert "Specifico per puma" in testo and "numero di produzione" in testo.lower()
    schema = ai.client.messages.chiamate[0]["tools"][0]["input_schema"]
    assert {"codice_cartellino", "taglie_coerenti", "contatore_produzione"} <= set(schema["required"])
