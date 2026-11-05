# Конфиг, расписание, дайджест

[README](../README.ru.md)

## Конфиг

`~/.config/greenkeep/config.toml`:

```toml
budget = 3            # merged PRs per weekday
weekend_budget = 1
boost_until = 2026-10-30T03:00:00+05:00  # optional: until then, a run every hour
boost_budget = 15                        # and this budget; every repo bug-hunts and lints
                                         # for complexity, SIM, PERF, UP and RUF too
                                         # and Opus plans each hard change before Sonnet writes it
login_hours = "Thu 21:00 - Fri 03:00"  # optional: the Claude Code login pays only then;
                                       # outside them calls go through OmniRoute's `claude/`
                                       # connection (needs omniroute and OMNIROUTE_API_KEY)
backup = "~/greenkeep-state"  # optional: a checkout of a private repo; after each scheduled
                              # run state.db's SQL dump is committed and pushed there.
                              # Restore: sqlite3 ~/.local/share/greenkeep/state.db < state.sql
repos = [
  "~/dev/my-repo",
  "owner/name-without-local-checkout",
  { path = "~/dev/monorepo", tests = "app" },  # the suite runs from app/, not the root
  { path = "~/dev/lib", hunt = true },  # bug hunt once nothing else is left to do
]
focus = ["~/dev/my-repo"]   # optional: these run first every run
```

Исправления уязвимых зависимостей не требуют бюджета и не тратят его.

## Расписание

```bash
cp contrib/systemd/greenkeep.* ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now greenkeep.timer
```

Таймер срабатывает каждый час, и greenkeep сразу выходит, если сегодняшний дайджест уже ушёл, поэтому запуск, прерванный выключением, начнётся заново после следующей загрузки. Во время буста каждое срабатывание, это запуск. Если Claude не отвечает (лимит, выход из аккаунта, API лежит), запуск останавливается, не засчитывая попытку, и день остаётся открытым: следующее срабатывание попробует снова.

Юнит запускает `uv run` из рабочей копии в `~/dev/greenkeep`; для установленной утилиты поставьте `ExecStart=%h/.local/bin/greenkeep --push` и уберите `WorkingDirectory`. Подойдёт и любой другой планировщик: запускайте `greenkeep --push` каждый час с переменными из `~/.config/greenkeep/env` и с `gh`, `claude` и `uv` в `PATH`.

## Дайджест, пауза, дашборд

Дайджест в Telegram: `GREENKEEP_TG_BOT_TOKEN` и `GREENKEEP_TG_CHAT_ID` в `~/.config/greenkeep/env` (без них: уведомление на рабочем столе). Пауза: `touch ~/.config/greenkeep/PAUSE`, чтобы продолжить, удалите файл. Откат влитого greenkeep PR или красный CI на main после него (такой greenkeep откатывает сам) отправляет это репо в недельное остывание; какой PR, пишет дайджест.

Дашборд (находки, пауза и продолжение, пробный запуск с живым логом, последние события):

```bash
greenkeep-ui            # http://127.0.0.1:8095
```

Если порт занят другим приложением, дашборд берёт следующий свободный; если там уже работает дашборд greenkeep, он спрашивает, перезапустить ли его (по умолчанию нет), и печатает ссылку.
