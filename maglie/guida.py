"""Checklist per riconoscere le maglie false, per marca.

Riassunto (in italiano, con parole nostre) della guida "A Guide to Spotting Fake Soccer Jerseys"
della wiki di r/KitSwap (ultima revisione 2018): https://www.reddit.com/r/KitSwap/wiki/spottingfakes/
Le foto di esempio della guida (imgur) sono elencate in ESEMPI per consultarle a mano: non vengono
mandate a Claude, costerebbero troppo a ogni annuncio.

A Claude va solo la parte generale più la scheda della marca dell'annuncio, così il prompt resta corto.
"""

from __future__ import annotations

FONTE = "https://www.reddit.com/r/KitSwap/wiki/spottingfakes/"

GENERALE = """\
- Ovvio: manca il logo della marca? È una maglia che il club non ha mai usato né venduto? Materiale strano?
- Cartellino esterno (se c'è): deve essere specifico per quella maglia (stemma del club, taglia giusta, home/away/third).
  I falsi hanno cartellini generici tipo "Adidas jersey".
- Etichette interne: stampa nitida e leggibile (sbiadita/sfocata = sospetto), paese di produzione plausibile,
  taglia e codice uguali su TUTTE le etichette, codice interno uguale a quello del cartellino.
- Stemma e cuciture: ricamo pulito, simmetrico, senza fili liberi o lettere deformate. Gli stemmi veri sono spesso
  rigidi, ma non è una regola.
- Colori e tessuto: i falsi hanno spesso colori più pallidi/slavati e un poliestere più lucido ed economico.
- Allineamento: elementi fuori posto (es. punta del collo a V non allineata a righe o grafiche) sono sospetti.
- Patch: troppe patch insieme (campionato + scudetto + coppa) sono tipiche dei falsi.
- Nome e numero: nei falsi si raggrinziscono e si rovinano molto di più.
- Foto: foto di catalogo invece della maglia vera sono un cattivo segno."""

MARCHE = {
    "adidas": """\
- Logo adidas: ricamo pulito, ben spaziato e allineato; nei falsi è storto e trasandato.
- Stemma: confronta la precisione; nei falsi a volte è morbido come stoffa.
- Dal 2008 circa c'è un'etichetta all'interno della spalla sinistra con mese/anno di produzione e codice
  prodotto di 6 caratteri (es. GM4614). Il codice va cercato: deve corrispondere a questa maglia (può variare per regione).
- Etichette stampate nitide; sfocate o sbiadite = sospetto. Codice interno = codice del cartellino.""",
    "nike": """\
- Etichette sul fianco sinistro in basso: l'ultima ha il codice prodotto. Nei falsi quasi sempre è un codice
  generico riusato che non corrisponde alla maglia (ma i falsi migliori copiano anche il codice giusto).
- Codice: 6 cifre o 2 lettere + 4 cifre, poi 3 cifre di colore (es. 776929-100, CV8413-101). Interno = cartellino.
- Cuciture: il controllo qualità Nike è ottimo; fili liberi o lettere deformate nello stemma = falso.
- Colori più pallidi del normale = sospetto.
- Contatore di produzione in basso a destra (numero di serie + anno): sui veri di solito supera 1.000.000,
  sui falsi è molto più basso (circa 500.000 è frequente).""",
    "puma": """\
- Tessuto dei falsi più lucido (poliestere economico), colori meno ricchi.
- Stemma: confronta precisione e rifinitura (qui il falso può essere anche più rigido del vero).
- Numero di produzione in basso a sinistra sul davanti: sui falsi è di solito molto basso.
- Etichette interne nitide; sotto c'è il codice: 6 cifre + 2 o 3 cifre di colore (es. 763432-01). Deve
  corrispondere alla maglia e al cartellino.
- Allineamento della grafica (es. collo a V rispetto alle righe) e patch in eccesso = sospetto.""",
    "lotto": """\
- Etichetta della taglia poco a sinistra del collo.
- Etichetta prodotto dettagliata (con codice) dietro quella di lavaggio, sul fianco sinistro.""",
    "macron": """\
- Le etichette interne dicono poco. Il cartellino deve avere lo stemma della squadra e, sul retro, i dati
  del prodotto con il codice.""",
    "jako": """\
- Codice e dati prodotto su una piccola etichetta dietro quella di lavaggio; devono coincidere con il retro
  del cartellino. Marca di nicchia, falsi rari.""",
    "new balance": """\
- Codice prodotto e data di produzione in fondo al retro delle etichette di lavaggio: devono coincidere con il
  cartellino. Etichetta olografica in basso a sinistra sul davanti.""",
    "hummel": """\
- Le etichette interne non aiutano; il cartellino è molto dettagliato. Falsi rari, maglie poco costose.""",
}

VECCHIE = """\
- Maglie vecchie (anni '90-primi 2000): le etichette dicono poco e i consigli per marca valgono meno.
- I falsi vecchi sono di solito molto più grossolani di quelli moderni.
- I falsi invecchiano peggio, soprattutto nome e numero.
- Confronta il design con foto della maglia ufficiale: i falsi spesso omettono dettagli sottili
  (righine, texture del tessuto, motivi in trasparenza)."""

ALIAS = {"adidas originals": "adidas", "newbalance": "new balance", "nb": "new balance"}


def scheda_marca(marca: str | None) -> tuple[str | None, str | None]:
    """(nome marca, scheda) per la marca dell'annuncio, se la guida ne parla."""
    if not marca:
        return None, None
    m = " ".join(marca.lower().split())
    m = ALIAS.get(m, m)
    for nome, testo in MARCHE.items():
        if nome == m or nome in m:
            return nome, testo
    return None, None


