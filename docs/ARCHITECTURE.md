# LIGER — Technical Architecture

> Read with `CLAUDE.md` (rules) and `BUSINESS_RULES.md` (behaviour).
> This document defines **structure**: how the code is laid out, how requests flow, how work is queued, how it scales.

---

## 1. Shape of the system

**Modular monolith, API-first.** One deployable backend with hard internal module boundaries, one web client, one future mobile client.

```
┌───────────────┐   ┌───────────────┐        ┌──────────────────┐
│  Dealer Web   │   │  Admin Web    │        │ Mobile App (P10) │
│  Next.js 16   │   │  Next.js 16   │        │  React Native    │
└───────┬───────┘   └───────┬───────┘        └────────┬─────────┘
        └───────────────────┴─────────────────────────┘
                            │ HTTPS, JWT
                    ┌───────▼────────┐
                    │  FastAPI /api/v1│  ← thin routers, no business logic
                    └───────┬────────┘
                            │
                    ┌───────▼────────┐
                    │ Service layer   │  ← ALL business rules live here
                    │ modules/*/       │
                    └───┬────────┬────┘
                        │        │
          ┌─────────────▼──┐  ┌──▼──────────────┐
          │ PostgreSQL 16  │  │ Redis 7          │
          │ source of truth│  │ cache/locks/queue│
          └────────────────┘  └──┬───────────────┘
                                 │
                        ┌────────▼─────────┐      ┌──────────────────┐
                        │ Celery workers   │─────▶│ Razorpay         │
                        │ critical         │─────▶│ WhatsApp / SMS   │
                        │ notifications    │─────▶│ S3 / R2 + CDN    │
                        │ reports          │      └──────────────────┘
                        └──────────────────┘
```

**Why not microservices:** at Liger's scale a modular monolith is faster to build, cheaper to run, and trivially splittable later. Module boundaries below are strict enough that extraction stays a refactor, not a rewrite. Splitting early is the classic mistake here.

---

## 2. Backend layout

```
backend/app/
├── core/
│   ├── config.py          Pydantic settings, env-driven
│   ├── money.py           Money type — integer paise, arithmetic, formatting (R1)
│   ├── security.py        hashing, JWT, OTP, 2FA
│   ├── permissions.py     RBAC decorator/dependency (BR-AC-08)
│   ├── exceptions.py      domain exceptions + single global HTTP mapper
│   ├── idempotency.py     Idempotency-Key store & replay (R6)
│   ├── audit.py           audit_log writer
│   ├── settings_registry.py  typed access to the settings table (R8)
│   ├── numbering.py       gapless order/invoice series (BR-ORD-06, BR-TAX-05)
│   └── logging.py         structured JSON logs, request ids
├── db/
│   ├── session.py         engine, session factory, unit-of-work
│   ├── base.py            declarative base + TimestampMixin + AuditMixin
│   └── alembic/           migrations — the ONLY schema path (R5)
├── modules/
│   ├── identity/          users, customer_users, roles, OTP, sessions, 2FA
│   ├── customers/         master, addresses, contacts, regions, distributor tree, tiers
│   ├── catalog/           designs, categories, images, accessories, import
│   ├── pricing/           rate cards, tiers, special rates, sqft engine, tax engine
│   ├── orders/            cart, quotation, order, line items, state machine, documents
│   ├── credit/            exposure, ageing, gate, ladder, overrides, score, simulation
│   ├── payments/          gateway adapter, webhooks, cash confirmation, allocation
│   ├── invoicing/         invoices, credit notes, GST, numbering, PDF
│   ├── fulfilment/        production, dispatch, delivery, POD
│   ├── notifications/     templates, provider adapters, queue, delivery status, prefs
│   ├── analytics/         aggregates, materialized views, reports, exports, insights
│   └── admin/             settings, audit view, imports, feature flags
├── workers/
│   ├── celery_app.py      app + queue routing
│   ├── beat.py            schedule
│   └── tasks/             notifications.py credit.py reports.py webhooks.py maintenance.py
├── api/v1/
│   ├── deps.py            auth, current user, db session, pagination, idempotency
│   └── routers/           auth designs customers orders cart payments credit reports admin
└── main.py
```

### Module internal shape (every module is identical)

```
modules/<domain>/
├── models.py       SQLAlchemy ORM models
├── schemas.py      Pydantic request/response
├── repository.py   ALL database access for this module
├── service.py      ALL business rules — cites BR- ids in comments (R12)
├── events.py       domain events this module emits
└── tests/          unit + integration
```

