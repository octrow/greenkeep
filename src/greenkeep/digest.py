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
    owner: list[str] = field(default_factory=list)  # what only the owner can settle
    billing: list[str] = field(default_factory=list)  # repos whose CI GitHub did not start
    # repos with nothing to report: no findings, findings only past the budget, cooling down
    clean: int = 0
    waiting: list[str] = field(default_factory=list)
    cooling: int = 0
    reached: int = 0  # repos cloned: none means no network, so the day is not done
    limited: bool = False  # Claude did not answer (limit, login, outage): the day is not done

    def text(self, budget_left: int) -> str:
        # one account runs out of Actions minutes for all its private repos: one line
        billing = f"CI does not start (GitHub Actions billing): {', '.join(self.billing)}"
        problems = [*self.problems, *([billing] if self.billing else [])]
        lines = [
            (
                f"greenkeep: {len(self.merged)} merged, {len(self.parked)} parked, "
                f"{len(problems)} problems; budget left today: {budget_left}"
            )
        ]
        for title, items in (
            ("Merged", self.merged),
            ("Needs you", [*self.parked, *self.owner]),
            ("Problems", problems),
        ):
            if items:
                lines += ["", f"{title}:", *(f"• {i}" for i in items)]
        if quiet := self._quiet():
            lines += ["", "Quiet: " + quiet]
        return "\n".join(lines)

    def _quiet(self) -> str:
        parts = [
            f"{self.clean} clean" if self.clean else "",
            (
                f"{len(self.waiting)} waiting for budget ({', '.join(self.waiting)})"
                if self.waiting
                else ""
            ),
            f"{self.cooling} in cool-down" if self.cooling else "",
        ]
        return ", ".join(p for p in parts if p)


def send(text: str) -> None:
    """Telegram when configured, else a desktop notification; the log has it either way."""
    log.info("%s", text)
    token, chat = os.environ.get(TOKEN_ENV, ""), os.environ.get(CHAT_ENV, "")
    if token and chat.lstrip("-").isdigit() and bot_send(token, int(chat), text):
        return
    notify("greenkeep", text.splitlines()[0])