def testo_guida(marca: str | None) -> str:
    nome, scheda = scheda_marca(marca)
    parti = ["GUIDA PER RICONOSCERE I FALSI (r/KitSwap)", "Regole generali:", GENERALE]
    if scheda:
        parti += [f"Specifico per {nome}:", scheda]
    else:
        parti += ["Per questa marca la guida non ha indicazioni specifiche: usa le regole generali."]
    parti += ["Maglie vecchie:", VECCHIE]
    return "\n".join(parti)


# Foto di esempio della guida (vero vs falso), per consultarle a mano
ESEMPI = {
    "generale": {"falso con troppe patch (Juventus)": "https://i.imgur.com/xwKB6Qq.jpg"},
    "adidas": {
        "maglia vera (Juventus away)": "https://i.imgur.com/ewYyud2.jpg",
        "maglia falsa (stessa stagione)": "https://i.imgur.com/yRSt7uy.jpg",
        "logo vero": "https://i.imgur.com/3w7uJjK.jpg",
        "logo falso": "https://i.imgur.com/Ycq5MTc.jpg",
        "stemma vero": "https://i.imgur.com/s8Z8B5F.jpg",
        "stemma falso": "https://i.imgur.com/0JN0Zgq.jpg",
        "etichetta spalla sinistra (vera)": "https://i.imgur.com/QQEey6t.jpg",
        "risultati codice che corrispondono": "https://imgur.com/VCAS57B",
        "etichetta interna (falsa)": "https://i.imgur.com/Hd6oRKs.jpg",
        "risultati codice che non corrispondono": "https://imgur.com/S6HMkVb",
        "etichetta nitida (vera)": "https://i.imgur.com/od0aBLO.jpg",
        "etichetta sfocata (falsa)": "https://i.imgur.com/wYBW1mI.jpg",
        "etichetta interna Marsiglia (vera)": "https://i.imgur.com/hOSjUbq.jpg",
        "cartellino Marsiglia (vero)": "https://i.imgur.com/t9sA4r8.jpg",
    },
    "nike": {
        "maglia vera (Francia away Griezmann)": "https://i.imgur.com/6mmrwVN.jpg",
        "maglia falsa": "https://i.imgur.com/YNiI4yT.jpg",
        "etichetta codice 1": "https://i.imgur.com/vnHnVJm.jpg",
        "etichetta codice 2": "https://i.imgur.com/IwLTREg.jpg",
        "stemma vero": "https://i.imgur.com/M6xA2y7.jpg",
        "stemma falso": "https://i.imgur.com/omV1o0v.jpg",
        "cuciture del falso": "https://i.imgur.com/RVQp1Wi.jpg",
        "confronto colori": "https://i.imgur.com/O2vSnBR.jpg",
        "codice sulla maglia": "https://i.imgur.com/U2i06qw.jpg",
        "codice sul cartellino": "https://i.imgur.com/jwmXY9g.jpg",
        "contatore produzione vero": "https://i.imgur.com/tWQlfRq.jpg",
        "contatore produzione falso": "https://i.imgur.com/G8LF3YV.jpg",
    },
    "puma": {
        "maglia vera (Arsenal away 16/17)": "https://i.imgur.com/hpWx3RA.jpg",
        "maglia falsa": "https://i.imgur.com/mlcqhQu.jpg",
        "stemma vero": "https://i.imgur.com/G26pndH.jpg",
        "stemma falso": "https://i.imgur.com/juboinT.jpg",
        "numero produzione falso": "https://i.imgur.com/6Zxj0aH.jpg",
        "numero produzione vero": "https://i.imgur.com/z3CjrBA.jpg",
        "etichette vere": "https://i.imgur.com/tVfD3gf.jpg",
        "etichette false": "https://i.imgur.com/3u91WKV.jpg",
        "codice vero": "https://i.imgur.com/L7JDgTF.jpg",
        "codice falso": "https://i.imgur.com/QazXVy2.jpg",
        "allineamento vero": "https://i.imgur.com/zzQoLVC.jpg",
        "allineamento falso": "https://i.imgur.com/Ycjwdok.jpg",
        "patch sul falso": "https://i.imgur.com/iWZpMr0.jpg",
        "etichetta interna": "https://i.imgur.com/i4hbgHu.jpg",
        "cartellino": "https://i.imgur.com/LN5e1Y0.jpg",
        "cartellini veri 1": "https://i.imgur.com/Q1v5c9g.jpg",
        "cartellini veri 2": "https://i.imgur.com/c9DXvBl.jpg",
    },
    "lotto": {"etichetta taglia": "https://i.imgur.com/JbGFkJg.jpg",
              "etichetta prodotto": "https://i.imgur.com/vzIW2SE.jpg"},
    "macron": {"cartellino con stemma": "https://i.imgur.com/T1J8Un5.jpg",
               "retro cartellino": "https://i.imgur.com/93P8xbq.jpg"},
    "jako": {"etichetta prodotto": "https://i.imgur.com/AR2akbi.jpg",
             "retro cartellino": "https://i.imgur.com/oPwoLMR.jpg",
             "fronte cartellino": "https://i.imgur.com/Mi5Mwq9.jpg"},
    "new balance": {"cartellino": "https://i.imgur.com/KL2uBkC.jpg",
                    "cartellino, etichetta e ologramma": "https://i.imgur.com/MYpfwJo.jpg"},
    "hummel": {"cartellini": "https://i.imgur.com/MUNYx1X.jpg"},
    "vecchie": {"etichette Argentina away 03/04 (1)": "https://i.imgur.com/xVulowT.jpg",
                "etichette Argentina away 03/04 (2)": "https://i.imgur.com/WC71KVr.jpg"},
}
