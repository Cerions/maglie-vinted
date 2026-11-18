"""Scanner e analizzatore con Vinted, Claude e Telegram finti."""

import threading
import time

import pytest

from maglie.ai import Consumo
from maglie.config import Config, Ricerca
from maglie.db import Archivio
from maglie.servizio import Analizzatore, Scanner, attesa_backoff
from maglie.vinted import VintedErrore


class TelegramFinto:
    attivo = True

    def __init__(self):
        self.messaggi = []

    def invia(self, testo, *, foto=None, in_risposta_a=None):
        self.messaggi.append({"testo": testo, "foto": foto, "risposta_a": in_risposta_a})
        return 1000 + len(self.messaggi)


class ClientFinto:
    def __init__(self):
        self.risultati = []
        self.errore = None
        self.richieste_pagina = 0
        self.sessioni_nuove = 0

    def cerca(self, params):
        if self.errore:
            raise self.errore
        return list(self.risultati)

    def nuova_sessione(self):
        self.sessioni_nuove += 1

    def pagina_annuncio(self, id_annuncio):
        self.richieste_pagina += 1
        if self.errore:
            raise self.errore
        return ('<meta property="og:description" content="Maglia away 2020/21">'
                f'<img src="https://images1.vinted.net/t/A/f800/{id_annuncio}1.jpeg?s=1">'
                f'<img src="https://images1.vinted.net/t/B/f800/{id_annuncio}2.jpeg?s=2">')

    @staticmethod
    def scarica_foto(url):
        return b"jpeg", "image/jpeg"


class AIFinta:
    def __init__(self, lettura=None, prodotto=None, verdetto=None):
        self.lettura = lettura or {"etichetta_trovata": True, "indice_foto": 2, "marca": "nike",
                                   "codice_stile": "CV8413-101", "upc": "194502132830",
                                   "taglia_etichetta": "L", "leggibilita": "buona",
                                   "descrizione_maglia": "maglia azzurra con dettagli gialli"}
        self.prodotto = prodotto or {"trovato": True, "nome_prodotto": "Maglia away", "squadra": "Squadra X",
                                     "stagione": "2020/21", "tipo_kit": "away", "upc_coerente": True,
                                     "fonti": ["https://esempio.it/cv8413"]}
        self.verdetto = verdetto or {"esito": "corrisponde", "taglia_coerente": True, "motivo": "tutto coerente"}
        self.chiamate = {"leggi": 0, "cerca": 0, "confronta": 0, "prezzi": 0}
        self.prezzi = {"usato_min_eur": 40, "usato_max_eur": 60, "nuovo_eur": 90, "fonti": ["https://prezzi.it"], "note": None}
        self.consumo = Consumo()
        self.errore_lettura = None

    def prendi_consumo(self):
        c, self.consumo = self.consumo, Consumo()
        return c

    def _spendi(self, t_in, t_out, ricerche):
        self.consumo.token_in += t_in
        self.consumo.token_out += t_out
        self.consumo.ricerche_web += ricerche
        self.consumo.costo += t_in / 1e6 * 0.10 + t_out / 1e6 * 0.50 + ricerche * 0.01

    def leggi_etichetta(self, foto, annuncio):
        self.chiamate["leggi"] += 1
        self._spendi(8000, 300, 0)
        if self.errore_lettura:
            raise self.errore_lettura
        self.ultime_foto = foto
        return dict(self.lettura)

    def cerca_codice(self, codice, upc, marca, con_prezzi=True):
        self.chiamate["cerca"] += 1
        self._spendi(20000, 500, 2)
        return dict(self.prodotto)

    def cerca_prezzi(self, maglia, annuncio):
        self.chiamate["prezzi"] += 1
        self.ultima_maglia_prezzi = maglia
        self._spendi(15000, 300, 2)
        return dict(self.prezzi)

    def confronta(self, annuncio, lettura, prodotto, foto_maglia=None):
        self.chiamate["confronta"] += 1
        self.ultime_foto_confronto = foto_maglia
        self._spendi(1500, 200, 0)
        self.ultimo_annuncio = annuncio
        return dict(self.verdetto)


