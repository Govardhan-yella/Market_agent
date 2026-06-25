# Market Agent — Improvement Sequence

## Phase 0 — Stabilize
- [x] Shared formatter utility (`utils.py`) to deduplicate `_format_table` and trend helpers
- [x] Structured logging: stdout + rotating file logging
- [x] SIGTERM / SIGINT gracefully stops the scheduler and notifies on Telegram
- [x] `.gitignore` hardened
- [x] Cleaned up `report.py`, `alerts.py`, `main.py`

## Phase 1 – Retry / backoff (current)
Objectives: harden external fetch paths and add visibility.

1. Add retry/backoff decorators for NSE, Yahoo, GDELT, NewsAPI, Finnhub.
2. Add request timeout constants in `config.py`.
3. Log HTTP status codes for failed fetches.
4. Add circuit-breaker per data source (cooldown + degraded-mode stubs).

## Phase 2 – Observability
Objectives: easier ops without breaking user’s chat UX.

5. Add Telegram `/status` command: uptime, last run timestamps, DB stats.
6. Add `/settings` to summarize active configuration.
7. Structured JSON logging for machine reads.

## Phase 3 – Health checks
Objectives: detach from just Telegram loop for liveness.

8. Add optional `FastAPI` `/health` endpoint.
9. Add `/report` endpoint to trigger reports.
10. Expose last alert + last report state.

## Phase 4 – Data retention & cleanup
Objectives: keep disk and DB tidy automatically.

11. Auto gzip reports older than 7 days; keep `latest.json` + 30 uncompressed.
12. Weekly DB vacuum/rollup job.
13. Expire old alert rows by policy.

## Phase 5 – Scheduling upgrade
Objectives: ditch the `while True` loop for robust scheduling.

14. Replace loop with `APScheduler`.
15. Add misfire handling, pausing on holiday/weekend detection.
16. Persistent job stores for crash recovery.

## Phase 6 – Modular split
Objectives: reduce `data.py` surface area.

17. Split `data.py` into: `nse.py`, `yahoo.py`, `news.py`, `technicals.py`.
18. Move HTTP + formatting helpers into a `transport` layer.
19. Keep all existing import surfaces for no-break upgrade.

## Phase 7 – Test coverage
Objectives: keep regressions low.

20. Add baseline unit tests for formatter, DB flows, and DSL.
21. Add integration fixture report that verifies `latest.json` shape.

## Phase 8 – Extensibility
Objectives: make it easy to add new data.

22. Plugin-style provider registry.
23. Add `data_manifest.json` for enabling/disabling sources.
24. Add webhook + event-emitter for breaking alerts.
