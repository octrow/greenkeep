import pytest


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    """The end-to-end tests commit in throwaway repos; CI runners have no git identity."""
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")