**Boundary rules**
- A module may call another module's **service**, never another module's repository or models.
- Cross-module reads that would create a cycle go through a domain **event** instead.
- `orders` → `credit` and `orders` → `pricing` are the only synchronous cross-calls in the hot path.
- Everything else (notifications, analytics, score) reacts to events **asynchronously**.

### Domain events (in-process, dispatched to Celery)

| Event | Emitted by | Consumers |
|---|---|---|
| `order.placed` | orders | notifications, analytics, credit(exposure) |
| `order.status_changed` | orders | notifications, fulfilment, analytics |
| `payment.confirmed` | payments | credit(recompute + auto-unblock BR-CR-47), notifications, analytics |
| `cash.pending_confirmation` | payments | notifications(admin) |
| `credit.state_changed` | credit | notifications, analytics |
| `customer.blocked` / `.unblocked` | credit | notifications, follow-ups |
| `invoice.created` | invoicing | notifications, credit(ageing) |

---

## 3. Request flow — order placement (the critical path)

```
POST /api/v1/orders  (Idempotency-Key: <uuid>)
  1. api/v1/routers/orders.py      validate payload, resolve actor, check permission
  2. core/idempotency.py            key seen? → return stored response, stop            (R6)
  3. BEGIN TRANSACTION
  4. pricing.service.price_cart()   per line: resolve rate BR-PR-01, sqft BR-SQFT-*,
                                    making charge, discount, tax BR-TAX-*  → frozen snapshot
  5. credit.service.evaluate()      SELECT customer FOR UPDATE                          (R7)
                                    exposure BR-CR-02, gate BR-CR-10…16
                                    → decision object BR-CR-21
  6. decision = BLOCK               → ROLLBACK, 403 with overdue invoices + pay link
     decision = NEEDS_APPROVAL      → persist order as PENDING_APPROVAL, notify admin
     decision = ALLOW/WARN          → persist order + lines + status history
  7. numbering.next_order_no()      gapless, inside the same transaction
  8. store decision snapshot on the order
  9. COMMIT
 10. emit order.placed              → Celery: notifications, PDF, analytics             (R10)
 11. store idempotent response
```

**Everything that can fail slowly (WhatsApp, PDF, analytics) happens after COMMIT, in a worker.** The dealer's request never waits on a third party.

---

## 4. Background jobs

| Queue | Priority | Contents |
|---|---|---|
| `critical` | highest | payment webhooks, credit recompute after payment, auto-unblock |
| `notifications` | high | WhatsApp/SMS sends, retries, delivery-status callbacks |
| `reports` | low | exports, PDF generation, materialized-view refresh, digests |

### Scheduled (Celery Beat, IST)

| Time | Job | Rules |
|---|---|---|
| 00:30 | Re-age all invoices, write `credit_snapshots` | BR-CR-04, BR-CR-54 |
| 01:00 | Advance escalation ladder, fire due reminders, apply auto-blocks | BR-CR-41…49 |
| 02:00 | Recompute customer scores + suggested limits | BR-SCR-01 |
| 02:30 | Expire lapsed overrides and special rates | BR-CR-50, BR-PR-04 |
| 03:00 | Ledger reconciliation assertion + alert on drift | BR-LED-04 |
| 03:30 | Rebuild analytics reporting tables | BR-AN-08 |
| 08:00 | Owner daily digest | BR-AN-07 |
| every 15 min | Refresh dashboard materialized views | BR-AN-08 |
| Mon 08:00 | Weekly business review digest | BR-AN-07 |
| 1st 08:00 | Monthly statements to all dealers | BR-AN-07 |
| hourly | Retry failed notifications; reconcile gateway settlements | BR-NOT-02 |

**All scheduled jobs are idempotent and safe to re-run** — they key off state, not off "has this job run today".

---

## 5. Data & consistency

- **Postgres is the only source of truth.** Redis holds cache and locks; losing Redis loses no business data.
- Money: `BIGINT` paise everywhere (**R1**). The `Money` type wraps arithmetic so a raw int can't leak in.
- Ledger append-only (**R2**); balances derived (**R3**) and cached in Redis with **event-driven invalidation** on any order/payment/limit change — never TTL-only, because a stale credit limit is a financial error.
- Concurrency: pessimistic row lock for the credit gate (**R7**); optimistic `version` column on orders and customers for general edits.
- All timestamps `timestamptz`, stored UTC, rendered IST.
- Soft delete (`deleted_at`) on masters; **never** on ledger, invoices, orders, or audit rows.

---

## 6. Security architecture

