import pytest

pytest_plugins = ["nicegui.testing.user_plugin"]  # temp storage path for the page tests


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    """The end-to-end tests commit in throwaway repos; CI runners have no git identity."""
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")
