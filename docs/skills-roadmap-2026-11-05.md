# Навыки с рынка: что добавить (2026-11-05)

Источник: `~/dev/jobs/data/corpus_roadmap.md` (11.5k вакансий, 2026-11-05) против [system-design](system-design.md) и [logic-map](logic-map.md). Берём только то, что нужно самому проекту; чего проект не требует — в «Отвергнуто». Сводка по всем репо: `~/dev/docs/skills-roadmap-report-2026-11-05.md`.

## Предложения

- [ ] **[docker, github actions, ghcr]** К открытому B3 (Dockerfile) добавить job сборки образа в GHCR в существующем `release.yml`. B3 не дублировать, только расширить. Усилие S.

## Отвергнуто

- OpenTelemetry: пакетный прогон раз в сутки, лога, Telegram-дайджеста и дашборда хватает (rung 1).
- Terraform: одна VM, ставится один раз (если нужен — в oci-server).
- Kafka/очереди: ≤3 merge в день. MCP: отдавать нечего, дашборд есть.
