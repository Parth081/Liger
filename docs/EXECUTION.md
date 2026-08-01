# LIGER — Execution Guide

> How the build actually runs: phase order, what is parallel, what is not, and the gate that must be green before a phase is called done.

---

## 1. The honest picture of parallelism

**Phases are sequential.** P3 (credit) needs P2's orders. P4 (payments) needs P3's ledger and ageing. P7 (analytics) needs real order and payment data to aggregate. Running them "in parallel" produces code built on interfaces that do not exist yet, and it always costs more to unpick than it saved.

**Tracks inside a phase are parallel.** Every phase below is split into tracks `T1…Tn`, each scoped to a **disjoint set of files**, with its own task ids. Tracks in the same phase can be executed simultaneously with no merge conflicts. That is where real speed comes from.

**One track runs parallel to everything, all the way through:**

> **T-EXT — External onboarding.** WhatsApp Business API verification, SMS DLT template registration, Razorpay KYC, S3/CDN, domain + SSL, Play Store account.
> These are **human paperwork with multi-week lead times**. Start on day 1 of P0. If they are not started early, P5 will sit idle waiting on approvals that no amount of code can accelerate. This is the single most common way a project like this slips.

---

## 2. Phase dependency graph

```
P0 Foundations ──┬─► P1 Catalogue & Pricing ──► P2 Order Taking ──► P3 Credit Engine ──┐
                 │                                      │                              │
                 │                                      └──────────────┐               ▼
                 │                                                     ├──────► P4 Payments & Invoicing
                 │                                                     │               │
                 │                                                     ▼               ▼
                 └────────────────────────────────────────────► P5 Notifications ◄─────┘
                                                                        │
                                                                        ▼
                                              P6 Fulfilment & Follow-up ──► P7 Analytics & Insights
                                                                                       │
                                                                                       ▼
                                                                 P8 Hardening & Migration
                                                                                       │
                                                                                       ▼
                                                                          P9 Go-Live (web)
                                                                                       │
                                                                                       ▼
                                                                          P10 Mobile App
```

| Phase | Weeks | Depends on | Can start early? |
|---|---|---|---|
| P0 Foundations | 1–2 | — | — |
| P1 Catalogue & Pricing | 3–4 | P0 | design-system work (T4) can begin in P0 week 2 |
| P2 Order Taking | 5–7 | P1 | no |
| P3 Credit Engine | 8–10 | P2 | ageing/ledger schema (T1) can begin in P2 week 7 |
| P4 Payments & Invoicing | 11–13 | P3 | gateway sandbox integration (T2) can begin in P3 |
| P5 Notifications | 14–15 | P3 (triggers), P4 (pay links) | template authoring (T3) can begin in P1 |
| P6 Fulfilment & Follow-up | 16–17 | P2, P5 | no |
| P7 Analytics & Insights | 18–20 | P2, P4 | schema/MV design (T1) can begin in P4 |
| P8 Hardening & Migration | 21–23 | all | importer scripts (T2) can begin in P1 |
| P9 Go-Live | 24 | P8 | — |
| P10 Mobile App | 25–32 | P9 | design (T1) can begin in P7 |

---

## 3. Running a phase

```
/build-phase P2
```
or plainly: `Execute phase P2`.

**The required sequence:**

1. Read `CLAUDE.md` (rules), then `docs/phases/P<n>-*.md`.
2. **Check the entry gate.** Every phase doc opens with `Entry gate` — the previous phase's Definition of Done. If any item is unmet, **stop and report it**. Do not build on an unfinished foundation.
3. Read the `BR-` rules cited by the phase, in `docs/BUSINESS_RULES.md`. Implement from the spec, not from memory.
4. Execute tracks. Sequential by default. **Parallel subagents only if the user explicitly asks.**
5. Run the phase's verification commands after each task. Green means green — not "it should work".
6. Report against the Definition of Done, line by line, stating what passed and what did not.

**Task id format:** `P<phase>-T<track>-<nn>` — e.g. `P2-T1-03`. Cite it in the commit message.

---

## 4. Track conflict map

Two tracks must never edit the same file. This table is the contract that makes parallel execution safe.

| Track type | Owns | Never touches |
|---|---|---|
| **T1 — Data & migrations** | `db/alembic/`, `modules/*/models.py` | services, routers, frontend |
| **T2 — Domain services** | `modules/*/service.py`, `repository.py`, `core/` | migrations, routers, frontend |
| **T3 — API layer** | `api/v1/routers/`, `modules/*/schemas.py` | services, migrations, frontend |
| **T4 — Frontend** | `frontend_new/` | all backend |
| **T5 — Workers & jobs** | `workers/` | routers, frontend |
| **T6 — Tests & fixtures** | `tests/`, factories, seed data | production code |
| **T-EXT — External onboarding** | credentials, provider dashboards, docs | code |

If a task genuinely needs files from two tracks, it belongs to **one** track — the one owning the riskier file. Split the task, never the file.

---

## 5. Universal Definition of Done

No task, and no phase, is complete unless every line is true:

- [ ] Implements the cited `BR-` rules exactly as written
- [ ] Alembic migration written, applied, and reversible (if schema changed)
- [ ] Unit tests pass — **100% branch coverage on any pricing, tax, credit or ledger path**
- [ ] Integration test covers the happy path **and** at least one rejection path
- [ ] `ruff check` + `mypy` clean; `tsc --noEmit` + `eslint` clean
- [ ] `docs/API_SPEC.md` updated in the same commit as any endpoint change
- [ ] No `Float` money, no hardcoded rule value, no secret, no `print()`
- [ ] Audit-logged if it changes money, limits, prices, or permissions
- [ ] Manually exercised once against the running app — not just unit-tested

---

## 6. Verification commands

```powershell
cd backend; pytest -q --cov=app --cov-report=term-missing; ruff check app; mypy app
```
```powershell
cd frontend_new; npx tsc --noEmit; npm run lint
```
```powershell
cd backend; alembic upgrade head; alembic downgrade -1; alembic upgrade head
```

The migration up-down-up check is not optional — an irreversible migration is a production outage waiting to happen.

---

## 7. Standing risks to watch during execution

| Risk | Guard |
|---|---|
| External approvals (WhatsApp/DLT/Razorpay) delay P5 | T-EXT starts day 1 of P0 |
| Auto-blocking goes live and stops real business | Shadow mode is mandatory (BR-CR-40); enforcement only after owner review |
| Migrated opening balances are wrong | Per-customer reconciliation sign-off in P8 before enforcement |
| Preview total ≠ invoice total | Single pricing engine (BR-SQFT-11) + golden dataset regression test |
| Two orders both slip under one limit | Row-lock concurrency test is a P3 exit gate (BR-CR-20) |
| Dealers reject the system | Phased rollout, generous initial limits, Hindi/Gujarati UI, staff can order on their behalf |
| Scope creep | These docs are the scope. New ideas go to a `docs/BACKLOG.md`, not into the current phase. |
