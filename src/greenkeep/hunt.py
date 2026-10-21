"""Bug hunt: the Hunter suspects bugs in one module, the Skeptic keeps what it cannot refute.

What survives is a finding like any other; the gate's red-then-green test is the referee
(docs/adr/0002). One module per repo per run, never the same unchanged module twice.
"""

from __future__ import annotations

import hashlib
import logging
from textwrap import shorten

from claude_cli import ClaudeCliError, UsageLimitError

from . import llm
from .core import Job
from .findings import UNTESTED_MIN_STATEMENTS, UNTESTED_PERCENT, Finding, _digest
from .gate import is_test_path

log = logging.getLogger("greenkeep")

TITLE_MAX = 60


def _sha(job: Job, path: str) -> str:
    return hashlib.sha1((job.ws.root / path).read_bytes()).hexdigest()


def _mark(job: Job, path: str) -> str:
    return f"hunt:{job.ws.name}:{path}"


def pick(job: Job) -> str | None:
    """The biggest covered module not hunted since it last changed: its tests can show a bug,
    and the gate's coverage rule holds for a fix in it."""
    files = job.base.tests.cov.get("files", {})
    fit = [(data["summary"]["num_statements"], path) for path, data in files.items()
           if not is_test_path(path) and (job.ws.root / path).is_file()
           and data["summary"]["num_statements"] >= UNTESTED_MIN_STATEMENTS
           and data["summary"]["percent_covered"] >= UNTESTED_PERCENT
           and job.state.mark(_mark(job, path)) != _sha(job, path)]
    return max(fit)[1] if fit else None


def _finding(job: Job, path: str, c: llm.Claim) -> Finding | None:
    """None for a claim on a line the module does not have."""
    text = job.ws.read_line(path, c.line)
    if not text.strip():
        return None
    title = shorten(c.title, TITLE_MAX, placeholder="…")
    # the line number too: two claims on identical lines (`return None`) are two bugs
    fp = f"bug:{path}:{c.line}:{_digest(text.strip())}"
    return Finding("bug", "hunt", path, c.line, c.why, fp, title=f"{title} ({path}:{c.line})")


def hunt(job: Job) -> list[Finding]:
    """Workable bug findings in one module. The module is marked hunted only when none are
    left: a bug still open (timed out) is found again next run and gets its next attempt."""
    path = pick(job)
    if path is None:
        return []
    log.info("%s: hunting bugs in %s", job.ws.name, path)
    kept = _surviving_claims(job, path)
    if kept is None:
        return []  # a failed hunt leaves the module unmarked: it is retried next run
    found = [f for f in (_finding(job, path, c) for c in kept)
             if f and job.state.is_workable(job.ws.name, f.fingerprint)]
    if not found:
        job.state.set_mark(_mark(job, path), _sha(job, path))
    return found


def _surviving_claims(job: Job, path: str) -> list[llm.Claim] | None:
    """The Hunter's claims the Skeptic kept; None when either call failed or its reply was
    unreadable."""
    try:
        claims = llm.hunt(path, job.opts.model, job.ws.root)
        kept = llm.doubt(path, claims, job.opts.review_model, job.ws.root) if claims else claims
    except UsageLimitError:
        raise
    except (ClaudeCliError, TimeoutError) as exc:
        log.warning("%s: bug hunt failed: %s", job.ws.name, exc)
        return None
    if claims is None or kept is None:
        log.warning("%s: bug hunt reply unreadable", job.ws.name)
        return None
    log.info("  %d suspected, %d kept by the skeptic", len(claims), len(kept))
    return kept
