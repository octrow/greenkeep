"""greenkeep dashboard: what the bot did and is doing, on top of nicegui-kit.

    greenkeep-ui [--port 8095] [--no-show]

Read-only over the state database and log, plus three actions: resume after a pause,
start a run (live log; a dry run by default, with every CLI option in a dialog), refresh.
"""

from __future__ import annotations

import argparse
import re
import sys
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nicegui import app, ui
from nicegui_kit import layout, run_monitor, run_pool, table, texts, theme

from . import port
from .cli import LOG_FILE
from .config import PAUSE_FILE, ConfigError, load
from .core import DEFAULT_MODEL, DEFAULT_REVIEW_MODEL
from .state import State

DEFAULT_PORT = 8095
REFRESH_SECONDS = 30
LOG_TAIL_LINES = 200
LOG_MAX_HEIGHT = "24rem"
LOG_RECORD = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
PROJECT = Path(__file__).resolve().parents[2]  # the greenkeep checkout uv runs from
STATUSES = {"All": None, "Merged": "merged", "Needs you": "parked", "In progress": "open",
            "Reverted": "reverted"}
COLUMNS = [
    {"name": "repo", "label": "Repo", "field": "repo", "sortable": True, "align": "left"},
    {"name": "title", "label": "Finding", "field": "title", "align": "left"},
    {"name": "status", "label": "Status", "field": "status", "sortable": True, "align": "left"},
    {"name": "attempts", "label": "Attempts", "field": "attempts", "align": "right"},
    {"name": "updated", "label": "Updated", "field": "updated", "sortable": True,
     "align": "left"},
    {"name": "link", "label": "", "field": "link", "align": "left"},
]
LINK_SLOT = ('<q-td :props="props"><a v-if="props.value" :href="props.value" target="_blank" '
             'rel="noopener">{{ props.row.link_text }}</a></q-td>')


def table_rows(rows: list[dict], status: str | None, query: str) -> list[dict]:
    """State rows -> table rows: filtered, with a link to the PR (or else the issue)."""
    return [_decorate(r) for r in rows if _matches(r, status, query)]


def _matches(row: dict, status: str | None, query: str) -> bool:
    if status and row["status"] != status:
        return False
    return not query or query.lower() in f"{row['repo']} {row['title'] or ''}".lower()


def _decorate(row: dict) -> dict:
    number, kind = (row["pr"], "pull") if row["pr"] else (row["issue"], "issues")
    link = f"{row['url']}/{kind}/{number}" if row["url"] and number else ""
    return {**row, "title": row["title"] or row["fp"], "key": f"{row['repo']}:{row['fp']}",
            "link": link, "link_text": f"#{number}" if link else ""}


def budget_today() -> int | None:
    try:
        return load().budget_at(datetime.now().astimezone())  # a boost's budget too
    except ConfigError:
        return None


def log_tail(text: str) -> str:
    """The last events, newest first: lines that start an INFO+ log record. Debug records
    and continuation lines (diffs, test output) stay in the file."""
    events = [ln for ln in text.splitlines() if LOG_RECORD.match(ln) and " DEBUG " not in ln]
    return "\n".join(reversed(events[-LOG_TAIL_LINES:])) or "(no events yet)"


def _read_log() -> str:
    try:
        return LOG_FILE.read_text(errors="replace")
    except OSError:
        return ""


def _pause_card(refresh) -> None:
    with layout.section_card(recessive=not PAUSE_FILE.exists()):
        if PAUSE_FILE.exists():
            layout.heading("Paused", level=2)
            ui.label(PAUSE_FILE.read_text().strip() or "paused by hand")

            def resume() -> None:
                PAUSE_FILE.unlink(missing_ok=True)
                ui.notify("Resumed: the next hourly firing runs, unless today's run already finished")
                refresh()

            layout.button("Resume", kind="primary", on_click=resume)
        else:
            ui.label("Active: the systemd timer fires hourly; one run a day (every hour in a boost)")


def _summary(state: State) -> None:
    rows = state.rows()
    budget = budget_today()
    parked = sum(r["status"] == "parked" for r in rows)
    text = (f"Merged today: {state.merged_today()}"
            + (f" of {budget}" if budget is not None else "")
            + f" · needs you: {parked} · tracked findings: {len(rows)}")
    ui.label(text).classes("t-status")


def _filters() -> tuple[ui.select, ui.input]:
    """Status filter, search, and the dry-run button that sits with them."""
    with layout.filter_bar():
        status = ui.select(list(STATUSES), value="All").props("outlined dense")
        search = layout.search_field("Repo or finding")
        layout.button("Dry run now", kind="normal", on_click=_dry_run)
        layout.button("Run with options…", kind="normal", on_click=_options_dialog().open)
    return status, search


def run_argv(repos: list[str], push: bool, budget: int | None, model: str,
             review_model: str, keep: bool) -> list[str]:
    """The `greenkeep` command line for the dashboard's options; defaults and empties are omitted."""
    argv = ["uv", "run", "--project", str(PROJECT), "greenkeep"]
    argv += ["--push"] * push
    argv += ["--budget", str(budget)] if budget is not None else []
    argv += ["--model", model] if model and model != DEFAULT_MODEL else []
    argv += ["--review-model", review_model] if review_model and review_model != DEFAULT_REVIEW_MODEL else []
    return argv + ["--keep"] * keep + list(repos)


