**English** | [Русский](CONTRIBUTING.ru.md)

# Contributing

Bug reports, ideas and pull requests are welcome. By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). For a large change, open an issue first so we can agree on the approach.

## Set up

You need Python 3.12+, [uv](https://docs.astral.sh/uv/) and git. The [GitHub CLI](https://cli.github.com/) and an [OmniRoute](https://github.com/diegosouzapw/OmniRoute) key are needed only for real runs, not for the tests.

```bash
git clone https://github.com/octrow/greenkeep && cd greenkeep
uv sync
uv run greenkeep --help
```

## Before a pull request

```bash
uvx ruff check .
uv run pytest -q
```

- Add a test for new behavior. Tests never call GitHub or a model: the end-to-end tests run real git, uv, ruff and pytest on throwaway repos and fake only the LLM.
- Keep README.md and README.ru.md in sync when you change behavior or options.
- Use the words from [CONTEXT.md](CONTEXT.md) (Tended repo, Finding, Fix PR, Gate...). A larger design decision gets an ADR in [docs/adr/](docs/adr/).
- Findings come from deterministic tools; the LLM only writes the patch ([ADR 0001](docs/adr/0001-deterministic-findings-llm-patches.md)).
- Commit messages: `<type>: <description>`, where type is feat, fix, refactor, docs, test, chore, perf or ci.

## Releases

Bump `version` in pyproject.toml, add a CHANGELOG entry, merge to main, then push a tag `vX.Y.Z`. The release workflow runs the tests, builds the wheel and publishes the GitHub release.
