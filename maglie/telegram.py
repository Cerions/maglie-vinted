"""Notifiche Telegram via Bot API (solo httpx, nessuna libreria in più)."""

from __future__ import annotations

import html
import logging
import time

import httpx

log = logging.getLogger(__name__)


def e(testo) -> str:
    """Escape per il parse_mode HTML di Telegram."""
    return html.escape(str(testo or ""), quote=False)


class Telegram:
    def __init__(self, bot_token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{bot_token}"
        self.chat_id = chat_id
        self.attivo = bool(bot_token and chat_id)

    def _chiama(self, metodo: str, dati: dict) -> dict | None:
        for _ in range(3):
            try:
                r = httpx.post(f"{self.base}/{metodo}", json=dati, timeout=20)
                corpo = r.json()
            except (httpx.HTTPError, ValueError) as ex:
                log.warning("Telegram %s fallito: %s", metodo, ex)
                time.sleep(3)
                continue
            if corpo.get("ok"):
                return corpo["result"]
            attesa = (corpo.get("parameters") or {}).get("retry_after")
            if attesa:
                time.sleep(attesa + 1)
                continue
            log.warning("Telegram %s rifiutato: %s", metodo, corpo.get("description"))
            return None
        return None

    def invia(self, testo: str, *, foto: str | None = None, in_risposta_a: int | None = None) -> int | None:
        """Invia un messaggio (con foto se possibile). Ritorna il message_id."""
        if not self.attivo:
            log.info("[telegram disattivato] %s", testo.replace("\n", " | "))
            return None
        base = {"chat_id": self.chat_id, "parse_mode": "HTML"}
        if in_risposta_a:
            base["reply_parameters"] = {"message_id": in_risposta_a, "allow_sending_without_reply": True}
        if foto:
            ris = self._chiama("sendPhoto", {**base, "photo": foto, "caption": testo[:1024]})
            if ris:
                return ris["message_id"]
        ris = self._chiama("sendMessage", {**base, "text": testo[:4096], "link_preview_options": {"is_disabled": True}})
        return ris["message_id"] if ris else None

    def chat_recenti(self) -> list[dict]:
        """Per trovare il chat_id: scrivi qualcosa al bot e poi chiama questo."""
        ris = self._chiama("getUpdates", {"timeout": 0}) or []
        chat = {}
        for u in ris:
            msg = u.get("message") or u.get("channel_post") or {}
            c = msg.get("chat")
            if c:
                chat[c["id"]] = c
        return list(chat.values())
