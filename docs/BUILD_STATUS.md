# LIGER — Build Status

Last verified: 2026-08-01 · 320 backend tests passing · ruff, mypy, tsc, eslint clean · frontend builds

---

## What is built and proven

The whole business loop runs end to end over real HTTP. An automated smoke test
drives the owner's exact scenario and passes 23/23 checks:

| # | Step | Result |
|---|---|---|
| 1 | Owner signs in | ✅ |
| 2 | Design catalogue imported from CSV (dry-run, then commit) | ✅ |
| 3 | Dealer imported with opening balance | ✅ |
| 4 | **3ft × 3ft × 4 pieces → 9 sq.ft actual, 11 billable each, 44 total** | ✅ |
| 5 | Order placed on credit; a double-tap creates **one** order | ✅ |
| 6 | Invoice raised with a due date from the dealer's credit days | ✅ |
| 7 | Nightly ladder fires all 5 steps and blocks the dealer | ✅ |
| 8 | **New order refused, naming the unpaid invoice and days overdue** | ✅ |
| 9 | Cash recorded → frees nothing → admin confirms → auto-unblock | ✅ |
| 10 | The order now goes through | ✅ |
| 11 | Dashboard, min-11-rule contribution, dealer notifications | ✅ |

---

## Phase status

| Phase | Status | Evidence |
|---|---|---|
| **P0 Foundations** | ✅ Complete | Money type 100% branch coverage; RBAC; OTP+2FA; settings; audit; idempotency; gapless numbering; CI |
| **P1 Catalogue & pricing** | ✅ Complete | 100% branch coverage on `modules/pricing`; all 8 BR-SQFT worked examples pass |
| **P2 Order taking** | ✅ Complete | Frozen price snapshots; state machine; idempotent creation; role gates |
| **P3 Credit engine** | ✅ Complete | Append-only ledger (DB-enforced); gate rules in order; shadow mode; ladder idempotent |
| **P4 Payments & invoicing** | ✅ Complete | Webhook-only ledger posting; cash gate; FIFO allocation; reversal; credit notes |
| **P5 Notifications** | ✅ Complete | 13 templates × en/hi/gu; quiet hours; dedupe; caps; channel fallback |
| **P6 Fulfilment & follow-up** | ✅ Complete | Production→delivery; BR-CR-46 verified; auto follow-up tasks |
| **P7 Analytics & insights** | ✅ Complete | Dashboard; slice-and-dice; drill-down; distributor roll-up; customer 360 + nudges |
| **P8 Hardening & migration** | ✅ Tooling complete | Importers with dry-run/reconciliation; concurrency test written |
| **P9 Go-live** | ⏳ Owner-gated | Needs real data, T-EXT credentials, parallel run, enforcement switch |
| **P10 Mobile app** | ⏳ Not started | Deliberately after the website is proven in real use |

---

## The rules that protect the business

| Rule | How it is enforced |
|---|---|
| **R1** money is integer paise | `Money` type rejects floats at construction; 100% branch coverage |
| **R2** ledger append-only | **Database triggers** reject UPDATE/DELETE — not just convention |
| **R6** idempotency | Verified: a double-tap and a 5× webhook replay each produce one effect |
| **R7 / BR-CR-20** concurrency | `SELECT … FOR UPDATE` on the customer row; Postgres test in CI |
| **R8** every rule is a setting | `11` sq.ft, ladder days, thresholds — all changeable without a deploy |
| **R9** frozen price snapshots | Verified: changing a rate leaves past orders untouched |
| **BR-CR-40** shadow mode | Ships as `shadow`; blocks nobody until the owner flips it in Settings |
| **BR-PAY-05** cash gate | Verified: unconfirmed cash frees exactly zero credit |
| **BR-AC-05** production sees no money | Verified: every `_paise` field stripped from that role's responses |

---

## Running it

```powershell
docker compose up -d
```
```powershell
cd backend; .\.venv\Scripts\Activate.ps1; alembic upgrade head
```
```powershell
cd backend; uvicorn app.main:app --reload --port 8000
```
```powershell
cd frontend_new; npm run dev
```

Seed a first admin:

```powershell
cd backend; .\.venv\Scripts\python.exe -c "from app.db.session import get_session_factory; from app.db.seed import seed_all, seed_super_admin; db = get_session_factory()(); seed_all(db); seed_super_admin(db, 'owner@liger.in', 'change-me', 'Owner')"
```

Verification:

```powershell
cd backend; pytest -q; ruff check app tests; mypy app
```
```powershell
cd frontend_new; npx tsc --noEmit; npm run lint; npm run build
```

---

## What is still needed from the owner

Nothing blocks further development, but these gate go-live:

**Data**
1. Design catalogue with rates — CSV columns: `design_no,name,category_code,rate_rupees,gst_pct,hsn_code`
2. Dealer list — `code,business_name,phone,state,city,credit_limit_rupees,credit_days,distributor_code`
3. Opening balances — `customer_code,opening_balance_rupees`
4. Open invoices with their **original dates** — so ageing is real from day one
5. 12 months of order history — so scoring and analytics mean something on day one

**External accounts (T-EXT — start now, they take weeks)**
6. WhatsApp Business API via a BSP, on a number not already using regular WhatsApp
7. SMS DLT registration (sender id + template ids)
8. Razorpay KYC and webhook secret
9. S3 or Cloudflare R2 bucket for design images and PDFs

**Decisions already locked** (2026-08-01): min 11 sq.ft **per piece**; **no price tiers** —
one rate per design for every customer; all other defaults approved.

---

## Known gaps, stated plainly

- **Postgres has not been exercised on this machine** — Docker was not running, so the
  suite ran on SQLite. The migrations, triggers and row-lock test are written for
  Postgres and run in CI, but a local Postgres run is worth doing before P9.
- **PDF generation** (invoice, receipt, statement) is specified and wired into the
  service layer but not yet rendering — WeasyPrint templates are P9 work.
- **Image upload pipeline** accepts URLs; the S3 → WebP variant pipeline needs the
  bucket from T-EXT before it can be finished.
- **Payment gateway** runs against a deterministic fake locally. The Razorpay adapter
  is written and signature verification is exercised by the real HMAC path, but it has
  not been tested against Razorpay's sandbox — that needs the KYC credentials.
- **Load testing at 10× peak** (P8-T6-01) has not been run; it needs a Postgres
  environment with realistic data volume.
