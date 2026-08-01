# P0 — Foundations (Weeks 1–2)

**Goal:** replace the prototype's unsafe foundations with production ones. Nothing business-facing ships in this phase; everything after it depends on getting this right.

**Entry gate:** none — this is the first phase.
**Rules implemented:** R1–R14 (`CLAUDE.md` §2), BR-AC-01…09.

⚠️ **Start T-EXT on day 1.** WhatsApp/DLT/Razorpay approvals take weeks and will block P5 if deferred.

---

## T1 — Data foundations *(owns `db/alembic/`, `modules/*/models.py`)*

| Id | Task |
|---|---|
| P0-T1-01 | Initialise Alembic properly; delete every `Base.metadata.create_all()` from non-test code (**R5**) |
| P0-T1-02 | Base mixins: `id`, `uid UUID`, `created_at`, `updated_at`, `created_by`, `updated_by`, `deleted_at`, `version` |
| P0-T1-03 | **Migrate all money columns to `BIGINT` paise**, renamed `*_paise` (**R1**). This is the phase's most important task. |
| P0-T1-04 | `settings` + `settings_history` tables; seed every key listed in `DATA_MODEL.md` §10 (**R8**) |
| P0-T1-05 | `roles`, `permissions`, `role_permissions`, `users`, `customer_users`, `otp_requests`, `refresh_tokens`; seed roles + permission matrix (BR-AC-01…07) |
| P0-T1-06 | `audit_log` (append-only) with `(entity_type, entity_id, created_at DESC)` index |
| P0-T1-07 | `idempotency_keys` table (**R6**) |
| P0-T1-08 | Postgres `ENUM` types for order status, payment status/method, customer status, credit event type |
| P0-T1-09 | Verify every migration is reversible: `upgrade head` → `downgrade -1` → `upgrade head` |

## T2 — Core services *(owns `core/`)*

| Id | Task |
|---|---|
| P0-T2-01 | `core/money.py` — `Money` value type in paise: arithmetic, `ROUND_HALF_UP`, percentage, `format_inr()`. **Raw ints must not leak into business code.** |
| P0-T2-02 | `core/config.py` — Pydantic settings, env-driven, fails loudly on a missing required secret |
| P0-T2-03 | `core/security.py` — argon2 hashing, JWT access 15 min + rotating refresh 30 d, TOTP 2FA, OTP generate/verify with 5/hr rate limit + lockout (BR-AC-09) |
| P0-T2-04 | `core/permissions.py` — FastAPI dependency `require(*permissions)`; **dealer scope derived from the token only** (BR-AC-07/08) |
| P0-T2-05 | `core/exceptions.py` — domain exception hierarchy + one global handler producing the `API_SPEC` error envelope. Services must never raise `HTTPException`. |
| P0-T2-06 | `core/idempotency.py` — key store, request-hash match, replay of the stored response (**R6**) |
| P0-T2-07 | `core/audit.py` — one writer used by every privileged mutation |
| P0-T2-08 | `core/settings_registry.py` — typed cached settings access with invalidation on write (**R8**) |
| P0-T2-09 | `core/numbering.py` — gapless in-transaction series for orders and invoices (BR-ORD-06, BR-TAX-05) |
| P0-T2-10 | `core/logging.py` — structured JSON logs, request id propagated into Celery |
| P0-T2-11 | `db/session.py` — session factory + unit-of-work; helper for `SELECT … FOR UPDATE` (**R7**) |

## T3 — API skeleton *(owns `api/v1/`)*

| Id | Task |
|---|---|
| P0-T3-01 | `/api/v1` router mounting, versioned; OpenAPI generation configured |
| P0-T3-02 | `api/v1/deps.py` — current actor, db session, pagination (cursor, `max_page_size=100`), idempotency dependency |
| P0-T3-03 | Auth endpoints: staff login, 2FA, OTP request/verify, refresh, logout, `/auth/me` |
| P0-T3-04 | Rate limiting (per IP + per user), stricter on auth/OTP |
| P0-T3-05 | Security headers, CORS locked to known origins, HTTPS-only assumptions |
| P0-T3-06 | `/health` and `/health/deep` (DB, Redis, storage) |

