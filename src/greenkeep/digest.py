"""The digest: what one run did, sent to the owner's Telegram after the run."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from tg_toolkit.push import bot_send, notify

log = logging.getLogger("greenkeep")

TOKEN_ENV = "GREENKEEP_TG_BOT_TOKEN"
CHAT_ENV = "GREENKEEP_TG_CHAT_ID"


@dataclass
class Digest:
    merged: list[str] = field(default_factory=list)
    parked: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def text(self, budget_left: int) -> str:
        lines = [(f"greenkeep: {len(self.merged)} merged, {len(self.parked)} parked, "
                 f"{len(self.problems)} problems; budget left today: {budget_left}")]
        for title, items in (("Merged", self.merged), ("Needs you", self.parked),
                             ("Problems", self.problems)):
            if items:
                lines += ["", f"{title}:", *(f"• {i}" for i in items)]
        return "\n".join(lines)


def send(text: str) -> None:
    """Telegram when configured, else a desktop notification; the log has it either way."""
    log.info("%s", text)
    token, chat = os.environ.get(TOKEN_ENV, ""), os.environ.get(CHAT_ENV, "")
    if token and chat.lstrip("-").isdigit() and bot_send(token, int(chat), text):
        return
    notify("greenkeep", text.splitlines()[0])
