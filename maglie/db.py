"""Archivio SQLite: annunci visti, coda di analisi, cache delle ricerche dei codici.

Ogni thread apre la propria connessione (SQLite non ama condividerle).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS annunci (
    id              INTEGER PRIMARY KEY,
    ricerca         TEXT,
    titolo          TEXT,
    descrizione     TEXT,
    prezzo          TEXT,
    taglia          TEXT,
    marca           TEXT,
    url             TEXT,
    foto_principale TEXT,
    visto_il        TEXT,
    -- baseline | vecchio | in_coda | analizzato | errore | saltato
    stato           TEXT,
    tentativi       INTEGER DEFAULT 0,
    tg_message_id   INTEGER,
    -- corrisponde | non_corrisponde | incerto | codice_non_trovato |
    -- codice_anomalo | illeggibile | nessuna_etichetta
    esito           TEXT,
    codice_stile    TEXT,
    upc             TEXT,
    taglia_etichetta TEXT,
    prodotto        TEXT,
    motivo          TEXT,
    analizzato_il   TEXT,
    giudizio_prezzo TEXT,     -- sotto | media | sopra | indicativo
    prezzo_tipico   TEXT
);
CREATE INDEX IF NOT EXISTS idx_annunci_stato ON annunci(stato);
CREATE INDEX IF NOT EXISTS idx_annunci_esito ON annunci(esito);

CREATE TABLE IF NOT EXISTS ricerche_inizializzate (
    chiave      TEXT PRIMARY KEY,
    il          TEXT,
    max_id      INTEGER,   -- ID più alto visto: gli annunci con ID molto più basso sono vecchi
    ultimo_giro TEXT       -- ultima ricerca riuscita: serve a capire se lo script è rimasto fermo
);

-- un record per ogni tentativo di analisi (anche quelli falliti: i token li paghi lo stesso)
CREATE TABLE IF NOT EXISTS consumi (
    id_annuncio  INTEGER,
    il           TEXT,
    token_in     INTEGER,
    token_out    INTEGER,
    ricerche_web INTEGER,
    costo        REAL
);
CREATE INDEX IF NOT EXISTS idx_consumi_il ON consumi(il);

CREATE TABLE IF NOT EXISTS cache_codici (
    chiave   TEXT PRIMARY KEY,
    risultato TEXT,
    il       TEXT
);
"""

ESITI_LISTA = ("corrisponde", "visivo_originale")

_LOCK_SCHEMA = threading.Lock()


