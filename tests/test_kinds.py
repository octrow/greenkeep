import pytest

from greenkeep.findings import Finding
from greenkeep.kinds import Kind, kind_of


def finding(kind: str, rule: str) -> Finding:
    return Finding(kind, rule, "a.py", 1, "m", f"{kind}:{rule}")


def test_work_order_is_security_bugs_tests_upgrades_style():
    found = [finding("ruff", "E501"), finding("outdated", "x==2"), finding("untested", "cov"),
             finding("ruff", "F401"), finding("vuln", "y==3")]
    ordered = sorted(found, key=lambda f: kind_of(f).priority(f))
    assert [f.rule for f in ordered] == ["y==3", "F401", "cov", "x==2", "E501"]


def test_kind_base_cannot_be_instantiated():
    with pytest.raises(TypeError):
        Kind()
