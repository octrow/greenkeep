import pytest

from greenkeep.workspace import kept_addopts, repo_addopts


@pytest.mark.parametrize(("addopts", "kept"), [
    ("-ra --cov=src --cov-report term-missing -m 'not slow'", ["-ra", "-m", "not slow"]),
    ("--cov src --strict-markers", ["--strict-markers"]),
    ("--cov --no-cov-on-fail -p pytest_cov -x", ["-x"]),
    ("-n auto --dist loadfile -q", ["-q"]),
    ("-n4 --numprocesses=2 --tb=short", ["--tb=short"]),
])
def test_coverage_and_xdist_options_are_dropped_the_rest_is_kept(addopts, kept):
    assert kept_addopts(addopts) == kept


def test_addopts_come_from_the_file_pytest_itself_would_read(tmp_path):
    assert repo_addopts(tmp_path) == []
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = ["-ra", "--cov=pkg"]\n')
    assert repo_addopts(tmp_path) == ["-ra"]
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -x --cov\n")
    assert repo_addopts(tmp_path) == ["-x"]  # pytest.ini wins, like in pytest
    (tmp_path / "pytest.ini").unlink()
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\naddopts = ["-q"]\n')
    assert repo_addopts(tmp_path) == ["-q"]