| Concern | Implementation |
|---|---|
| Staff auth | email + password (argon2) + TOTP 2FA |
| Dealer auth | phone + OTP, 5/hour/number, lockout, no password to leak |
| Tokens | JWT access 15 min; rotating refresh 30 d; httpOnly SameSite cookies on web; secure storage on mobile |
| Authorization | permission dependency on every route (**R11**); dealer queries always scoped by `customer_id` from the token, never from the request body |
| Input | Pydantic v2 strict; ORM only, no string SQL |
| Rate limiting | per IP + per user; stricter on auth, OTP and payment endpoints |
| Webhooks | provider signature verification + raw event stored + idempotent processing (BR-PAY-04) |
| Uploads | type + size validated, virus scanned, served from a separate domain, never executable |
| Secrets | secret manager in prod; `.env` local only and git-ignored (**R14**) |
| Transport | HTTPS only, HSTS, CSP, standard security headers |
| Audit | every money/limit/price/permission change → `audit_log` with before/after, actor, IP |
| PII | dealer phone/GSTIN encrypted at rest; access logged; DPDP-compliant deletion process |

---

## 7. Scaling path (concrete)

| Stage | Load | Configuration |
|---|---|---|
| **Now** | ~100 dealers, ~200 orders/day | 1 API container + 2 workers + Postgres + Redis on one VPS |
| **Growth** | ~1,000 dealers | 3 API containers behind a load balancer; Postgres read replica; analytics served from replica + materialized views; images on CDN; worker pools split per queue |
| **Scale** | 10,000+ dealers, multi-plant | Partition `orders` / `ledger_entries` / `notifications` by month; nightly reporting schema; separate analytics database; per-queue autoscaling; read-only API keys for large distributors |

Design decisions that make this possible **from day one** (do not defer):
- Cursor pagination on every list endpoint — never `OFFSET` (**R13**)
- Every foreign key indexed; composite indexes on `(customer_id, created_at)`, `(status, due_date)`, `(design_no)`
- No `SELECT *`; explicit column lists
- Hard-capped page sizes (`max_page_size = 100`)
- All heavy aggregation pre-computed, never live (BR-AN-08)
- Stateless API containers — no in-process session or cache that breaks on a second replica

---

## 8. Frontend architecture

```
frontend_new/app/
├── (dealer)/          home  catalogue  order/new  cart  orders  invoices  ledger  pay  profile
├── (admin)/           dashboard  orders  customers  catalogue  rate-cards  payments
│                      credit  follow-ups  notifications  reports  users  settings  audit
├── (auth)/            login  otp
├── components/        ui/ (design system)  order/  credit/  charts/
├── lib/
│   ├── api/           generated typed client from the OpenAPI schema
│   ├── money.ts       formatINR(paise) — the ONLY place paise become rupees
│   ├── sqft.ts        display-only preview; the server total is authoritative
│   └── i18n/          en / hi / gu
└── hooks/
```

- **Read `frontend_new/AGENTS.md` before any frontend work** — this Next.js version differs from training data.
- Server Components for dashboards and lists; Client Components only where interaction demands it.
- TanStack Query for cache + optimistic updates; every mutation invalidates the credit snapshot.
- The order form previews sq.ft locally for responsiveness but **always shows the server-calculated total before submit** — client arithmetic is never authoritative (**R4**).
- Design system first: one set of primitives (Button, Input, Table, Money, StatusPill, EmptyState, Skeleton) built in P0, used everywhere after.

---

## 9. Environments and deployment

| Env | Purpose |
|---|---|
| local | Docker Compose (Postgres 5433, Redis 6379), seeded demo data |
| staging | production-like, sanitised data, all integrations in sandbox mode |
| production | managed Postgres with PITR, app containers behind LB, workers, Redis, S3+CDN |

**CI/CD (GitHub Actions):** `ruff` → `mypy` → `pytest` (coverage gate) → `tsc` → `eslint` → build images → deploy staging → smoke tests → manual approval → production, with `alembic upgrade head` as a release step and a documented rollback.

**Reliability:** daily automated backups with a **monthly tested restore** (an untested backup is not a backup); PITR; health checks; uptime alerting to the owner's phone; graceful degradation — if WhatsApp is down, orders still work and messages queue.

---

## 10. Observability

- Structured JSON logs with a request id propagated into Celery tasks.
- Sentry for exceptions, with release tagging.
- Metrics: API p95 latency, error rate, queue depth per queue, notification failure rate, webhook lag, nightly job success, ledger-drift assertion.
- **Business alerts** (not just technical): auto-block fired, ledger reconciliation drift, gateway webhook not received in 30 min, notification failure rate > 5%, nightly credit job failed.