def _start(argv: list[str], push: bool) -> None:
    label = "greenkeep run (push)" if push else "greenkeep dry run"
    if run_pool.submit(argv, label=label, cwd=PROJECT) is None:
        ui.notify("The run queue is full: wait for a run to finish", type="warning")


def _dry_run() -> None:
    _start(run_argv([], False, None, DEFAULT_MODEL, DEFAULT_REVIEW_MODEL, False), False)


def _config_repos() -> list[str]:
    try:
        return [t.spec for t in load().repos]
    except ConfigError:
        return []


def _confirm_push(go) -> None:
    with ui.dialog() as dialog, layout.section_card():
        layout.heading("Open pull requests and merge?", level=2)
        ui.label("This run opens real issues and pull requests and merges them on GitHub.")

        def confirmed() -> None:
            dialog.close()
            go()

        with layout.actions_row():
            layout.button("Cancel", kind="quiet", on_click=dialog.close)
            layout.button("Open PRs and merge", kind="danger", on_click=confirmed)
    dialog.open()


def _options_dialog() -> ui.dialog:
    """Every CLI option of a run; empty repos and budget mean the config's."""
    with ui.dialog() as dialog, layout.section_card():
        layout.heading("Run with options", level=2)
        repos = ui.select(_config_repos(), multiple=True, new_value_mode="add-unique",
                          label="Repos (empty: from config)").props(
            "outlined dense stack-label use-chips").classes("w-full")
        push = ui.switch("Push: open issues and PRs and merge (off: local dry run)")
        budget = layout.field("Budget (empty: from config)", kind="number")
        model = layout.field("Model", value=DEFAULT_MODEL)
        review = layout.field("Review model", value=DEFAULT_REVIEW_MODEL)
        keep = ui.checkbox("Keep the throwaway clones")

        def start() -> None:
            raw = (budget.value or "").strip()
            if raw and not raw.isdigit():
                budget.error = "A whole number"
                return
            argv = run_argv(list(repos.value or []), push.value, int(raw) if raw else None,
                            model.value.strip(), review.value.strip(), keep.value)
            dialog.close()
            _start(argv, push.value)

        with layout.actions_row():
            layout.button("Cancel", kind="quiet", on_click=dialog.close)
            layout.button("Start", kind="primary",
                          on_click=lambda: _confirm_push(start) if push.value else start())
    return dialog


def _log_card() -> ui.code:
    with layout.section_card(recessive=True):
        layout.heading("Recent events", level=2)
        return ui.code("", language="text").classes("w-full").style(
            f"max-height: {LOG_MAX_HEIGHT}; overflow: auto")


@dataclass
class _Page:
    """The page's live parts; refresh() redraws them from the state and the log."""
    state: State
    top: ui.column
    status: ui.select
    search: ui.input
    findings: table.EmptyAwareTable
    log: ui.code

    def refresh(self) -> None:
        self.top.clear()
        with self.top:
            _pause_card(self.refresh)
            _summary(self.state)
        status = STATUSES[self.status.value]
        self.findings.set_rows(table_rows(self.state.rows(), status, self.search.value or ""))
        self.log.set_content(log_tail(_read_log()))


def _findings_table() -> table.EmptyAwareTable:
    findings = table.empty_aware_table(COLUMNS, row_key="key", pagination={"rowsPerPage": 25},
                                       empty_text="No findings yet")
    findings.table.add_slot("body-cell-link", LINK_SLOT)
    return findings


@ui.page("/")
def index() -> None:
    with ui.header().classes("items-center justify-between"):
        layout.heading("greenkeep", level=1)
        theme.theme_toggle()
    with layout.page_container(max_width=layout.PAGE_MAX_WIDTH_WIDE):
        top = ui.column().classes("w-full")
        status, search = _filters()
        findings = _findings_table()
        run_monitor.attach_run_monitor()
        page = _Page(State(), top, status, search, findings, _log_card())
    status.on_value_change(lambda _: page.refresh())
    search.on_value_change(lambda _: page.refresh())
    page.refresh()
    ui.timer(REFRESH_SECONDS, page.refresh)


@app.get(port.MARKER_PATH)
def _marker() -> dict:
    return port.marker()


async def _stop_runs() -> None:  # one-arg handlers would get the App
    """A dry run (minutes of Claude calls) must not outlive the dashboard."""
    await run_pool.terminate_all_live_processes()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="greenkeep-ui", description=__doc__)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-show", action="store_true", help="do not open a browser tab")
    a = p.parse_args(argv)
    choice = port.choose(a.port, ask=port.ask_tty)
    if not choice.serve:
        print(f"greenkeep dashboard: {choice.url}")
        if not a.no_show:  # a second click on the menu entry brings the running one up
            webbrowser.open(choice.url)
        return 0
    if choice.port != a.port:
        print(f"port {a.port} is taken by another app, using {choice.port}", file=sys.stderr)
    texts.TEXTS.update(texts.EN)  # kit strings are Russian by default; greenkeep speaks English
    theme.apply_theme()
    app.on_shutdown(_stop_runs)
    ui.run(host=port.HOST, port=choice.port, title="greenkeep", show=not a.no_show,
           reload=False)
    return 0


if __name__ in {"__main__", "__mp_main__"}:
    raise SystemExit(main())
