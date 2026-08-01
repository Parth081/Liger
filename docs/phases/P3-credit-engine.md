# P3 — Credit Engine (Weeks 8–10)

**Goal:** the rule that solves the owner's actual problem — a dealer with old unpaid dues cannot place a new order. Blocking driven by **overdue age**, not just amount.

**Entry gate:** P2 Definition of Done fully green.
**Rules implemented:** BR-CR-01…54, BR-LED-01…05, BR-SCR-01…07.
**Blocked by decisions:** DEC-06 (credit days), DEC-07 (ladder days), DEC-08 (cash bonus) — **owner sign-off before T2.**

> ⚠️ **BR-CR-40 is mandatory.** This phase ships with `credit_enforcement_mode = shadow`. The engine logs every decision it *would* have made and blocks nobody. Enforcement is switched on only in P9, after the owner has reviewed two weeks of shadow output. Shipping enforcement on day one will stop real business.

---

## T1 — Data & migrations

| Id | Task |
|---|---|
| P3-T1-01 | `ledger_entries` — **append-only**; DB-level guard (trigger or revoked UPDATE/DELETE grant) so the rule cannot be broken by accident (**R2**) |
| P3-T1-02 | `credit_snapshots` (daily, unique per customer per date) |
| P3-T1-03 | `credit_events` (append-only) with `is_shadow` flag for BR-CR-40 |
| P3-T1-04 | `credit_overrides` with `valid_until` |
| P3-T1-05 | `customer_scores` with `factors JSONB` |
| P3-T1-06 | `escalation_state` — unique per invoice, makes the ladder idempotent (BR-CR-49) |
| P3-T1-07 | Customer credit columns: `credit_limit_paise`, `credit_days`, `cash_bonus_pct`, `opening_balance_paise`, `status`, `blocked_at`, `block_reason`, `is_manual_block` |
| P3-T1-08 | Index `(status, due_date)` on invoices — the index the nightly ladder job depends on |
| P3-T1-09 | Seed settings: `credit_days_default=30`, `hard_block_days=45`, `ladder_pre_due=-3`, `ladder_warn1=3`, `ladder_warn2=10`, `ladder_block=15`, `warn_utilisation_pct=80`, `cash_bonus_pct_default=10`, `cash_ratio_threshold=0.30`, `credit_enforcement_mode=shadow` |

## T2 — Credit services *(replaces the P2 stub)*

| Id | Task |
|---|---|
| P3-T2-01 | `credit/ledger.py` — append-only posting, `balance_after`, entry types, reversal-only corrections (BR-LED-01/02) |
| P3-T2-02 | `credit/exposure.py` — `outstanding` from the ledger (BR-CR-01) + **confirmed-but-uninvoiced orders** (BR-CR-02). Without this a dealer places ten orders in a day and beats the check. |
| P3-T2-03 | `credit/ageing.py` — per-invoice due dates and buckets `current/1-30/31-60/61-90/90+` (BR-CR-03/04) |
| P3-T2-04 | `credit/limits.py` — `effective_limit = base + cash_bonus + active_override`; cash bonus gated on the 6-month confirmed-cash ratio (BR-CR-05/06) |
| P3-T2-05 | **`credit/gate.py::evaluate()`** — BR-CR-10…16 in exact order, inside a transaction with `SELECT … FOR UPDATE` on the customer row (**R7**, BR-CR-20). Returns the structured decision object (BR-CR-21). |
| P3-T2-06 | Shadow mode: when `credit_enforcement_mode = shadow`, the decision is computed and written to `credit_events` with `is_shadow=true`, and **ALLOW is returned** (BR-CR-40) |
| P3-T2-07 | Colour-state resolver — one implementation used by API, admin and app (BR-CR-30…33) |
| P3-T2-08 | Override service: grant with mandatory reason + expiry, auto-revert, full audit (BR-CR-50/51) |
| P3-T2-09 | Manual block/unblock; manual block outranks automatic logic (BR-CR-52) |
| P3-T2-10 | **Simulation service** — "what if this limit were ₹X" and "what would this rule change do across all customers" (BR-CR-53) |
| P3-T2-11 | `credit/scoring.py` — 7 weighted factors, bands, suggested limit, plain-language factor reasons, `NEW` band under 3 months (BR-SCR-01…07) |
| P3-T2-12 | Wire `evaluate()` into `orders/service.py`, replacing the P2 stub. No other order code changes. |

## T5 — Workers