def adesso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Archivio:
    def __init__(self, percorso: str | Path):
        self.conn = sqlite3.connect(str(percorso), timeout=30)
        self.conn.row_factory = sqlite3.Row
        # creazione/aggiornamento dello schema: un thread alla volta (in parallelo SQLite dà "database is locked")
        with _LOCK_SCHEMA:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self._migra()
            self.conn.commit()

    def _migra(self) -> None:
        """Aggiunge le colonne nuove ai database creati con versioni precedenti."""
        nuove = {
            "ricerche_inizializzate": (("max_id", "INTEGER"), ("ultimo_giro", "TEXT")),
            "annunci": (("giudizio_prezzo", "TEXT"), ("prezzo_tipico", "TEXT")),
        }
        for tabella, colonne_nuove in nuove.items():
            colonne = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({tabella})")}
            for nome, tipo in colonne_nuove:
                if nome not in colonne:
                    try:
                        self.conn.execute(f"ALTER TABLE {tabella} ADD COLUMN {nome} {tipo}")
                    except sqlite3.OperationalError as ex:
                        # un altro thread l'ha appena aggiunta: va bene così
                        if "duplicate column" not in str(ex):
                            raise

    def chiudi(self) -> None:
        self.conn.close()

    # --- ricerche -----------------------------------------------------------
    def ricerca_inizializzata(self, chiave: str) -> bool:
        r = self.conn.execute("SELECT 1 FROM ricerche_inizializzate WHERE chiave=?", (chiave,)).fetchone()
        return r is not None

    def segna_ricerca_inizializzata(self, chiave: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO ricerche_inizializzate (chiave, il) VALUES (?, ?)", (chiave, adesso()))
        self.conn.commit()

    def stato_ricerca(self, chiave: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT max_id, ultimo_giro FROM ricerche_inizializzate WHERE chiave=?", (chiave,)
        ).fetchone()

    def aggiorna_ricerca(self, chiave: str, max_id: int | None) -> None:
        """Registra un giro riuscito: aggiorna l'ID più alto visto e l'ora dell'ultimo giro."""
        self.conn.execute(
            """INSERT INTO ricerche_inizializzate (chiave, il, max_id, ultimo_giro) VALUES (?, ?, ?, ?)
               ON CONFLICT(chiave) DO UPDATE SET
                 max_id = MAX(COALESCE(max_id, 0), COALESCE(excluded.max_id, 0)),
                 ultimo_giro = excluded.ultimo_giro""",
            (chiave, adesso(), max_id, adesso()),
        )
        self.conn.commit()

    # --- annunci ------------------------------------------------------------
    def ids_gia_visti(self, ids: list[int]) -> set[int]:
        if not ids:
            return set()
        segnaposto = ",".join("?" * len(ids))
        righe = self.conn.execute(f"SELECT id FROM annunci WHERE id IN ({segnaposto})", ids).fetchall()
        return {r["id"] for r in righe}

    def inserisci_annuncio(self, a: dict, ricerca: str, stato: str) -> bool:
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO annunci
               (id, ricerca, titolo, descrizione, prezzo, taglia, marca, url, foto_principale, visto_il, stato)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (a["id"], ricerca, a.get("titolo"), a.get("descrizione"), a.get("prezzo"), a.get("taglia"),
             a.get("marca"), a.get("url"), a.get("foto_principale"), adesso(), stato),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def imposta_tg_message_id(self, id_annuncio: int, message_id: int | None) -> None:
        self.conn.execute("UPDATE annunci SET tg_message_id=? WHERE id=?", (message_id, id_annuncio))
        self.conn.commit()

    def prossimo_in_coda(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM annunci WHERE stato='in_coda' ORDER BY visto_il, id LIMIT 1"
        ).fetchone()

    def annuncio(self, id_annuncio: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM annunci WHERE id=?", (id_annuncio,)).fetchone()

    def segna_tentativo_fallito(self, id_annuncio: int, motivo: str, max_tentativi: int) -> int:
        self.conn.execute("UPDATE annunci SET tentativi=tentativi+1, motivo=? WHERE id=?", (motivo, id_annuncio))
        self.conn.execute(
            "UPDATE annunci SET stato='errore' WHERE id=? AND tentativi>=?", (id_annuncio, max_tentativi)
        )
        self.conn.commit()
        return self.conn.execute("SELECT tentativi FROM annunci WHERE id=?", (id_annuncio,)).fetchone()["tentativi"]

    def salva_esito(self, id_annuncio: int, esito: str, *, codice_stile=None, upc=None,
                    taglia_etichetta=None, prodotto=None, motivo=None, giudizio_prezzo=None,
                    prezzo_tipico=None) -> None:
        self.conn.execute(
            """UPDATE annunci SET stato='analizzato', esito=?, codice_stile=?, upc=?, taglia_etichetta=?,
               prodotto=?, motivo=?, analizzato_il=?, giudizio_prezzo=?, prezzo_tipico=? WHERE id=?""",
            (esito, codice_stile, upc, taglia_etichetta, prodotto, motivo, adesso(), giudizio_prezzo,
             prezzo_tipico, id_annuncio),
        )
        self.conn.commit()

    def segna_saltato(self, id_annuncio: int, motivo: str) -> None:
        self.conn.execute("UPDATE annunci SET stato='saltato', motivo=? WHERE id=?", (motivo, id_annuncio))
        self.conn.commit()

    def analisi_oggi(self) -> int:
        oggi = datetime.now(timezone.utc).date().isoformat()
        return self.conn.execute(
            "SELECT COUNT(*) AS n FROM annunci WHERE analizzato_il LIKE ?", (oggi + "%",)
        ).fetchone()["n"]

    def lista(self, esiti: tuple[str, ...] = ESITI_LISTA) -> list[sqlite3.Row]:
        segnaposto = ",".join("?" * len(esiti))
        return self.conn.execute(
            f"""SELECT a.*, COALESCE((SELECT SUM(c.costo) FROM consumi c WHERE c.id_annuncio = a.id), 0) AS costo_usd
                FROM annunci a WHERE a.esito IN ({segnaposto}) ORDER BY a.analizzato_il DESC""", esiti
        ).fetchall()

    # --- consumi ------------------------------------------------------------
    def registra_consumo(self, id_annuncio: int | None, token_in: int, token_out: int,
                         ricerche_web: int, costo: float) -> None:
        self.conn.execute("INSERT INTO consumi VALUES (?,?,?,?,?,?)",
                          (id_annuncio, adesso(), token_in, token_out, ricerche_web, costo))
        self.conn.commit()

    def costo_oggi(self) -> float:
        oggi = datetime.now(timezone.utc).date().isoformat()
        r = self.conn.execute("SELECT COALESCE(SUM(costo), 0) AS t FROM consumi WHERE il LIKE ?", (oggi + "%",)).fetchone()
        return r["t"]

    def consumi_per_giorno(self, giorni: int = 7) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT substr(il, 1, 10) AS giorno, COUNT(DISTINCT id_annuncio) AS annunci,
                      SUM(token_in) AS token_in, SUM(token_out) AS token_out,
                      SUM(ricerche_web) AS ricerche_web, SUM(costo) AS costo
               FROM consumi GROUP BY giorno ORDER BY giorno DESC LIMIT ?""", (giorni,)
        ).fetchall()

    # --- cache codici -------------------------------------------------------
    def cache_codice(self, chiave: str, max_giorni: float | None = None) -> dict | None:
        r = self.conn.execute("SELECT risultato, il FROM cache_codici WHERE chiave=?", (chiave,)).fetchone()
        if not r:
            return None
        if max_giorni is not None:
            try:
                eta = datetime.now(timezone.utc) - datetime.fromisoformat(r["il"])
            except (TypeError, ValueError):
                return None
            if eta.total_seconds() > max_giorni * 86400:
                return None
        return json.loads(r["risultato"])

    def salva_cache_codice(self, chiave: str, risultato: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache_codici VALUES (?,?,?)",
            (chiave, json.dumps(risultato, ensure_ascii=False), adesso()),
        )
        self.conn.commit()
