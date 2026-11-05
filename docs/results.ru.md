# Итоги первой недели

[README](../README.ru.md)

Первая неделя на 19 репозиториях автора (2026-10-15, 2026-10-22), по базе состояния greenkeep и самим PR:

| | Влито | Как |
|---|---|---|
| Уязвимые зависимости | 17 | `uv lock --upgrade-package`, только lock-файл: certifi [PYSEC-2026-4320](https://github.com/octrow/buy-smarter/pull/18) в 9 репозиториях за один день, [Pillow](https://github.com/octrow/finance-insights/pull/10), [werkzeug](https://github.com/octrow/finance-insights/pull/12), [aiohttp](https://github.com/octrow/tiktok-lazy-follower/pull/3)… |
| Устаревшие зависимости | 2 | так же, только в пределах мажорной версии ([alembic](https://github.com/octrow/buy-smarter/pull/15)) |
| Находки линтера | 12 | 6 через `ruff --fix`, 6 исправила модель, например незакрытый файл ([finance-insights#6](https://github.com/octrow/finance-insights/pull/6)) |
| Характеризационные тесты | 1 | [repo-scout#10](https://github.com/octrow/repo-scout/pull/10) |

32 PR в 16 репозиториях. Отдельное ревью всех 32 (Opus читал каждый дифф): 23 хороших, 5 приемлемых, 4 плохих:

- [repo-scout#10](https://github.com/octrow/repo-scout/pull/10): 559 строк тестов на завендоренную копию модуля из другого репозитория, ещё из первой версии без ревьюера и без лимита строк. Теперь завендоренный код находок не получает.
- [whisper-gpu#2](https://github.com/octrow/whisper-gpu/pull/2): `ruff --fix` для лишних `noqa` удалил и пояснения, написанные после них. Это правило теперь чинит модель и пояснения оставляет.
- [linux-coding#23](https://github.com/octrow/linux-coding/pull/23) и [claude-cli#4](https://github.com/octrow/claude-cli/pull/4): верно, но код стал хуже, логгер в тесте только ради `S110`, строка, сдвинутая в нулевую колонку. Промпты патчера и ревьюера теперь требуют идиоматичного исправления для правила.

Что не получилось: 20 находок отложены после трёх попыток, 16 из них, характеризационные тесты (неверные догадки о поведении, новый lint, отказ ревьюера, тесты не там, где их держит репозиторий). Хорошие тесты для непокрытого кода, самая трудная его работа, и проваливает он чаще всего её.
