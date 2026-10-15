<p align="center"><img src="docs/assets/hero.svg" width="100%" alt="greenkeep: детерминированные инструменты находят проблемы, LLM пишет по патчу на находку, вливается только если тесты уже покрывали каждую изменённую строку"></p>

[![test](https://github.com/octrow/greenkeep/actions/workflows/test.yml/badge.svg)](https://github.com/octrow/greenkeep/actions/workflows/test.yml)
[![release](https://img.shields.io/github/v/release/octrow/greenkeep)](https://github.com/octrow/greenkeep/releases/latest)
[![python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<p><a href="README.md">English</a> | <b>Русский</b></p>

greenkeep сопровождает ваши собственные Python-репозитории по расписанию. Проблемы находят детерминированные инструменты, LLM пишет по одному патчу на каждую находку, а PR с исправлением вливается сам, только если весь набор тестов зелёный и каждая изменённая строка уже была покрыта тестами. Он для разработчика с кучей pet-проектов, который хочет, чтобы они получали мелкие безопасные исправления без ручного ревью каждого.

Каждый PR закрывает одну находку, а в сообщении коммита указаны модель и хэш промпта, которые дали патч.

## Как выглядит запуск

Настоящий пробный запуск (dry run) на маленьком демо-репозитории с изменяемым значением аргумента по умолчанию (ruff `B006`). Патч написала бесплатная модель через OmniRoute, весь запуск занял около 10 секунд:

```console
$ greenkeep ~/dev/tidy --keep
tidy: B006: Do not use mutable data structures for argument defaults (src/tidy/tags.py)
  merged B006: Do not use mutable data structures for argument defaults (src/tidy/tags.py) via gemini-3.8-flash-tiered
```

```diff
    fix: B006: Do not use mutable data structures for argument defaults (src

    Finding: `B006` at `src/tidy/tags.py:1`: Do not use mutable data structures for argument defaults
    Patched by: `gemini-3.8-flash-tiered` (prompt `cbd0ba095834`)
    Gate: full test suite green; every changed line was covered before the change; no new lint findings.

-def add_tag(tag, tags=[]):
+def add_tag(tag, tags=None):
+    if tags is None:
+        tags = []
     tags.append(tag.strip().lower())
     return tags
```

В пробном запуске «merged» значит «влито в одноразовый клон». С `--push` то же изменение становится plan issue и PR на GitHub, и его вливают squash-ом, когда проходят обязательные проверки CI.

## Как это работает

1. Клон: каждый переданный репозиторий клонируется в `~/.cache/greenkeep`, ваша рабочая копия не трогается. Если тесты красные ещё до каких-либо изменений, репозиторий пропускается.
2. Поиск: находки дают только детерминированные инструменты: ruff (сначала правила безопасности, багов и pyflakes) и coverage (модули совсем без тестов).
3. Патч: на каждую находку LLM получает её и код вокруг и возвращает правки SEARCH/REPLACE. Для модуля без тестов он пишет характеризационные тесты, которые фиксируют, что код делает сейчас.
4. Гейт: изменение остаётся, только если весь набор тестов проходит, каждая изменённая строка была покрыта до изменения (для нового файла тестов: покрытие модуля выросло) и ruff не сообщает ничего нового.
5. Влить или отложить: прошедшее изменение коммитится (а с `--push` открывается как PR и вливается на зелёном). На находку даётся три попытки, после этого она откладывается с меткой `needs-human`.

Почему детерминированные находки, а не свободный кодинг-агент: [ADR 0001](docs/adr/0001-deterministic-findings-llm-patches.md). Термины: [CONTEXT.md](CONTEXT.md).

## Установка

Linux и macOS:

```bash
curl -LsSf https://raw.githubusercontent.com/octrow/greenkeep/main/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/octrow/greenkeep/main/install.ps1 | iex"
```

Скрипт ставит [uv](https://docs.astral.sh/uv/), если его нет, а затем wheel из последнего релиза. Повторный запуск обновляет, `... | sh -s -- --uninstall` удаляет greenkeep. Чтобы прочитать скрипт перед запуском, скачайте `install.sh` и выполните `sh install.sh`. Или поставьте напрямую через uv:

```bash
uv tool install git+https://github.com/octrow/greenkeep
```

Ещё понадобятся:

- git и [GitHub CLI](https://cli.github.com/) с выполненным `gh auth login` (только для `--push`);
- [Claude Code](https://docs.claude.com/en/docs/claude-code) с выполненным входом, вызывается через [claude-subprocess](https://github.com/octrow/claude-cli) (`omniroute run claude -p`): Sonnet пишет патчи, Opus проверяет их прямо в клоне, только на чтение.

## Использование

```bash
greenkeep ~/dev/repo-a ~/dev/repo-b          # dry run: nothing leaves the machine except LLM calls
greenkeep ~/dev/repo-a --push --budget 3     # open issues and PRs, merge on green, at most 3 merges a day
```

| Опция | По умолчанию | Что делает |
|---|---|---|
| `--push` | выкл. | Открывает plan issue и PR, ждёт обязательных проверок, вливает squash-ом |
| `--budget N` | 3 | Сколько PR с исправлениями влить за день, по всем репозиториям |
| `--model NAME` | `claude-sonnet-5-5` | Модель Claude Code, которая пишет патчи |
| `--review-model NAME` | `claude-opus-5-5` | Модель Claude Code, которая проверяет каждый патч в клоне |
| `--keep` | выкл. | Оставляет одноразовые клоны в `~/.cache/greenkeep` для разбора |

За один запуск делается не больше одного исправления на репозиторий, поэтому greenkeep рассчитан на запуск по расписанию. Хватит строки в cron раз в день:

```cron
0 6 * * * $HOME/.local/bin/greenkeep $HOME/dev/repo-a $HOME/dev/repo-b --push >> $HOME/.local/state/greenkeep.log 2>&1
```

Состояние (попытки по каждой находке, вливания за день) хранится в `~/.local/share/greenkeep/state.db`. Пробный запуск его не пишет.

## Что нужно репозиторию

- Python под управлением uv: `pyproject.toml` в корне (`uv sync` должен работать).
- Тесты, которые pytest может запустить и которые сейчас проходят. Репозиторий совсем без тестов подходит: greenkeep начнёт с характеризационных тестов.
- Для `--push`: GitHub-remote, в который можно пушить и вливать.

## Чем отличается

- Dependabot, Renovate: они обновляют зависимости. greenkeep пока ничего не обновляет, он исправляет находки в коде и добавляет тесты. Их можно запускать на одном репозитории.
- ruff `--fix`, автофиксы pre-commit.ci: они применяют только то, что ruff умеет исправить механически. greenkeep берёт безопасный фикс ruff, если он есть, и зовёт LLM только для находок, которые ruff не исправляет, как `B006` выше; оба вида проходят один гейт и получают свой PR.
- Кодинг-агенты (Copilot coding agent, OpenHands, Sweep): агент сам решает, что и сколько менять. Здесь проблему выбирает детерминированный инструмент, патч ограничен одной находкой, и ничего не вливается, если тесты не покрывали каждую затронутую строку. Агенты умеют больше; greenkeep старается быть скучным и воспроизводимым.
- [patchwork](https://github.com/patched-codes/patchwork): ближе всего по духу (сценарии AutoFix). Но расписание, дневной бюджет, гейт по покрытию и цикл issue → PR → merge он не берёт на себя, а greenkeep построен вокруг них.

## Статус

С 2026-10-16 ежедневно работает на репозиториях автора. Что сделано и что дальше: [Status and roadmap](README.md#status-and-roadmap) (на английском).

## Участие и безопасность

См. [CONTRIBUTING.ru.md](CONTRIBUTING.ru.md) и [SECURITY.ru.md](SECURITY.ru.md). Изменения: [CHANGELOG.md](CHANGELOG.md).

## Лицензия

[MIT](LICENSE)