def annuncio_raw(id_, minuti_fa=1):
    ts = time.time() - minuti_fa * 60 if minuti_fa is not None else None
    return {"id": id_, "titolo": f"Maglia {id_}", "descrizione": "", "prezzo": "25.0 EUR", "taglia": "L",
            "marca": "Nike", "url": f"https://www.vinted.it/items/{id_}",
            "foto_principale": f"https://images1.vinted.net/t/A/f800/{id_}1.jpeg?s=1",
            "timestamp": ts, "promosso": False}


@pytest.fixture
def ambiente(tmp_path):
    cfg = Config()
    cfg.percorso_db = tmp_path / "test.db"
    cfg.ricerche = [Ricerca("Test", "maglia calcio")]
    client, tg = ClientFinto(), TelegramFinto()
    stop, sveglia = threading.Event(), threading.Event()
    archivio = Archivio(cfg.percorso_db)
    scanner = Scanner(cfg, client, tg, stop, sveglia)
    return cfg, client, tg, stop, sveglia, archivio, scanner


def test_primo_giro_baseline_senza_avvisi(ambiente):
    cfg, client, tg, _, sveglia, archivio, scanner = ambiente
    client.risultati = [annuncio_raw(1), annuncio_raw(2)]
    assert scanner.giro_ricerca(archivio, cfg.ricerche[0]) == 0
    assert tg.messaggi == []
    assert archivio.annuncio(1)["stato"] == "baseline"
    assert not sveglia.is_set()


def test_annuncio_nuovo_avviso_immediato_e_coda(ambiente):
    cfg, client, tg, _, sveglia, archivio, scanner = ambiente
    client.risultati = [annuncio_raw(1)]
    scanner.giro_ricerca(archivio, cfg.ricerche[0])
    client.risultati = [annuncio_raw(3), annuncio_raw(2), annuncio_raw(1), annuncio_raw(4, minuti_fa=300)]
    assert scanner.giro_ricerca(archivio, cfg.ricerche[0]) == 2
    assert [m["testo"].split("\n")[0] for m in tg.messaggi] == ["🆕 <b>Maglia 2</b>", "🆕 <b>Maglia 3</b>"]
    assert archivio.annuncio(2)["stato"] == "in_coda" and archivio.annuncio(2)["tg_message_id"] == 1001
    assert archivio.annuncio(4)["stato"] == "vecchio"
    assert sveglia.is_set()
    # al giro dopo non li riavvisa
    assert scanner.giro_ricerca(archivio, cfg.ricerche[0]) == 0


def test_backoff_cresce_e_ha_un_tetto(ambiente):
    cfg = ambiente[0]
    attese = [attesa_backoff(cfg, n, bloccato=True) / 60 for n in (1, 2, 3, 10)]
    assert 4 <= attese[0] <= 6 and 8 <= attese[1] <= 12 and 16 <= attese[2] <= 24
    assert attese[3] <= 60 * 1.2
    assert attesa_backoff(cfg, 1, bloccato=False) / 60 <= 1.2


def test_scanner_in_blocco_cambia_sessione_e_avvisa(ambiente):
    cfg, client, tg, stop, _, archivio, scanner = ambiente
    client.errore = VintedErrore("error code: 403", 403)
    cfg.vinted.backoff_blocco_minuti = 0.0005  # test veloce
    cfg.vinted.backoff_max_minuti = 0.001
    t = threading.Thread(target=scanner.esegui)
    t.start()
    for _ in range(100):
        if scanner.fallimenti >= 3:
            break
        time.sleep(0.05)
    stop.set()
    t.join(5)
    assert scanner.fallimenti >= 3
    assert client.sessioni_nuove >= 3
    assert any("🔴" in m["testo"] for m in tg.messaggi)


