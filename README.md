# pelatro_ui

RA_product monorepo — the RADONaix Rating Assurance platform.

| Directory | Stack | Purpose |
| --- | --- | --- |
| [`ra_demo/`](ra_demo/) | React + Vite + TypeScript | Web UI |
| [`ra_backend/`](ra_backend/) | FastAPI | Monitoring, auth, pipelines |
| [`ra_rating_backend/`](ra_rating_backend/) | FastAPI | Rating assurance service |

## Setup

Each service ships a `.env.example`. Copy it to `.env` and fill in real values —
`.env` files are gitignored and never committed.

```bash
cp ra_rating_backend/.env.example ra_rating_backend/.env
```

`JWT_SECRET` and `JWT_ALGORITHM` must be **identical** in `ra_backend/.env` and
`ra_rating_backend/.env`; the rating service only verifies tokens the monitoring
service issues, and a mismatch returns 401 with no other symptom.

Dependencies are not vendored — install them per service (`npm install` for the
UI, a virtualenv plus `requirements.txt` for each backend).

## Planning docs

- [RATING_ASSURANCE_PLAN.md](RATING_ASSURANCE_PLAN.md)
- [RATING_UX_ENHANCEMENT_PLAN.md](RATING_UX_ENHANCEMENT_PLAN.md)
- [RULE_MANAGEMENT_ENHANCEMENT_PLAN.md](RULE_MANAGEMENT_ENHANCEMENT_PLAN.md)
