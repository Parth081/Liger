# LIGER — Project Operating Manual

> **This file is the entrypoint.** Read it fully before touching any code in this repo.
> It tells you what this project is, the rules you must never break, where the specs live,
> and how to execute a build phase with parallel tracks.

---

## 1. What this is

**Liger** manufactures curtains, fabrics and all types of blinds in India.
This repo is the production **Order, Credit & Sales platform**.

**The problem it solves:** dealers place new orders while previous orders are unpaid. Everything is
offline today, so nobody knows the real outstanding at the moment of order-taking.

**What the system does:**
1. Takes orders in Liger's format — **Design No + Length × Breadth × Qty → sq.ft → amount** (min 11 sq.ft rule)
2. Prices them from a versioned rate card, with GST
3. Enforces credit rules automatically — blocks dealers with old unpaid dues
4. Chases payment on WhatsApp/SMS with an escalating reminder ladder
5. Collects money online (UPI / cards / netbanking) and tracks cash with admin confirmation
6. Shows the owner sales and outstanding by month / customer / region / distributor

**Build order:** Web platform first (Phases P0–P9), then a React Native mobile app on the *same* API (P10).

---

## 2. Non-negotiable engineering rules

Break any of these and the build is wrong. No exceptions, no "just for now".

| # | Rule | Why |
|---|---|---|
| **R1** | **All money is `BIGINT`, stored in paise.** Never `Float`, never `Double`. Use the `Money` type in `app/core/money.py`. | Floats lose paise. This is a financial system. |
| **R2** | **The ledger is append-only.** Corrections are posted as reversing entries. Never `UPDATE` or `DELETE` a ledger row. | Auditability. Balances must be reconstructable. |
| **R3** | **Balances are derived from the ledger, then cached.** Never a mutable `balance` column as the source of truth. | Stored counters drift. Derived values cannot. |
| **R4** | **API-first.** All business logic lives behind `/api/v1`. Zero business logic in the frontend. | The mobile app (P10) reuses the same API with no rework. |
| **R5** | **Alembic is the only way schema changes.** Never `Base.metadata.create_all()` outside tests. | Reproducible deploys. |
| **R6** | **Every money-touching endpoint is idempotent** (order create, payment, webhook) via an `Idempotency-Key`. | Retries and double-taps must not double-charge. |
| **R7** | **Credit checks run inside a transaction with `SELECT … FOR UPDATE` on the customer row.** | Two simultaneous orders must not both pass the limit check. |
| **R8** | **Every business rule is a `Setting`**, versioned and audit-logged. Never hardcode `11`, `15 days`, `80%`. | The owner changes rules without a deploy. |
| **R9** | **Order lines store a frozen price snapshot** (rate, rule version, sq.ft breakdown). | Changing the rate card must never re-price a past order. |
| **R10** | **All side effects are queued jobs** (Celery), never inline in the request. | A slow WhatsApp API must never slow down an order. |
| **R11** | **Permissions enforced server-side on every endpoint.** UI hiding is cosmetic only. | Security. |
| **R12** | **Every rule implemented must cite its `BR-` id** from `docs/BUSINESS_RULES.md` in a code comment and in its test name. | Traceability from spec → code → test. |
| **R13** | **No `SELECT *`, every FK indexed, cursor pagination on every list endpoint.** No `OFFSET` paging. | Scale to 10k+ dealers. |
| **R14** | **Secrets never in git.** `backend/.env` must stay git-ignored. | Security. |

---

## 3. Current state of the repo

A **Phase-0 prototype** exists. It is a skeleton, not production code.

```
E:\Liger
├── backend/           FastAPI + SQLAlchemy prototype  (to be rebuilt on the P0 foundations)
│   ├── app/           api.py crud.py engines.py models.py pricing.py schemas.py auth.py database.py
│   ├── .env           ⚠️ verify git-ignored; rotate any committed credential
│   └── requirements.txt
├── frontend_new/      Next.js 16 + React 19 + Tailwind v4  (has its own AGENTS.md — READ IT)
│   └── app/           layout.tsx page.tsx cart/ components/
├── docs/              ← all specifications live here
├── docker-compose.yml Postgres 15 (port 5433) + Redis 7
└── CLAUDE.md          this file
```

**Keep from the prototype:**
- `backend/app/pricing.py` — one shared sq.ft calculator for preview + cart + order. Correct pattern.
- `backend/app/engines.py::compute_customer_due` — due derived from the ledger, not stored. Correct pattern.