def _analizzatore(ambiente, ai):
    cfg, client, tg, stop, sveglia, archivio, scanner = ambiente
    client.risultati = []
    scanner.giro_ricerca(archivio, cfg.ricerche[0])  # baseline vuota
    client.risultati = [annuncio_raw(10)]
    scanner.giro_ricerca(archivio, cfg.ricerche[0])
    return Analizzatore(cfg, client, ai, tg, stop, sveglia)


def test_analisi_completa_corrisponde_e_risponde_al_primo_avviso(ambiente):
    _, client, tg, *_, archivio, _ = ambiente
    ai = AIFinta()
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    r = archivio.annuncio(10)
    assert r["stato"] == "analizzato" and r["esito"] == "corrisponde"
    assert r["codice_stile"] == "CV8413-101" and r["upc"] == "194502132830"
    assert len(ai.ultime_foto) == 2
    assert ai.ultimo_annuncio["descrizione"] == "Maglia away 2020/21"  # presa dalla pagina
    risposta = tg.messaggi[-1]
    assert risposta["risposta_a"] == 1001 and "✅" in risposta["testo"] and "📋" in risposta["testo"]
    assert [x["esito"] for x in archivio.lista()] == ["corrisponde"]


def test_cache_del_codice(ambiente):
    cfg, client, tg, _, _, archivio, scanner = ambiente
    ai = AIFinta()
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    client.risultati = [annuncio_raw(11), annuncio_raw(10)]
    scanner.giro_ricerca(archivio, cfg.ricerche[0])
    an.elabora(archivio, archivio.prossimo_in_coda())
    assert ai.chiamate == {"leggi": 2, "cerca": 1, "confronta": 2, "prezzi": 1}  # prezzi: poi dalla cache


def test_nessuna_etichetta(ambiente):
    archivio = ambiente[5]
    ai = AIFinta(lettura={"etichetta_trovata": False, "leggibilita": "scarsa", "descrizione_maglia": "x"})
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    assert archivio.annuncio(10)["esito"] == "nessuna_etichetta"
    assert ai.chiamate["cerca"] == 0
    assert "➖" in ambiente[2].messaggi[-1]["testo"]


def test_codice_anomalo_non_spende_ricerche(ambiente):
    archivio = ambiente[5]
    lettura = AIFinta().lettura | {"codice_stile": "CV84X3-1O1"}
    ai = AIFinta(lettura=lettura)
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    assert archivio.annuncio(10)["esito"] == "codice_anomalo"
    assert ai.chiamate["cerca"] == 0


def test_upc_di_altro_prodotto_declassa_a_incerto(ambiente):
    archivio = ambiente[5]
    prodotto = AIFinta().prodotto | {"upc_coerente": False}
    an = _analizzatore(ambiente, AIFinta(prodotto=prodotto))
    an.elabora(archivio, archivio.prossimo_in_coda())
    r = archivio.annuncio(10)
    assert r["esito"] == "incerto" and "codice a barre" in r["motivo"]


def test_errore_vinted_ritenta_poi_si_arrende(ambiente):
    _, client, tg, stop, *_ = ambiente
    archivio = ambiente[5]
    an = _analizzatore(ambiente, AIFinta())
    client.errore = VintedErrore("HTTP 500", 500)
    stop.set()  # così stop.wait() non aspetta davvero
    for _ in range(3):
        riga = archivio.prossimo_in_coda()
        an.elabora(archivio, riga)
    assert archivio.annuncio(10)["stato"] == "errore"
    assert archivio.prossimo_in_coda() is None
    assert "❌" in tg.messaggi[-1]["testo"]


def test_limite_giornaliero(ambiente):
    cfg, *_ = ambiente
    archivio = ambiente[5]
    cfg.analisi.max_analisi_al_giorno = 0
    ai = AIFinta()
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    assert archivio.annuncio(10)["stato"] == "saltato"
    assert ai.chiamate["leggi"] == 0


