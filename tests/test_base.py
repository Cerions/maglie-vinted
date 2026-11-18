from maglie.codici import gtin_valido, normalizza_codice_stile, normalizza_upc
from maglie.vinted import estrai_descrizione, estrai_foto_da_pagina, normalizza_annuncio, parametri_da_ricerca


# --- codici -----------------------------------------------------------------

def test_nike_codice_normale_e_senza_trattino():
    assert normalizza_codice_stile("CV8413-101", "Nike").codice == "CV8413-101"
    r = normalizza_codice_stile("cv8413 101", "NIKE")
    assert r.valido and r.codice == "CV8413-101"
    assert normalizza_codice_stile("CV8413101", "nike").codice == "CV8413-101"
    assert normalizza_codice_stile("776929–100", "nike").valido  # trattino lungo, codice vecchio


def test_nike_formato_strano():
    r = normalizza_codice_stile("CV84-13101X", "nike")
    assert not r.valido and "nike" in r.motivo


def test_adidas_e_puma():
    assert normalizza_codice_stile("GM4614", "adidas").valido
    assert normalizza_codice_stile("H35917", "Adidas Originals").valido
    r = normalizza_codice_stile("76343201", "puma")
    assert r.valido and r.codice == "763432-01"


def test_marca_sconosciuta_formato_generico():
    assert normalizza_codice_stile("KA1234-5", "Kappa").valido
    assert not normalizza_codice_stile("??", None).valido
    assert normalizza_codice_stile(None, "nike").codice is None


def test_upc_della_foto_di_esempio():
    assert gtin_valido("194502132830")
    assert normalizza_upc("1945 0213 2830").valido


def test_upc_cifra_sbagliata():
    r = normalizza_upc("194502132831")
    assert not r.valido and "controllo" in r.motivo
    assert not normalizza_upc("12345").valido


# --- vinted -----------------------------------------------------------------

def test_parametri_da_url_del_browser():
    url = ("https://www.vinted.it/catalog?search_text=maglia%20calcio&brand_ids[]=53&brand_ids[]=14"
           "&catalog[]=1193&size_ids[]=208&price_to=40&currency=EUR&order=relevance&time=1700000000")
    p = parametri_da_ricerca(url)
    assert p["search_text"] == "maglia calcio"
    assert p["attribute_ids[brand]"] == "53,14"
    assert p["attribute_ids[catalog]"] == "1193"
    assert p["attribute_ids[size]"] == "208"
    assert p["price_to"] == "40"
    assert p["order"] == "newest_first"  # forzato
    assert "time" not in p


def test_parametri_da_testo():
    p = parametri_da_ricerca("maglia inter vintage")
    assert p == {"search_text": "maglia inter vintage", "order": "newest_first", "per_page": "20", "page": "1"}


PAGINA = r'''
<html><head>
<meta property="og:description" content="Maglia Inter 2020/21 taglia L, mai usata &amp; perfetta">
</head><body>
<img src="https://images1.vinted.net/t/AAA/f800/1700000001.jpeg?s=abc">
<script>{"photos":[{"url":"https:\/\/images1.vinted.net\/t\/AAA\/310x430\/1700000001.jpeg?s=abc"},
{"url":"https:\/\/images1.vinted.net\/t\/BBB\/f800\/1700000002.jpeg?s=def&x=1"},
{"url":"https:\/\/images1.vinted.net\/t\/CCC\/310x430\/1700000003.webp?s=ghi"}]}</script>
<img src="https://images1.vinted.net/t/CCC/f800/1700000003.webp?s=ghi">
<img src="https://images1.vinted.net/assets/logo.svg">
</body></html>
'''


def test_estrai_foto_ordine_dedup_e_f800():
    foto = estrai_foto_da_pagina(PAGINA, max_foto=8)
    assert [f.split("?")[0].rsplit("/", 1)[-1] for f in foto] == ["1700000001.jpeg", "1700000002.jpeg", "1700000003.webp"]
    assert all("/f800/" in f for f in foto)
    assert estrai_foto_da_pagina(PAGINA, max_foto=2)[-1].endswith("1700000002.jpeg?s=def&x=1")