**Must be rebuilt in P0:**
- `Float` money columns → integer paise (**R1**)
- Credit blocking on limit-utilisation only → must block on **overdue age** (`BR-CR-*`)
- `Notification` table with nothing sending → real queue + provider adapters
- No customer auth, no RBAC, no audit log, no migrations discipline, no tests, no CI

⚠️ **`frontend_new/AGENTS.md` warns that this Next.js version has breaking changes vs. training data.**
Read `node_modules/next/dist/docs/` before writing any frontend code. This is binding.

---

## 4. Stack (decided — do not substitute)

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic |
| Database | PostgreSQL 16 (`localhost:5433` locally, see `docker-compose.yml`) |
| Cache / locks / broker | Redis 7 |
| Jobs | Celery + Celery Beat (queues: `critical`, `notifications`, `reports`) |
| Storage | S3 / Cloudflare R2 + CDN (design images, PDFs, payment slips) |
| Frontend | Next.js 16 App Router, TypeScript, Tailwind v4, TanStack Query, react-hook-form + Zod |
| Auth | Staff: email + password + 2FA. Dealers: phone + OTP. JWT access 15 min + rotating refresh 30 d. |
| Payments | Razorpay (UPI, cards, netbanking) — webhook-driven only |
| Messaging | WhatsApp Business API (AiSensy/Gupshup/Interakt) primary, SMS via DLT-registered sender fallback |
| PDF | WeasyPrint |
| Observability | Sentry + structured JSON logs + Prometheus/Grafana |
| Mobile (P10) | React Native + Expo, Android first |

---

## 5. Document map — read the one that matches your task

| Document | Read it when |
|---|---|
| `docs/PRODUCTION_PLAN.md` | You need the overall business plan, roadmap, risks |
| **`docs/BUSINESS_RULES.md`** | **Always, before implementing any rule.** Canonical `BR-` rule ids. This is the source of truth for behaviour. |
| `docs/ARCHITECTURE.md` | You are designing or adding a module, worker, or integration |
| `docs/DATA_MODEL.md` | You are writing a migration or a model |
| `docs/API_SPEC.md` | You are adding/consuming an endpoint |
| `docs/EXECUTION.md` | You are starting a phase, or deciding what can run in parallel |
| `docs/phases/P*.md` | You are executing that specific phase — task ids, tracks, acceptance criteria |
| `frontend_new/AGENTS.md` | Any frontend work. Binding. |

**Precedence when documents disagree:** `BUSINESS_RULES.md` > `DATA_MODEL.md` / `API_SPEC.md` > `ARCHITECTURE.md` > `PRODUCTION_PLAN.md` > this file's summaries. If you find a real contradiction, fix the spec first, then the code.

---

## 6. Execution protocol

### To build a phase

```
Execute phase P2
```
or use the slash command:
```
/build-phase P2
```

**What must happen, in this order:**

1. **Read** `docs/EXECUTION.md`, then `docs/phases/P<n>-*.md` for that phase.
2. **Verify the phase's `Depends on` gate is green.** If the previous phase's Definition of Done is not met, stop and say so. Do not build on an unfinished foundation.
3. **Identify the parallel tracks** (`T1`, `T2`, …) listed in that phase doc. Tracks inside a phase are designed to be independent — different files, no shared edits.
4. **Execute the tracks.** Sequentially by default. **Only fan out to parallel subagents if the user explicitly asks for it** (e.g. "run the tracks in parallel", "use subagents") — never spawn agents on your own initiative.
5. **After every task**, run the phase's verification commands. Do not mark a task done on the basis that the code looks right.
6. **Report** against the phase's Definition of Done checklist — item by item, with what passed and what did not. Never report a phase complete with a failing check.

### Parallelism — what is actually true

- **Phases are sequential.** P3 needs P2's orders; P4 needs P3's ledger. Do not start a phase whose dependency is unfinished.
- **Tracks within a phase are parallel-safe** — they are scoped to disjoint file sets, listed in each phase doc.
- **Two tracks must never edit the same file.** If a task requires it, it belongs in one track, not two.
- P1 T-INT (external onboarding: WhatsApp, DLT, Razorpay, S3) runs **calendar-parallel to every phase** — it is human paperwork with multi-week lead times. Start it in week 1 regardless of code progress.

### Definition of Done — applies to every task, in every phase

