"""Controlla come vengono costruite le richieste a Claude (senza chiamare l'API)."""

from types import SimpleNamespace as NS

from maglie.ai import AnalistaAI


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