def test_consumo_registrato_per_ogni_analisi(ambiente):
    archivio = ambiente[5]
    an = _analizzatore(ambiente, AIFinta())
    an.elabora(archivio, archivio.prossimo_in_coda())
    righe = archivio.conn.execute("SELECT * FROM consumi").fetchall()
    assert len(righe) == 1
    r = righe[0]
    # lettura + ricerca codice + confronto + ricerca prezzi (il prodotto finto non ha prezzi)
    assert (r["id_annuncio"], r["token_in"], r["token_out"], r["ricerche_web"]) == (10, 44500, 1300, 4)
    assert r["costo"] == pytest.approx(44500 / 1e6 * 0.10 + 1300 / 1e6 * 0.50 + 0.04)
    assert archivio.costo_oggi() == pytest.approx(r["costo"])
    assert archivio.lista()[0]["costo_usd"] == pytest.approx(r["costo"])


def test_consumo_registrato_anche_se_l_analisi_fallisce(ambiente):
    _, _, _, stop, *_ = ambiente
    archivio = ambiente[5]
    ai = AIFinta()
    ai.errore_lettura = RuntimeError("API giù")
    an = _analizzatore(ambiente, ai)
    stop.set()
    an.elabora(archivio, archivio.prossimo_in_coda())
    r = archivio.conn.execute("SELECT * FROM consumi").fetchone()
    assert r["token_in"] == 8000 and archivio.annuncio(10)["stato"] == "in_coda"


def test_nessun_consumo_se_salta_per_limite(ambiente):
    cfg, *_ = ambiente
    archivio = ambiente[5]
    cfg.analisi.max_analisi_al_giorno = 0
    an = _analizzatore(ambiente, AIFinta())
    an.elabora(archivio, archivio.prossimo_in_coda())
    assert archivio.conn.execute("SELECT COUNT(*) AS n FROM consumi").fetchone()["n"] == 0


def test_cache_con_risultato_vuoto_viene_rifatta(ambiente):
    archivio = ambiente[5]
    archivio.salva_cache_codice("CV8413-101|194502132830", {"trovato": True})  # voce rotta di una versione vecchia
    ai = AIFinta()
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    assert ai.chiamate["cerca"] == 1
    assert archivio.cache_codice("CV8413-101|194502132830")["squadra"] == "Squadra X"
    assert archivio.annuncio(10)["esito"] == "corrisponde"


def test_prodotto_senza_dettagli_esito_incerto_e_niente_cache(ambiente):
    archivio = ambiente[5]
    ai = AIFinta(prodotto={"trovato": True, "fonti": []})
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    r = archivio.annuncio(10)
    assert r["esito"] == "incerto" and "non ha detto" in r["motivo"]
    assert ai.chiamate["confronta"] == 0
    assert archivio.cache_codice("CV8413-101|194502132830") is None


def test_annuncio_ripubblicato_con_id_vecchio_ignorato(ambiente):
    cfg, client, tg, _, _, archivio, scanner = ambiente
    cfg.vinted.margine_id_vecchi = 1000
    client.risultati = [annuncio_raw(50_000, minuti_fa=None)]
    scanner.giro_ricerca(archivio, cfg.ricerche[0])  # baseline, max_id = 50.000
    vecchio = annuncio_raw(10_000) | {"timestamp": None}  # ID molto più basso: annuncio di giorni fa
    tardivo = annuncio_raw(49_500) | {"timestamp": None}  # poco più basso: indicizzato in ritardo, va avvisato
    nuovo = annuncio_raw(50_100) | {"timestamp": None}
    client.risultati = [nuovo, vecchio, tardivo]
    assert scanner.giro_ricerca(archivio, cfg.ricerche[0]) == 2
    assert archivio.annuncio(10_000)["stato"] == "vecchio"
    assert archivio.annuncio(49_500)["stato"] == "in_coda"
    assert archivio.stato_ricerca(cfg.ricerche[0].url)["max_id"] == 50_100


