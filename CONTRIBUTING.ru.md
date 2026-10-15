[English](CONTRIBUTING.md) | **Русский**

# Как поучаствовать

Баг-репорты, идеи и pull request'ы приветствуются. Участвуя, вы принимаете [Кодекс поведения](CODE_OF_CONDUCT.ru.md). Перед большим изменением откройте issue, чтобы договориться о подходе.

## Подготовка

Нужны Python 3.12+, [uv](https://docs.astral.sh/uv/) и git. [GitHub CLI](https://cli.github.com/) и ключ [OmniRoute](https://github.com/diegosouzapw/OmniRoute) понадобятся только для настоящих запусков, тестам они не нужны.

```bash
git clone https://github.com/octrow/greenkeep && cd greenkeep
uv sync
uv run greenkeep --help
```

## Перед pull request

```bash
uvx ruff check .
uv run pytest -q
```

- На новое поведение добавьте тест. Тесты не ходят ни в GitHub, ни к модели: end-to-end тесты гоняют настоящие git, uv, ruff и pytest на одноразовых репозиториях, подменяется только LLM.
- Если меняете поведение или опции, обновите README.md и README.ru.md.
- Используйте термины из [CONTEXT.md](CONTEXT.md) (Tended repo, Finding, Fix PR, Gate...). Крупное архитектурное решение оформите как ADR в [docs/adr/](docs/adr/).
- Находки дают детерминированные инструменты, LLM только пишет патч ([ADR 0001](docs/adr/0001-deterministic-findings-llm-patches.md)).
- Сообщения коммитов: `<type>: <description>`, где type - feat, fix, refactor, docs, test, chore, perf или ci.

## Релизы

Поднимите `version` в pyproject.toml, добавьте запись в CHANGELOG, влейте в main и запушьте тег `vX.Y.Z`. Release-workflow прогонит тесты, соберёт wheel и опубликует релиз на GitHub.
