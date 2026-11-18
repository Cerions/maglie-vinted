"""Caricamento della configurazione (config.toml + variabili d'ambiente / .env)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv è opzionale
    load_dotenv = None


@dataclass
class Ricerca:
    nome: str
    url: str  # URL di una ricerca Vinted copiato dal browser, oppure testo semplice


@dataclass
class VintedConfig:
    dominio: str = "https://www.vinted.it"
    intervallo_min_secondi: float = 60
    intervallo_max_secondi: float = 120
    pausa_tra_ricerche_secondi: tuple[float, float] = (3, 8)
    backoff_blocco_minuti: float = 5
    backoff_errore_minuti: float = 1
    backoff_max_minuti: float = 60
    eta_massima_annuncio_minuti: float = 30
    margine_id_vecchi: int = 1_000_000
    risultati_per_ricerca: int = 20


@dataclass
class AnalisiConfig:
    modello: str = "claude-haiku-5-5"
    max_foto: int = 8
    max_analisi_al_giorno: int = 2000
    ricerche_web_per_codice: int = 3
    # Tetto di spesa giornaliero (dollari): raggiunto questo, le analisi si fermano fino a domani
    budget_giornaliero_usd: float = 1.0
    # Analizza solo le maglie da gara (salta felpe, allenamento, ecc.)
    solo_maglie_da_calcio: bool = True
    # Controllo prezzo con ricerca web: "occasioni" (solo se la stima dice che può costare meno del solito),
    # "sempre" oppure "mai"
    controllo_prezzo: str = "occasioni"
    # Dopo quanti giorni rifare la ricerca di un codice o dei prezzi di una maglia
    giorni_cache: int = 7
    # Prezzi in dollari (default: Claude Haiku 5.5). Se cambi modello, aggiornali.
    prezzo_input_mtok: float = 0.10
    prezzo_output_mtok: float = 0.50
    prezzo_ricerca_web: float = 0.01
    soglia_prompt_lungo: int = 100_000
    prezzo_input_mtok_lungo: float = 0.50
    prezzo_output_mtok_lungo: float = 2.50


@dataclass
class TelegramConfig:
    avviso_immediato: bool = True
    bot_token: str = ""
    chat_id: str = ""

    @property
    def attivo(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass
class Config:
    vinted: VintedConfig = field(default_factory=VintedConfig)
    analisi: AnalisiConfig = field(default_factory=AnalisiConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    ricerche: list[Ricerca] = field(default_factory=list)
    anthropic_api_key: str = ""
    percorso_db: Path = Path("maglie.db")
    cartella_log: Path = Path("logs")


def _riempi(obj, valori: dict) -> None:
    for chiave, valore in valori.items():
        if not hasattr(obj, chiave):
            raise ValueError(f"Opzione sconosciuta nel config: '{chiave}'")
        if isinstance(getattr(obj, chiave), tuple):
            valore = tuple(valore)
        setattr(obj, chiave, valore)


def carica_config(percorso: str | Path = "config.toml") -> Config:
    percorso = Path(percorso)
    if load_dotenv is not None:
        load_dotenv(percorso.parent / ".env")

    cfg = Config()
    if percorso.exists():
        with open(percorso, "rb") as f:
            dati = tomllib.load(f)
        _riempi(cfg.vinted, dati.get("vinted", {}))
        _riempi(cfg.analisi, dati.get("analisi", {}))
        _riempi(cfg.telegram, dati.get("telegram", {}))
        for r in dati.get("ricerche", []):
            cfg.ricerche.append(Ricerca(nome=r.get("nome") or r["url"], url=r["url"]))
        if "percorso_db" in dati:
            cfg.percorso_db = Path(dati["percorso_db"])

    cfg.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    cfg.telegram.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", cfg.telegram.bot_token)
    cfg.telegram.chat_id = os.environ.get("TELEGRAM_CHAT_ID", cfg.telegram.chat_id)

    if cfg.analisi.controllo_prezzo not in ("occasioni", "sempre", "mai"):
        raise ValueError('controllo_prezzo deve essere "occasioni", "sempre" o "mai"')
    v = cfg.vinted
    if v.intervallo_min_secondi > v.intervallo_max_secondi:
        raise ValueError("intervallo_min_secondi non può superare intervallo_max_secondi")
    if v.intervallo_min_secondi < 30:
        raise ValueError("Intervallo minimo sotto i 30 secondi: troppo aggressivo per un IP di casa")
    return cfg
