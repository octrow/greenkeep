"""docs/logic-map.md names real modules: a rename that forgets the map fails here."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "greenkeep"  # where Module: paths start
MAPS = [ROOT / "docs" / "logic-map.md", ROOT / "docs" / "logic-map.ru.md"]


def _module_refs(path: Path) -> list[str]:
    lines = [line for line in path.read_text().splitlines() if line.startswith("Module:")]
    return [ref for line in lines for ref in re.findall(r"`([^`]+)`", line)]


def test_every_module_line_points_to_an_existing_module() -> None:
    for path in MAPS:
        refs = _module_refs(path)
        missing = [
            ref
            for ref in refs
            if not (PACKAGE / f"{ref}.py").is_file() and not (PACKAGE / ref).is_dir()
        ]
        assert refs, path
        assert missing == [], path