| Id | Task |
|---|---|
| P3-T5-01 | 00:30 — re-age all invoices, write daily `credit_snapshots` (BR-CR-54) |
| P3-T5-02 | 01:00 — advance the escalation ladder per invoice, apply auto-blocks at `+ladder_block`; **idempotent**, each step fires at most once per invoice (BR-CR-41…49). Notification sends are stubbed until P5 and recorded as intents. |
| P3-T5-03 | 02:00 — recompute scores and suggested limits |
| P3-T5-04 | 02:30 — expire lapsed overrides and special rates |
| P3-T5-05 | 03:00 — **ledger reconciliation assertion**: Σ ledger == derived outstanding for every customer; alert on any drift (BR-LED-04) |
| P3-T5-06 | Real-time: on `payment.confirmed` → recompute → **auto-unblock within seconds** (BR-CR-47) |

## T3 — API layer

| Id | Task |
|---|---|
| P3-T3-01 | `GET /credit/customers/{uid}/status` — limit, exposure, available, colour, ageing |
| P3-T3-02 | `POST /credit/evaluate` — dry-run for a hypothetical cart |
| P3-T3-03 | `GET /credit/ageing`, `GET /credit/blocked` |
| P3-T3-04 | `PATCH /customers/{uid}/limit`, `/block`, `/unblock`, `POST /customers/{uid}/overrides` |
| P3-T3-05 | `POST /credit/simulate` |
| P3-T3-06 | `GET /customers/{uid}/credit-events`, `GET /customers/{uid}/score` |
| P3-T3-07 | `GET /customers/{uid}/ledger` |

## T4 — Frontend

| Id | Task |
|---|---|
| P3-T4-01 | **Credit Centre**: ageing table with buckets, filter by region/rep/bucket, drill to invoices |
| P3-T4-02 | Blocked-customers list with revenue frozen behind each |
| P3-T4-03 | Customer credit panel: limit, exposure, available, colour state, ageing chart, score with factor reasons |
| P3-T4-04 | Limit edit with mandatory reason + suggested-limit hint from the score |
| P3-T4-05 | Block / unblock / override dialogs with reason and expiry |
| P3-T4-06 | **Simulation UI** — preview a limit change or a rule change before committing (BR-CR-53) |
| P3-T4-07 | **Shadow-mode review screen** — "orders that would have been blocked", the owner's decision input for P9 |
| P3-T4-08 | Credit-events timeline per customer |
| P3-T4-09 | Dealer-side: my credit status, my ledger, my overdue invoices |
| P3-T4-10 | Replace the P2 credit strip stub with the live evaluation |

## T6 — Tests *(the highest-stakes tests in the project)*

| Id | Task |
|---|---|
| P3-T6-01 | Each gate rule BR-CR-10…16 in isolation, in the specified order, named `test_BR_CR_*` |
| P3-T6-02 | **Concurrency (BR-CR-20):** two simultaneous orders, each within the limit, jointly over it → **exactly one accepted.** Non-negotiable exit gate. |
| P3-T6-03 | Exposure includes confirmed-uninvoiced orders — ten same-day orders cannot beat the limit |
| P3-T6-04 | Ladder: day-by-day simulation over 60 days with a frozen clock; each step fires exactly once; re-running the job sends nothing extra (BR-CR-49) |
| P3-T6-05 | Auto-unblock on payment confirmation, within the same request cycle |
| P3-T6-06 | Cash bonus applies only above the ratio threshold, and lapses when it drops |
| P3-T6-07 | Override grants extra limit and auto-reverts on expiry |
| P3-T6-08 | Manual block cannot be cleared by automatic logic |
| P3-T6-09 | Ledger append-only enforced at the DB level — a direct `UPDATE` fails |
| P3-T6-10 | Reconciliation: Σ ledger == derived outstanding across a generated 12-month dataset |
| P3-T6-11 | **Shadow mode blocks nobody** while recording every decision |
| P3-T6-12 | Scoring: known input profiles produce expected bands and suggested limits |

---

## Verification

```powershell
cd backend; pytest -q tests/credit --cov=app/modules/credit --cov-report=term-missing
```
100% branch coverage on `modules/credit/` required.

## Definition of Done — P3 exit gate

- [ ] DEC-06, DEC-07, DEC-08 signed off and held in Settings
- [ ] 100% branch coverage on `modules/credit/`
- [ ] **Concurrency test passes** — two simultaneous orders cannot both clear one limit
- [ ] Exposure counts confirmed-uninvoiced orders
- [ ] 60-day ladder simulation fires each step exactly once and is re-run safe
- [ ] Auto-unblock on payment works within seconds
- [ ] `credit_enforcement_mode = shadow` in every environment; shadow review screen live
- [ ] Ledger append-only enforced by the database, not only by convention
- [ ] Nightly reconciliation passes on a generated 12-month dataset
- [ ] Every block, unblock, limit change and override is audit-logged with actor and reason
- [ ] Colour states identical across dealer view, admin view and API