## T4 — Frontend foundations *(owns `frontend_new/`)*

| Id | Task |
|---|---|
| P0-T4-01 | **Read `frontend_new/AGENTS.md` and `node_modules/next/dist/docs/` before writing any code.** Binding. |
| P0-T4-02 | App shell: `(auth)` / `(dealer)` / `(admin)` route groups, layouts, navigation |
| P0-T4-03 | Design system primitives: Button, Input, Select, Table, Card, Modal, Toast, **Money**, StatusPill, EmptyState, Skeleton, ErrorState |
| P0-T4-04 | `lib/money.ts` — `formatINR(paise)`. **The only place paise become rupees.** |
| P0-T4-05 | Typed API client generated from the OpenAPI schema + TanStack Query setup |
| P0-T4-06 | Auth flows: staff login + 2FA, dealer phone+OTP, token refresh, protected routes |
| P0-T4-07 | i18n scaffolding (en/hi/gu) — every string goes through it from day one (DEC-10) |
| P0-T4-08 | Responsive shell verified at 360 px — dealers will use the website on a phone |

## T5 — Workers & infrastructure *(owns `workers/`, `docker-compose.yml`, CI)*

| Id | Task |
|---|---|
| P0-T5-01 | Celery app + Beat; queues `critical`, `notifications`, `reports` with routing |
| P0-T5-02 | Upgrade Postgres 15 → 16 in compose; add MinIO (local S3) and a mail catcher |
| P0-T5-03 | Sentry + structured logging wired in API and workers |
| P0-T5-04 | GitHub Actions: `ruff` → `mypy` → `pytest` (coverage gate) → `tsc` → `eslint` → build |
| P0-T5-05 | Staging environment provisioned; auto-deploy on merge to `main` |
| P0-T5-06 | Backup job + **a documented, tested restore procedure** |

## T6 — Test foundations *(owns `tests/`)*

| Id | Task |
|---|---|
| P0-T6-01 | Pytest setup: transactional DB fixture, factory-boy factories, frozen clock |
| P0-T6-02 | Coverage gate: **100% on `core/money.py`**, 90% overall, build fails below |
| P0-T6-03 | Auth + permission matrix tests: every role × every protected endpoint |
| P0-T6-04 | Playwright installed with one smoke test (login → shell renders) |

## T-EXT — External onboarding *(starts day 1, runs to P5)*

| Id | Task |
|---|---|
| P0-TE-01 | WhatsApp Business API: pick vendor, dedicated number, Meta business verification |
| P0-TE-02 | SMS: DLT principal-entity + header registration |
| P0-TE-03 | Razorpay account + KYC + sandbox keys |
| P0-TE-04 | S3/R2 bucket + CDN + credentials |
| P0-TE-05 | Domain, SSL, production hosting |
| P0-TE-06 | ⚠️ **Verify `backend/.env` is git-ignored; rotate any credential already committed** (**R14**) |

---

## Verification

```powershell
cd backend; alembic upgrade head; alembic downgrade -1; alembic upgrade head
```
```powershell
cd backend; pytest -q --cov=app --cov-report=term-missing; ruff check app; mypy app
```
```powershell
cd frontend_new; npx tsc --noEmit; npm run lint
```

## Definition of Done — P0 exit gate

- [ ] **No `Float` money column remains anywhere in the codebase** (grep-verified)
- [ ] `Money` type at 100% branch coverage
- [ ] All migrations reversible; `create_all()` removed from non-test code
- [ ] Settings table seeded; no rule value hardcoded in Python
- [ ] RBAC enforced server-side; permission matrix test passes for every role × endpoint
- [ ] Staff 2FA login and dealer OTP login both work end to end
- [ ] Idempotency middleware proven: same key twice → one effect, identical response
- [ ] Audit log writes on every privileged mutation
- [ ] Celery workers + Beat running; a test job completes
- [ ] CI green on `main`; staging auto-deploys
- [ ] Backup taken **and restored successfully at least once**
- [ ] `.env` git-ignored; exposed credentials rotated
- [ ] T-EXT items 01–05 all **submitted** (approval may still be pending)