def test_dopo_una_pausa_lunga_niente_avvisi_arretrati(ambiente):
    cfg, client, tg, _, _, archivio, scanner = ambiente
    client.risultati = [annuncio_raw(1)]
    scanner.giro_ricerca(archivio, cfg.ricerche[0])
    archivio.conn.execute("UPDATE ricerche_inizializzate SET ultimo_giro='2026-01-01T00:00:00+00:00'")
    archivio.conn.commit()
    client.risultati = [annuncio_raw(2), annuncio_raw(3), annuncio_raw(1)]
    assert scanner.giro_ricerca(archivio, cfg.ricerche[0]) == 0
    assert archivio.annuncio(2)["stato"] == "baseline" and tg.messaggi == []
    client.risultati = [annuncio_raw(4), annuncio_raw(3)]
    assert scanner.giro_ricerca(archivio, cfg.ricerche[0]) == 1  # da qui si riparte normalmente


def test_confronto_riceve_le_foto_della_maglia_senza_etichetta(ambiente):
    archivio = ambiente[5]
    ai = AIFinta()  # etichetta nella foto 2
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    assert len(ai.ultime_foto) == 2 and len(ai.ultime_foto_confronto) == 1


def test_nota_sul_testo_del_venditore_nel_messaggio(ambiente):
    archivio, tg = ambiente[5], ambiente[2]
    ai = AIFinta(verdetto={"esito": "corrisponde", "taglia_coerente": None, "motivo": "foto coerenti",
                           "nota_annuncio": "il venditore la chiama terza maglia, è la quarta"})
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    assert "📝 il venditore la chiama terza maglia" in tg.messaggi[-1]["testo"]
    assert archivio.annuncio(10)["esito"] == "corrisponde"
    assert "Nota: il venditore" in archivio.annuncio(10)["motivo"]


def test_budget_giornaliero_raggiunto(ambiente):
    cfg, *_ = ambiente
    archivio = ambiente[5]
    cfg.analisi.budget_giornaliero_usd = 0.01
    archivio.registra_consumo(1, 0, 0, 1, 0.02)
    ai = AIFinta()
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    assert archivio.annuncio(10)["stato"] == "saltato" and ai.chiamate["leggi"] == 0
    assert "Budget giornaliero" in ambiente[2].messaggi[-1]["testo"]


def test_esito_e_prezzo_salvati_nel_db(ambiente):
    archivio = ambiente[5]
    ai = AIFinta()
    an = _analizzatore(ambiente, ai)
    an.elabora(archivio, archivio.prossimo_in_coda())
    r = archivio.annuncio(10)
    assert r["giudizio_prezzo"] == "sotto" and r["prezzo_tipico"] == "40 €–60 €"  # 25 € contro 40-60 €


def test_parola_esclusa():
    from maglie.servizio import parola_esclusa

    parole = ["felpa", "tuta", "k-way", "tracksuit"]
    assert parola_esclusa("Felpa Sassuolo calcio Puma", parole) == "felpa"
    assert parola_esclusa("Nike Barcelona TRACKSUIT vintage", parole) == "tracksuit"
    assert parola_esclusa("K-Way Inter", parole) == "k-way"
    assert parola_esclusa("Maglia tutta originale", parole) is None  # "tutta" non è "tuta"
    assert parola_esclusa("Maglia Inter 2009/10", parole) is None
    assert parola_esclusa("Maglia", []) is None


def test_annuncio_con_parola_esclusa_niente_avviso_niente_analisi(ambiente):
    cfg, client, tg, _, sveglia, archivio, scanner = ambiente
    client.risultati = []
    scanner.giro_ricerca(archivio, cfg.ricerche[0])
    client.risultati = [annuncio_raw(20) | {"titolo": "Felpa Inter allenamento"}, annuncio_raw(21)]
    assert scanner.giro_ricerca(archivio, cfg.ricerche[0]) == 1
    assert archivio.annuncio(20)["stato"] == "escluso"
    assert len(tg.messaggi) == 1 and "Maglia 21" in tg.messaggi[0]["testo"]