def test_estrai_descrizione():
    assert estrai_descrizione(PAGINA) == "Maglia Inter 2020/21 taglia L, mai usata & perfetta"


def test_normalizza_annuncio():
    raw = {"id": "123", "title": "Maglia", "price": {"amount": "25.0", "currency_code": "EUR"},
           "size_title": "L", "brand_title": "Nike", "url": "https://www.vinted.it/items/123-maglia",
           "photo": {"url": "https://images1.vinted.net/t/X/f800/1.jpeg", "high_resolution": {"timestamp": 1700000000}}}
    a = normalizza_annuncio(raw, "https://www.vinted.it")
    assert a["id"] == 123 and a["prezzo"] == "25.0 EUR" and a["timestamp"] == 1700000000
    assert normalizza_annuncio({"id": 5}, "https://www.vinted.it")["url"] == "https://www.vinted.it/items/5"


def test_comando_costi(tmp_path, monkeypatch, capsys):
    import main
    from maglie.db import Archivio

    monkeypatch.chdir(tmp_path)
    assert main.main(["costi"]) == 0
    assert "Nessun consumo" in capsys.readouterr().out
    a = Archivio("maglie.db")
    a.registra_consumo(1, 9000, 300, 0, 0.00105)
    a.registra_consumo(2, 30000, 900, 2, 0.02345)
    a.chiudi()
    assert main.main(["costi"]) == 0
    out = capsys.readouterr().out
    assert "39.000" in out and "0.0245" in out


def test_accept_encoding_solo_formati_decodificabili(monkeypatch):
    import httpx._decoders
    import vinted_scraper._base_wrapper as base

    import maglie.vinted  # noqa: F401  (applica la correzione)

    h = base.get_cookie_headers("https://www.vinted.it", "UA", "it-IT")
    assert "zstd" not in h["Accept-Encoding"] or "zstd" in httpx._decoders.SUPPORTED_DECODERS
    # simula brotli bloccato
    monkeypatch.setattr(httpx._decoders, "SUPPORTED_DECODERS",
                        {k: v for k, v in httpx._decoders.SUPPORTED_DECODERS.items() if k != "br"})
    h = base.get_cookie_headers("https://www.vinted.it", "UA", "it-IT")
    assert h["Accept-Encoding"] == "gzip, deflate"


def test_comando_telegram_prova(tmp_path, monkeypatch, capsys):
    import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert main.main(["telegram-prova"]) == 1
    assert "Mancano" in capsys.readouterr().out

    inviati = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(main.Telegram, "_chiama", lambda self, metodo, dati: inviati.append((metodo, dati)) or {"message_id": 7})
    assert main.main(["telegram-prova"]) == 0
    assert inviati[0][0] == "sendMessage" and inviati[0][1]["chat_id"] == "42"


def test_prova_ricerca_salva_json(tmp_path, monkeypatch, capsys):
    import json

    import main

    monkeypatch.chdir(tmp_path)
    grezzo = {"items": [{"id": 1, "title": "Maglia", "price": {"amount": "10.0", "currency_code": "EUR"}}]}
    monkeypatch.setattr(main.ClientVinted, "cerca_grezzo", lambda self, params: grezzo)
    assert main.main(["prova-ricerca", "--ricerca", "maglia", "--salva-json", "risposta.json"]) == 0
    assert json.loads((tmp_path / "risposta.json").read_text(encoding="utf-8")) == grezzo
    assert "10.0 EUR" in capsys.readouterr().out


