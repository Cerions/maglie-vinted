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
