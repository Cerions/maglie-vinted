"""Controlla come vengono costruite le richieste a Claude (senza chiamare l'API)."""

from types import SimpleNamespace as NS

import pytest

from maglie.ai import AnalistaAI, Prezzi, costo_richiesta


class MessaggiFinti:
    def __init__(self, risposte):
        self.risposte = list(risposte)
        self.chiamate = []

    def create(self, **kwargs):
        # copia: il codice riusa la stessa lista di messaggi tra una chiamata e l'altra
        self.chiamate.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.risposte.pop(0)


def ai_con(risposte):
    ai = AnalistaAI("sk-test", "claude-haiku-5-5", ricerche_web=3)
    ai.client = NS(messages=MessaggiFinti(risposte))
    return ai


def tool(nome, dati):
    return NS(type="tool_use", name=nome, input=dati)


def test_leggi_etichetta_manda_le_foto_numerate_e_forza_il_tool():
    ai = ai_con([NS(content=[tool("riporta_etichetta", {"etichetta_trovata": True, "codice_stile": "CV8413-101",
                                                         "leggibilita": "buona", "descrizione_maglia": "x"})],
                    stop_reason="tool_use")])
    r = ai.leggi_etichetta([(b"a", "image/jpeg"), (b"b", "image/png")], {"titolo": "Maglia", "marca": "Nike"})
    assert r["codice_stile"] == "CV8413-101"
    chiamata = ai.client.messages.chiamate[0]
    contenuto = chiamata["messages"][0]["content"]
    assert [c["type"] for c in contenuto] == ["text", "image", "text", "image", "text"]
    assert contenuto[3]["source"]["media_type"] == "image/png"
    assert chiamata["tool_choice"] == {"type": "tool", "name": "riporta_etichetta"}


def test_cerca_codice_gestisce_pause_turn_e_sollecito():
    testo = NS(type="text", text="Ho cercato...")
    ai = ai_con([
        NS(content=[testo], stop_reason="pause_turn"),
        NS(content=[testo], stop_reason="end_turn"),
        NS(content=[tool("riporta_prodotto", {"trovato": True, "squadra": "X", "fonti": ["u"]})], stop_reason="tool_use"),
    ])
    r = ai.cerca_codice("CV8413-101", "194502132830", "nike")
    assert r["trovato"] and r["squadra"] == "X"
    chiamate = ai.client.messages.chiamate
    assert len(chiamate) == 3
    assert chiamate[0]["tools"][0] == {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
    assert "194502132830" in chiamate[0]["messages"][0]["content"]
    # dopo pause_turn: si rimanda la risposta dell'assistente senza aggiungere testo utente
    assert chiamate[1]["messages"][-1]["role"] == "assistant"
    # dopo end_turn senza tool: sollecito
    assert chiamate[2]["messages"][-1] == {"role": "user", "content": "Ora chiama riporta_prodotto con quello che hai trovato."}


def test_cerca_codice_alla_fine_forza_il_tool():
    testo = NS(type="text", text="...")
    ai = ai_con([NS(content=[testo], stop_reason="end_turn")] * 3 +
                [NS(content=[tool("riporta_prodotto", {"trovato": False, "fonti": []})], stop_reason="tool_use")])
    assert ai.cerca_codice("GM4614", None, "adidas") == {"trovato": False, "fonti": []}
    assert ai.client.messages.chiamate[3]["tool_choice"] == {"type": "tool", "name": "riporta_prodotto"}


def test_costo_richiesta_e_fascia_prompt_lungo():
    p = Prezzi()
    assert costo_richiesta(p, 10_000, 500, 0) == pytest.approx(0.001 + 0.00025)
    assert costo_richiesta(p, 10_000, 500, 2) == pytest.approx(0.02125)
    # oltre 100k token in input l'intera richiesta va alla tariffa più alta
    assert costo_richiesta(p, 150_000, 1_000, 0) == pytest.approx(0.075 + 0.0025)


def test_consumo_accumulato_dalle_risposte():
    uso1 = NS(input_tokens=9000, output_tokens=300, cache_creation_input_tokens=None,
              cache_read_input_tokens=None, server_tool_use=None)
    uso2 = NS(input_tokens=20000, output_tokens=400, cache_creation_input_tokens=0,
              cache_read_input_tokens=0, server_tool_use=NS(web_search_requests=2))
    ai = ai_con([
        NS(content=[tool("riporta_etichetta", {"etichetta_trovata": False, "leggibilita": "scarsa",
                                                "descrizione_maglia": ""})], stop_reason="tool_use", usage=uso1),
        NS(content=[tool("riporta_prodotto", {"trovato": False, "fonti": []})], stop_reason="tool_use", usage=uso2),
    ])
    ai.leggi_etichetta([(b"a", "image/jpeg")], {})
    ai.cerca_codice("GM4614", None, "adidas")
    c = ai.prendi_consumo()
    assert (c.token_in, c.token_out, c.ricerche_web) == (29000, 700, 2)
    assert c.costo == pytest.approx(costo_richiesta(Prezzi(), 9000, 300, 0) + costo_richiesta(Prezzi(), 20000, 400, 2))
    assert str(c) == "29.000 in / 700 out · 2 ricerche web · $0.0232"
    assert not ai.prendi_consumo()  # azzerato


def test_client_httpx_riprova_e_restituisce_oggetti(monkeypatch):
    import httpx

    from maglie import ai as modulo

    risposte = [
        httpx.Response(529, json={"error": {"message": "Overloaded"}}),
        httpx.Response(200, json={"content": [{"type": "tool_use", "name": "riporta_verdetto",
                                               "input": {"esito": "incerto", "motivo": "x"}}],
                                  "stop_reason": "tool_use",
                                  "usage": {"input_tokens": 100, "output_tokens": 10,
                                            "server_tool_use": {"web_search_requests": 1}}}),
    ]
    inviati = []

    def post(url, json, headers):
        inviati.append((url, json, headers))
        return risposte.pop(0)

    monkeypatch.setattr(modulo.time, "sleep", lambda s: None)
    ai = AnalistaAI("sk-test", "claude-haiku-5-5")
    monkeypatch.setattr(ai.client.http, "post", post)
    r = ai.confronta({}, {}, {})
    assert r == {"esito": "incerto", "motivo": "x"}
    assert len(inviati) == 2
    assert inviati[0][2]["x-api-key"] == "sk-test" and inviati[0][2]["anthropic-version"] == "2023-06-01"
    c = ai.prendi_consumo()
    assert (c.token_in, c.token_out, c.ricerche_web) == (100, 10, 1)


def test_client_httpx_errore_definitivo(monkeypatch):
    import httpx

    from maglie import ai as modulo

    ai = AnalistaAI("sk-bad", "claude-haiku-5-5")
    monkeypatch.setattr(ai.client.http, "post",
                        lambda url, json, headers: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))
    with pytest.raises(modulo.ErroreAPI, match="401: invalid x-api-key"):
        ai.confronta({}, {}, {})