def test_normalizza_annuncio_dalla_ricerca_attuale():
    # forma reale della risposta di /svc-catalogue/items (novembre 2026), dati inventati
    raw = {"id": 10868879602, "title": "Maglia Real Madrid 2013/2014",
           "price": {"amount": "5.00", "currency_code": "EUR"},
           "item_box": {"first_line": "adidas", "second_line": "XS · Buone",
                        "accessibility_label": "Maglia Real Madrid 2013/2014, Brand: adidas, Condizioni: Buone, "
                                               "Taglia: XS, 5.00 €, 5.95 €"},
           "photo": {"url": "https://images1.vinted.net/t/A/f800/x.webp?s=1",
                     "full_size_url": "https://images1.vinted.net/tc/A/x.webp?s=2",
                     "high_resolution": {"id": "A", "orientation": 0}},
           "url": "/items/10868879602-maglia"}
    a = normalizza_annuncio(raw, "https://www.vinted.it")
    assert (a["marca"], a["taglia"], a["condizione"]) == ("adidas", "XS", "Buone")
    assert a["url"] == "https://www.vinted.it/items/10868879602-maglia"
    assert a["foto_principale"].endswith("/f800/x.webp?s=1")
    assert a["timestamp"] is None


def test_normalizza_annuncio_taglia_bambino_solo_second_line():
    raw = {"id": 1, "item_box": {"first_line": "Puma", "second_line": "5 anni / 110 cm · Buone"}}
    a = normalizza_annuncio(raw, "https://www.vinted.it")
    assert (a["marca"], a["taglia"], a["condizione"]) == ("Puma", "5 anni / 110 cm", "Buone")


def test_migrazione_db_vecchio(tmp_path):
    import sqlite3

    from maglie.db import Archivio

    percorso = tmp_path / "vecchio.db"
    c = sqlite3.connect(percorso)
    c.execute("CREATE TABLE ricerche_inizializzate (chiave TEXT PRIMARY KEY, il TEXT)")
    c.execute("INSERT INTO ricerche_inizializzate VALUES ('r', '2026-11-18T10:00:00+00:00')")
    c.commit()
    c.close()
    a = Archivio(percorso)
    assert a.stato_ricerca("r")["max_id"] is None
    a.aggiorna_ricerca("r", 500)
    a.aggiorna_ricerca("r", 300)  # non scende mai
    assert a.stato_ricerca("r")["max_id"] == 500 and a.stato_ricerca("r")["ultimo_giro"]


def test_migrazione_da_due_thread_insieme(tmp_path):
    import sqlite3
    import threading

    from maglie.db import Archivio

    percorso = tmp_path / "vecchio.db"
    c = sqlite3.connect(percorso)
    c.execute("CREATE TABLE ricerche_inizializzate (chiave TEXT PRIMARY KEY, il TEXT)")
    c.commit()
    c.close()
    errori = []
    pronti = threading.Barrier(4)

    def apri():
        try:
            pronti.wait()
            Archivio(percorso).chiudi()
        except Exception as ex:  # noqa: BLE001
            errori.append(ex)

    thread = [threading.Thread(target=apri) for _ in range(4)]
    for t in thread:
        t.start()
    for t in thread:
        t.join()
    assert errori == []
    a = Archivio(percorso)
    a.aggiorna_ricerca("r", 1)
    assert a.stato_ricerca("r")["max_id"] == 1


def test_migrazione_con_colonna_gia_aggiunta_da_altri(tmp_path, monkeypatch):
    """Simula esattamente la gara: PRAGMA dice che manca, ma intanto un altro thread l'ha aggiunta."""
    import sqlite3

    from maglie.db import Archivio

    percorso = tmp_path / "db.db"
    Archivio(percorso).chiudi()  # colonne già presenti
    originale = Archivio._migra

    def migra_con_pragma_vecchio(self):
        vero_execute = self.conn.execute

        class ConnFinta:
            def execute(self_, sql, *a):
                if sql.startswith("PRAGMA table_info"):
                    return [{"name": "chiave"}, {"name": "il"}]
                return vero_execute(sql, *a)

        conn = self.conn
        self.conn = ConnFinta()
        try:
            originale(self)
        finally:
            self.conn = conn

    monkeypatch.setattr(Archivio, "_migra", migra_con_pragma_vecchio)
    Archivio(percorso).chiudi()  # non deve sollevare "duplicate column"
