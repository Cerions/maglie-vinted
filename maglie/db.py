"""Archivio SQLite: annunci visti, coda di analisi, cache delle ricerche dei codici.

Ogni thread apre la propria connessione (SQLite non ama condividerle).
"""

from __future__ import annotations

import json
import sqlite3
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
    analizzato_il   TEXT
);
CREATE INDEX IF NOT EXISTS idx_annunci_stato ON annunci(stato);
CREATE INDEX IF NOT EXISTS idx_annunci_esito ON annunci(esito);

CREATE TABLE IF NOT EXISTS ricerche_inizializzate (
    chiave TEXT PRIMARY KEY,
    il     TEXT
);

CREATE TABLE IF NOT EXISTS cache_codici (
    chiave   TEXT PRIMARY KEY,
    risultato TEXT,
    il       TEXT
);
"""

ESITI_LISTA = ("corrisponde",)


def adesso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Archivio:
    def __init__(self, percorso: str | Path):
        self.conn = sqlite3.connect(str(percorso), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def chiudi(self) -> None:
        self.conn.close()

    # --- ricerche -----------------------------------------------------------
    def ricerca_inizializzata(self, chiave: str) -> bool:
        r = self.conn.execute("SELECT 1 FROM ricerche_inizializzate WHERE chiave=?", (chiave,)).fetchone()
        return r is not None

    def segna_ricerca_inizializzata(self, chiave: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO ricerche_inizializzate VALUES (?, ?)", (chiave, adesso()))
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
                    taglia_etichetta=None, prodotto=None, motivo=None) -> None:
        self.conn.execute(
            """UPDATE annunci SET stato='analizzato', esito=?, codice_stile=?, upc=?, taglia_etichetta=?,
               prodotto=?, motivo=?, analizzato_il=? WHERE id=?""",
            (esito, codice_stile, upc, taglia_etichetta, prodotto, motivo, adesso(), id_annuncio),
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
            f"SELECT * FROM annunci WHERE esito IN ({segnaposto}) ORDER BY analizzato_il DESC", esiti
        ).fetchall()

    # --- cache codici -------------------------------------------------------
    def cache_codice(self, chiave: str) -> dict | None:
        r = self.conn.execute("SELECT risultato FROM cache_codici WHERE chiave=?", (chiave,)).fetchone()
        return json.loads(r["risultato"]) if r else None

    def salva_cache_codice(self, chiave: str, risultato: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache_codici VALUES (?,?,?)",
            (chiave, json.dumps(risultato, ensure_ascii=False), adesso()),
        )
        self.conn.commit()
