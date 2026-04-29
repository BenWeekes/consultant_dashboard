# consultant_dashboard — Repo Card

> Flask service for consultant/admin operations, encrypted session artifacts, and internal APIs used by the live session stack. Product-specific behavior should be documented here, not in the sample repo recipes.

## Identity
| Field | Value |
| --- | --- |
| Repo | `BenWeekes/consultant_dashboard` |
| Type | `api-service` |
| Language | `Python 3 + Flask + SQLite` |
| Deploy Target | Separate service process; local dev uses Flask on port `8090` |
| Owner | `Ben Weekes / consultant-dashboard` |
| Last Reviewed | `2026-04-28` |

## L1 — Summaries

The Audience column helps agents prioritise: **Use** = consuming the repo's functionality, **Maintain** = modifying internals.

| File | Purpose | Audience |
| --- | --- | --- |
| [01_setup](L1/01_setup.md) | Environment setup, quick commands, env vars, local bootstrapping | Use & Maintain |
| [02_architecture](L1/02_architecture.md) | Service boundaries, request flows, data/storage model | Maintain |
| [03_code_map](L1/03_code_map.md) | Directory map and “where does X live?” guidance | Maintain |
| [04_conventions](L1/04_conventions.md) | Repo patterns for config, routes, storage, auth, testing | Maintain |
| [05_workflows](L1/05_workflows.md) | Step-by-step guides for common changes | Maintain |
| [06_interfaces](L1/06_interfaces.md) | Internal API contracts, CLI commands, schema boundaries | Use & Maintain |
| [07_gotchas](L1/07_gotchas.md) | Critical caveats, environment traps, known limitations | Maintain |
| [08_security](L1/08_security.md) | Trust boundaries, encryption, sessions, secret handling | Maintain |

## L2 — Deep Dives

- [Deep Dive Index](L1/L2/_index.md)