A task is done only when **all** of these are true:
- [ ] Code implements the cited `BR-` rules exactly
- [ ] Alembic migration written and applied (if schema changed)
- [ ] Unit tests written and passing — **100% branch coverage on any pricing, tax, credit or ledger path**
- [ ] Integration test covering the happy path + at least one rejection path
- [ ] `ruff` + `mypy` clean (backend), `tsc` + `eslint` clean (frontend)
- [ ] API changes reflected in `docs/API_SPEC.md`
- [ ] No secret, no hardcoded rule value, no `Float` money, no `print()`
- [ ] Audit-logged if it changes money, limits, prices, or permissions

---

## 7. Commands

```powershell
docker compose up -d
```
```powershell
cd backend; .\venv\Scripts\Activate.ps1; pip install -r requirements.txt
```
```powershell
cd backend; alembic upgrade head
```
```powershell
cd backend; uvicorn app.main:app --reload --port 8000
```
```powershell
cd backend; celery -A app.workers.celery_app worker -Q critical,notifications,reports -l info
```
```powershell
cd backend; celery -A app.workers.celery_app beat -l info
```
```powershell
cd backend; pytest -q --cov=app --cov-report=term-missing
```
```powershell
cd backend; ruff check app; mypy app
```
```powershell
cd frontend_new; npm run dev
```
```powershell
cd frontend_new; npx tsc --noEmit; npm run lint
```

Local URLs: API `http://localhost:8000` · API docs `http://localhost:8000/docs` · Web `http://localhost:3000` · Postgres `localhost:5433` · Redis `localhost:6379`

---

## 8. Coding conventions

**Backend**
- Layering: `api/v1/` routers are thin — validate input, call a service, return a schema. **No business logic in routers, no DB queries in routers.**
- Business logic lives in `modules/<domain>/service.py`. Data access in `modules/<domain>/repository.py`.
- Every service function that changes money takes an explicit `actor` and writes an audit entry.
- Custom exceptions in `core/exceptions.py`, mapped to HTTP once in a global handler. Never raise `HTTPException` from a service.
- Type-hint everything. `mypy` strict on `modules/`.
- Naming: tables plural snake_case; money columns end `_paise`; timestamps end `_at`; booleans start `is_`/`has_`.
- Every table carries `id BIGSERIAL`, public `uid UUID`, `created_at`, `updated_at`, `created_by`, `updated_by`.
- Times stored UTC (`timestamptz`), displayed IST.

**Frontend**
- **Read `frontend_new/AGENTS.md` first — non-negotiable.**
- Server Components for read-only pages; Client Components only where interactivity requires it.
- All API calls through a generated typed client. No `fetch` scattered in components.
- Zod schema per form, shared shape with backend Pydantic schema.
- Money formatted through one `formatINR(paise)` helper. Never divide by 100 inline.
- Every list has loading, empty and error states. Not optional.
- UI strings go through i18n from day one (en / hi / gu).

**Testing**
- Test names cite the rule: `test_BR_SQFT_02_min_billable_applies_per_piece`.
- A **golden dataset** of real historical Liger orders must reproduce to the paise. This is the regression gate.
- Concurrency test required for the credit gate (`BR-CR-20`).

---

## 9. Decisions — RESOLVED (owner, 2026-08-01)

All DEC items are resolved — see `docs/BUSINESS_RULES.md` §0 for the authoritative list. Highlights:

- **DEC-01:** min 11 sq.ft applies **per piece**
- **DEC-03:** **NO price tiers** — one rate per design, identical for every customer; rate cascade is special → base
- All other assumed defaults approved as-is (rounding 0.25 up, GST per design, credit days 30, ladder −3/0/+3/+10/block +15/hard 45, cash bonus +10%, part-payment yes, languages en/hi/gu)

**Data still owed by the owner:** design catalogue with rates, dealer list, opening balances, 12 months of order history (needed by P8).

---

## 10. Things that will get you in trouble here

- Writing a money value as a float "just for the preview" — it will leak into the bill.
- Marking a phase complete because the code compiles. Run the verification commands.
- Implementing a rule from memory of this file instead of reading `docs/BUSINESS_RULES.md`.
- Enabling auto-blocking without shadow mode (`BR-CR-40`). It will stop real business.
- Editing `docs/BUSINESS_RULES.md` to match the code. The spec leads; the code follows.
- Spawning subagents the user did not ask for.
