# LIGER — Order, Credit & Sales Platform
## Production Build Plan (Web first → Mobile app later)

**Business:** Liger — manufacturer of curtains, fabrics and all types of blinds.
**Core problem being solved:** dealers place new orders while old orders are unpaid. Everything is offline today, so nobody knows the real outstanding at the moment of order-taking.
**Goal:** one system that takes orders in Liger's own format (L × B → sq.ft), prices them from Liger's rate card, enforces credit rules automatically, chases payment on WhatsApp/SMS, and shows the owner exactly where sales and money stand.

**Non-negotiables set by you:**
- Production grade, not an MVP demo. Built to scale.
- Website first, perfected; mobile app afterwards on the *same* backend.
- Nothing left out.

---

# PART 0 — WHAT ALREADY EXISTS (honest audit)

A Phase-0 prototype is in this repo. It is a good skeleton but **it is not production-safe yet**. Keep the ideas, rebuild the foundations.

| Area | Present | Verdict for production |
|---|---|---|
| `backend/app/pricing.py` — single sq.ft calculator, min-11 rule from Settings | ✅ | **Keep the idea.** One pricing function used by preview + cart + order is exactly right. Needs money/units rewrite. |
| `backend/app/engines.py` — due derived from ledger, not a stored counter | ✅ | **Keep.** Deriving due from orders − confirmed payments is the correct design. Needs to become invoice/allocation based. |
| `models.py` — `Float` for every money field | ❌ | **Must change.** Floats lose paise. All money → integer paise (`BigInteger`) or `NUMERIC(14,2)`. This is the single most important fix. |
| Credit block on `utilization >= 1.0` only | ⚠️ | Too crude. Real rule needs **overdue-age** blocking, not just limit utilisation. |
| `Notification` table exists, nothing sends | ❌ | No worker, no WhatsApp/SMS provider, no retry, no escalation ladder. |
| Payments | ⚠️ | No gateway, no webhook, no allocation to invoices, no receipt. Cash confirm flow exists in outline — keep it. |
| Auth | ⚠️ | Admin JWT only. No customer login, no OTP, no refresh tokens, no roles beyond `super_admin/staff`. |
| Cart keyed by `customer_id` nullable | ❌ | Anonymous cart rows will collide. Cart must be per authenticated identity. |
| `Base.metadata.create_all` style schema | ❌ | Alembic is in requirements but migrations must become the *only* way schema changes. |
| No tests, no CI, no observability, no backups | ❌ | All required before go-live. |
| Frontend: 3 components, no auth, no state layer | ⚠️ | Rebuild as a proper app shell (see Part 6). |

**Decision: keep the stack (FastAPI + PostgreSQL + Redis + Next.js), rebuild the schema and service layer properly.** You lose ~nothing, because the valuable part is the business logic, and that is being upgraded anyway.

---

# PART 1 — BUSINESS RULES (the source of truth)

Everything else in this document implements this section. **This section must be signed off by you before code is written**, because these are the rules money depends on.

## 1.1 Measurement & sq.ft

- Order line entry: **Length × Breadth**, entered in **feet + inches** (e.g. `7 ft 6 in`) or decimal feet. UI accepts both; system stores one canonical unit.
- Canonical storage: **inches, as `NUMERIC(10,2)`**. Feet displayed everywhere. (Storing inches avoids repeating decimals like 7.333 ft.)
- `raw_sqft = (length_in × breadth_in) / 144`
- **Minimum billable rule: 11 sq.ft.** → `billable_sqft = max(raw_sqft, 11)`
- Applied **per panel/piece**, then × quantity. (⚠️ *Confirm:* per piece, or per order line? Plan assumes **per piece** — that is standard in this trade and is what your current code does.)
- Rounding policy: `raw_sqft` rounded to 2 decimals; billable sq.ft rounded **up** to the nearest 0.25 sq.ft (configurable; can be set to "no rounding"). ⚠️ *Confirm.*
- The rule value (11) and the rounding step live in **Settings**, versioned and audit-logged — never hardcoded.
- Every saved line item stores a **frozen snapshot**: `raw_sqft`, `billable_sqft`, `min_rule_applied`, `rate_applied`, `rule_version`. An old order must never re-price when you change the rate card.

## 1.2 Design number & preview

- `design_no` is the key the dealer types. Must be unique, indexed, case-insensitive, and support your existing offline numbering exactly as-is.
- Design record: name, category (Curtain Fabric / Roller Blind / Zebra Blind / Roman / Vertical / Venetian / Wooden / Honeycomb / Sheer / Blackout / Accessory), sub-category, collection, colour, width of goods, composition, HSN code, GST %, status (active / discontinued / out of stock).
- **Images:** cover image + gallery + optional room-render. Stored in object storage (S3/R2) behind a CDN, served as WebP in 3 sizes (thumb 200px / card 600px / zoom 1600px).
- Live preview: as soon as a valid design number is typed, the image, name, category and rate appear beside the form. Invalid number → clear "Design not found" with a search fallback.
- Bulk import of the full design catalogue from Excel (design no, name, category, rate, HSN, image filename) — one-time migration + ongoing updates.

## 1.3 Pricing

Layered price resolution, **first match wins**:

1. **Customer-specific special rate** for that design (rare, admin-set, with expiry date)
2. **Customer price-tier rate** (Tier A / B / C — dealer, distributor, project)
3. **Design base rate** from the active rate card
4. Failing all → order line cannot be priced; blocked with a clear message

Plus, per line:
- Making / stitching charge (per sq.ft or per piece, by product type)
- Hardware & accessories (rod, track, channel, bracket, chain, motor) — separate catalogue items with their own units, **not** sq.ft
- Line discount % or ₹ (permission-controlled; sales staff capped at X%, above that needs admin approval)
- Then order level: order discount, freight/transport, packing, round-off
- **GST**: CGST+SGST for intra-state, IGST for inter-state, computed from your state vs. customer's state, at design-level GST%. HSN captured per line.

**Rate cards are versioned.** Publishing a new rate card creates a new version with an effective date; historical orders stay attached to the version they were priced under. This is how you change prices without corrupting past invoices.

## 1.4 Credit rules (the heart of the system)

Definitions:
- **Outstanding** = opening balance (migrated from your books) + all invoiced amounts − all *confirmed* payments − credit notes.
- **Overdue** = any invoice past its due date. `due_date = invoice_date + credit_days` (credit_days per customer, default 30 — configurable).
- **Ageing buckets:** Current, 1–30, 31–60, 61–90, 90+.
- **Credit limit** = base limit set by admin (or suggested by score, see 1.6) + **cash bonus**.
- **Cash bonus:** customers with a good confirmed-cash history get `+X%` extra limit (X per customer, admin-set, default from Settings). Applies only while their cash ratio stays above the threshold.
- **Exposure** = outstanding + value of confirmed-but-uninvoiced orders (work in progress). New orders must count against the limit *immediately*, not only after invoicing — otherwise a dealer can place ten orders in one day and beat the check.
- **Available credit** = effective limit − exposure.

### Order gate (runs at checkout, and again at order confirmation)

| # | Rule | Behaviour |
|---|---|---|
| R1 | Customer status = `BLOCKED` | **Hard stop.** "Please clear your due of ₹X to place a new order." Shows the exact unpaid invoice list + a Pay Now button. |
| R2 | Any invoice overdue beyond `hard_block_days` (default 45) | **Hard stop**, same as R1. This is the rule that actually solves your problem — age, not just amount. |
| R3 | Order value > available credit | **Blocked** with "Order exceeds available credit. Available ₹X, this order ₹Y." Offers: pay ₹(Y−X) now, or **request admin approval**. |
| R4 | Order value ≤ available credit but pushes exposure past 80% | **Allowed**, with an amber warning shown to dealer + a notification to admin. |
| R5 | Customer has any overdue invoice but under `hard_block_days` | **Allowed with warning banner** + reminder fired. Configurable to "block" if you want stricter. |
| R6 | Order paid fully upfront (online or admin-confirmed cash) | **Bypasses R3/R4** — a prepaid order consumes no credit. |

### Colour language (must be identical everywhere — web, app, admin)
- 🟢 **Green** — no overdue, exposure < 60% of limit
- 🟡 **Amber** — exposure 60–90%, or overdue 1–15 days
- 🔴 **Red** — exposure ≥ 90%, or overdue 16–45 days
- ⚫ **Black / Blocked** — overdue > 45 days, or manual admin block

### Escalation & block ladder (the "notify 2 times then block" rule)

| Day (relative to due date) | Action | Channel |
|---|---|---|
| −3 | Gentle pre-due reminder | WhatsApp |
| 0 | Payment due today | WhatsApp |
| +3 | **Warning 1** — "clear due to keep ordering" + pay link | WhatsApp + SMS |
| +10 | **Warning 2 (final)** — "account will be blocked in 5 days" + pay link + admin CC'd | WhatsApp + SMS + call task for staff |
| +15 | **Auto-block.** No new orders. | WhatsApp + SMS + admin alert |
| +15 → delivery | Orders already in production/dispatch **continue**, and follow-up continues until the goods are delivered and paid | WhatsApp on every status change |
| Any day | Payment confirmed → **auto-unblock within seconds**, confirmation message sent | WhatsApp + SMS |

All day-offsets are **Settings**, changeable by you without a code change. The ladder is per-invoice, and the state machine is per-customer.

- **Grace / manual override:** admin can grant a temporary limit increase or unblock, with mandatory reason + expiry date; auto-reverts on expiry; fully audit-logged.
- **Cash confirmation gate:** a cash payment entered by staff or claimed by a dealer sits at `PENDING_CONFIRMATION` and **frees zero credit** until an admin marks "cash received". Only then does the ledger move and the customer possibly unblock. This is exactly the control you asked for.

## 1.5 Payments

- **Online (dealer or staff-initiated):** Razorpay (recommended for India) — UPI, all credit/debit cards, net banking, wallets, and **UPI AutoPay/mandate** for recurring settlement if you ever want it. Money lands in Liger's current account.
  - Payment Links sent over WhatsApp/SMS for one-tap collection — this alone usually lifts collection rates sharply.
  - **Webhook-driven ledger updates only.** Never trust the browser redirect. Signature-verified, idempotent, replay-safe.
- **Offline:** cash, cheque, NEFT/RTGS/IMPS, UPI-to-bank. Entered by staff with reference number + optional photo of slip → admin confirms → ledger moves.
- **Allocation:** payment applies **oldest-invoice-first (FIFO)** by default; admin can re-allocate manually against specific invoices. Partial payments and on-account (unallocated) balances supported.
- **Receipts:** auto-generated PDF receipt on every confirmed payment, sent on WhatsApp.
- Cheque bounce / payment reversal handling, with automatic re-ageing and re-blocking.
- **Ledger is append-only.** Corrections are made by posting a reversing entry, never by editing or deleting. This is what makes the numbers trustworthy.

## 1.6 Customer score & limit recommendation

A 0–100 score, recomputed nightly, driving the *suggested* limit. **Admin always has the final say** — the system recommends, you decide.

| Factor | Weight | Meaning |
|---|---|---|
| Payment punctuality | 30% | Avg. days-to-pay vs. agreed credit days, last 12 months |
| Overdue history | 20% | Count/severity of times crossed into red or blocked |
| Business volume | 20% | Trailing 12-month purchase value, trend-adjusted |
| Consistency | 10% | Order frequency & regularity |
| Cash ratio | 10% | Share paid in confirmed cash / prepaid |
| Tenure | 5% | How long they have been a Liger customer |
| Disputes / returns | 5% | Rejections, quality claims, cheque bounces (negative) |

- Bands: 85+ **A+**, 70–84 **A**, 55–69 **B**, 40–54 **C**, <40 **D (watch)**.
- Suggested limit = band multiplier × trailing-3-month average monthly purchase, capped by a global ceiling.
- Every score change is stored as a time series, so you can see a dealer's trajectory, and every limit change is audit-logged with actor, old value, new value, reason.

## 1.7 Order lifecycle

`DRAFT → PENDING_CREDIT_APPROVAL → CONFIRMED → IN_PRODUCTION → READY → DISPATCHED → DELIVERED → CLOSED`
with side branches `CANCELLED`, `ON_HOLD (credit)`, `PARTIALLY_DELIVERED`.

- Every transition: timestamped, actor-stamped, notification-triggered, irreversible except by explicit admin action.
- Optional per-order fields you will want from day one: expected delivery date, site/party name, transport/LR number, remarks, attached photos.
- **Quotation mode:** a quote can be created without touching credit, then converted to an order (which is when the credit gate runs).

## 1.8 Notifications (customer + admin, as you asked)

- **Primary channel: WhatsApp Business API** (via AiSensy / Gupshup / Interakt / 360dialog). India-first, highest read rate, supports images (design preview!), PDFs (invoice/receipt) and buttons (Pay Now).
- **Fallback: SMS** via a DLT-registered sender (MSG91/Kaleyra). Required by TRAI — templates must be pre-registered.
- **Also:** email (invoice PDFs, statements), in-app bell, and admin push (mobile phase).
- Every notification is a **queued job with retries**, delivery-status tracking (sent/delivered/read/failed), and a permanent log per customer.
- Quiet hours (e.g. no messages 21:00–08:00), rate limiting (max N messages per customer per day), and de-duplication so a dealer never gets the same reminder twice.
- Both **customer number and admin number(s)** notified on: order placed, order confirmed, credit warning, credit breach, block, unblock, payment received, cash confirmation pending, dispatch, delivery.
- Templates in **English + Hindi + Gujarati** (chosen per customer), because your dealers will not all read English.

## 1.9 Analytics & insights (what you asked for at the end)

**Owner dashboard**
- This month's sales vs. last month vs. same month last year; MTD/QTD/YTD
- Total outstanding, overdue, and the ageing pyramid — with "money at risk" front and centre
- Collection efficiency %, average days-to-pay (DSO)
- Top customers, top regions, top distributors, top designs, top categories
- Blocked customers and how much revenue is frozen behind them

**Slice-and-dice** — by **month / customer / region / state / distributor / sales rep / design / category / product type**, any combination, exportable to Excel and PDF.

**Region & distribution mapping**
- Customers carry region, state, city, and an optional **distributor/dealer hierarchy** (Distributor → sub-dealers). Roll-up reporting so you see "Distributor X did ₹Y this month across 12 dealers in Gujarat."

**Per-customer insight card** (the "user-defined short insight" you described) — for any dealer, at a glance:
- 12-month purchase trend sparkline, score + band with reason ("pays 12 days late on average")
- Outstanding, overdue, available credit, last payment date & amount
- Favourite designs/categories, average order value, order frequency
- **Auto-generated nudges:** "Ordered every month for 2 years — nothing in 47 days. Call them." / "Crossed 90% of limit twice this quarter — consider a limit review." / "Pays 8 days early on average — safe to raise limit."
- Follow-up task list with owner, due date, notes, and call outcome logging

**Digest jobs:** daily WhatsApp/email summary to owner (yesterday's orders, collections, new blocks), weekly business review, monthly statement of accounts sent automatically to every dealer.

---

# PART 2 — ARCHITECTURE

## 2.1 Principles

1. **API-first.** The web app and the future mobile app are both clients of one versioned REST API (`/api/v1`). No business logic in the frontend, ever. This is what makes the mobile phase cheap.
2. **Money is integer paise.** Never float. Ever.
3. **Ledger is append-only.** Balances are derived, then cached — never a mutable counter that can drift.
4. **Everything credit-related is a state machine** with explicit transitions and an event log.
5. **All side effects are queued jobs**, not inline HTTP work. A slow WhatsApp API must never slow down an order.
6. **Every rule is a setting**, versioned and audit-logged.
7. **Idempotency everywhere** that touches money — order creation, payments, webhooks.
8. **Multi-user safe.** Row-level locking on credit checks so two simultaneous orders cannot both slip under the limit.

## 2.2 Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | **Python 3.12 + FastAPI** | Already started; async, fast, great for this. |
| ORM / migrations | SQLAlchemy 2.0 + **Alembic** | Migrations become the only schema path. |
| Database | **PostgreSQL 16** | Transactions, `NUMERIC`, partial indexes, materialized views for analytics, row-level locks. |
| Cache / locks / queue broker | **Redis 7** | Already in your compose. |
| Background jobs | **Celery** (or Dramatiq) + **Celery Beat** | Notifications, reminders, score recompute, digests, webhook retries, report generation. |
| Object storage | **S3 / Cloudflare R2** + CDN | Design images, invoices, receipts, payment-slip photos. |
| Web frontend | **Next.js 16 (App Router) + TypeScript + Tailwind v4** | Already started. Server components for dashboards, client for forms. |
| Frontend data layer | TanStack Query + Zod + react-hook-form | Type-safe forms, cache, optimistic UI. |
| Auth | JWT access (15 min) + rotating refresh (30 d), httpOnly cookies for web | Customer OTP login, staff email+password+2FA. |
| PDF | WeasyPrint / ReportLab | Invoices, receipts, statements, order confirmations. |
| Search | Postgres full-text + trigram (`pg_trgm`) | Design/customer search. Elasticsearch only if catalogue > 100k. |
| Observability | Sentry + structured JSON logs + Prometheus/Grafana | Errors, latency, queue depth, failed notifications. |
| Deploy | Docker Compose on a managed VPS → containers behind a load balancer as you grow | See Part 8. |
| CI/CD | GitHub Actions: lint → type-check → test → migrate → deploy | No manual deploys. |

## 2.3 Service layout (modular monolith — right choice at your scale)

```
backend/app/
  core/          config, security, logging, exceptions, idempotency, money type
  db/            session, base, alembic/
  modules/
    identity/    users, roles, permissions, OTP, sessions, 2FA
    customers/   master, addresses, contacts, regions, distributor hierarchy, tiers
    catalog/     designs, categories, images, HSN, stock flag, import
    pricing/     rate cards, tiers, special rates, sqft engine, tax engine
    orders/      cart, quotation, order, line items, state machine, documents
    credit/      exposure, ageing, limits, blocking ladder, overrides, score
    payments/    gateway, webhooks, cash confirmation, allocation, receipts
    invoicing/   invoice, credit note, GST, numbering series, PDF
    fulfilment/  production status, dispatch, delivery, LR/transport
    notifications/ templates, providers, queue, delivery status, preferences
    analytics/   aggregates, materialized views, reports, exports, insights
    admin/       settings, audit log, imports, backups, feature flags
  workers/       celery app, beat schedule, task modules
  api/v1/        thin routers only — validation in, service call, response out
```

**Not microservices.** A modular monolith with clean module boundaries is faster to build, cheaper to run, and easy to split later if a module ever needs its own scale. Splitting early would be the classic mistake here.

## 2.4 Scalability plan (concrete, not hand-wavy)

- **Now → 100 dealers, ~200 orders/day:** single app container + Postgres + Redis on one good VPS. Comfortable.
- **→ 1,000 dealers:** 2–3 API containers behind a load balancer, Postgres with a read replica, analytics queries served from the replica + materialized views refreshed every 15 min, images on CDN.
- **→ 10,000+ dealers / multi-plant:** partition `orders`/`ledger_entries` by month, move analytics to a nightly-built reporting schema, separate Celery worker pools per queue class (`critical` / `notifications` / `reports`), consider read-only API keys for large distributors.
- Database rules that make this possible **from day one**: no `SELECT *`, every foreign key indexed, composite indexes on `(customer_id, created_at)`, `(status, due_date)`, cursor pagination on every list endpoint (never OFFSET), and hard-capped page sizes.
- **Caching:** customer credit snapshot cached in Redis with event-driven invalidation on any order/payment/limit change. Analytics cached 5–15 min. Design images immutable + far-future cache headers.

---

# PART 3 — DATA MODEL

Core tables (each with `id BIGSERIAL`, public `uid UUID`, `created_at`, `updated_at`, `created_by`, `updated_by`, and soft-delete where sensible). **All money columns `BIGINT` paise.**

**Identity & access**
- `users` — staff & admins: name, email, phone, password hash, role, 2FA secret, active
- `customer_users` — dealer logins: phone (unique), name, customer_id, OTP state, active
- `roles`, `permissions`, `role_permissions` — RBAC (see 4.1)
- `sessions` / `refresh_tokens` — device, IP, revocation
- `audit_log` — actor, action, entity, entity_id, before/after JSON, IP, timestamp

**Customer master**
- `customers` — code, business name, legal name, GSTIN, PAN, phones, emails, **region_id**, state, city, pincode, price_tier, credit_limit, credit_days, cash_bonus_pct, status, blocked_at, block_reason, opening_balance, distributor_id (self-FK), sales_rep_id, language, notes
- `customer_addresses` — billing / multiple shipping
- `customer_contacts` — owner, accounts person, site person (each with own WhatsApp number)
- `regions` — hierarchy: country → zone → state → city/territory

**Catalogue**
- `categories`, `designs` (design_no unique-ci, category_id, hsn_code, gst_pct, base_rate, uom, width, composition, status), `design_images`, `design_stock` (optional)
- `accessories` — hardware items with own UOM and rate

**Pricing**
- `rate_cards` (version, effective_from, effective_to, published_by), `rate_card_items` (design_id, tier, rate)
- `customer_special_rates` (customer_id, design_id, rate, valid_from, valid_to)
- `making_charges` (product_type, mode: per_sqft|per_piece, amount)

**Orders**
- `carts`, `cart_items` (owner = customer_user or staff-on-behalf-of-customer)
- `quotations`, `quotation_items`
- `orders` — order_no, customer_id, placed_by, channel (web/app/staff), status, subtotal, discount, taxable, cgst, sgst, igst, freight, round_off, grand_total, expected_delivery, credit_decision JSON snapshot, idempotency_key
- `order_items` — design_id, design_no snapshot, design_name snapshot, length_in, breadth_in, qty, raw_sqft, billable_sqft, min_rule_applied, rate_applied, rate_source, making_charge, line_discount, taxable, gst_pct, line_total
- `order_status_history` — from, to, actor, reason, at

**Money**
- `invoices`, `invoice_items` — GST-compliant, numbering series per FY
- `credit_notes`
- `payments` — customer_id, amount, method, gateway_ref, status (`initiated|pending_confirmation|confirmed|failed|reversed`), confirmed_by, confirmed_at, slip_url, notes, idempotency_key
- `payment_allocations` — payment_id, invoice_id, amount (this is how FIFO/manual allocation is recorded)
- `ledger_entries` — **append-only**: customer_id, entry_type (invoice/payment/credit_note/adjustment/opening), debit, credit, ref_type, ref_id, balance_after, posted_at
- `gateway_events` — raw webhook payloads, signature, processed flag (idempotency + audit)

**Credit**
- `credit_snapshots` — daily per customer: outstanding, overdue by bucket, exposure, available, status, score
- `credit_events` — warned / blocked / unblocked / limit_changed / override_granted, with actor & reason
- `credit_overrides` — customer_id, extra_limit, valid_until, reason, granted_by
- `customer_scores` — score, band, factor breakdown JSON, computed_at

**Fulfilment**
- `production_jobs`, `dispatches` (transporter, LR no, vehicle, docket), `deliveries` (POD photo, received_by, at)

**Notifications**
- `notification_templates` — key, channel, language, body, variables, provider_template_id, approval status
- `notifications` — customer_id, user_id, channel, template_key, payload, status, provider_msg_id, attempts, error, sent_at, delivered_at, read_at
- `notification_preferences` — per customer: channels, language, quiet hours, opt-outs

**Ops**
- `settings` — key, value, type, description, updated_by (min_sqft, rounding, ladder days, thresholds, cash bonus default…)
- `import_jobs` — file, type, rows ok/failed, error report
- `saved_reports`, `report_runs`
- `follow_up_tasks` — customer_id, type, due_date, assignee, status, outcome, notes

---

# PART 4 — MODULE SPECIFICATIONS

## 4.1 Roles & permissions

| Role | Can do |
|---|---|
| **Super Admin (you)** | Everything: limits, rate cards, settings, user management, financial adjustments, exports |
| **Admin / Manager** | Orders, customers, limits within a ceiling, confirm cash, approve over-limit orders, all reports |
| **Accounts** | Payments, cash confirmation, invoices, credit notes, statements, ageing reports. No rate-card edits. |
| **Sales Rep** | Own customers only: place orders, view their credit status, follow-up tasks, own sales reports. Cannot change limits or confirm cash. |
| **Production** | Sees confirmed orders + specs, updates production/ready status. No money visibility at all. |
| **Dispatch** | Ready orders, dispatch & delivery updates, LR entry |
| **Customer (Dealer)** | Own catalogue view, place orders, own orders/invoices/ledger, pay online, own statements |

Permission checks are enforced **server-side on every endpoint**. The UI merely hides what the API would refuse anyway.

## 4.2 Order-taking screen (your core format — get this perfect)

Layout: entry form on the left, live design preview + running bill on the right.

Per line, the dealer enters: **Design No → Length → Breadth → Qty**, plus optional making/hardware/remarks.

As they type:
- Design preview image, name, category and rate appear instantly (debounced lookup, cached)
- `raw sq.ft` shown; if under 11, a clear amber note: **"Min 11 sq.ft applied"** with both numbers visible so there is never an argument later
- Line amount, running subtotal, GST and grand total update live
- A persistent **credit strip** at the top: `Limit ₹X · Outstanding ₹Y · Available ₹Z` in green/amber/red, updating as the cart grows
- The moment cart total crosses available credit, the strip turns red and shows exactly how much is over — before they waste time finishing the order

Then: Save as draft · Save as quotation · Place order.

On Place Order → credit gate runs server-side (with a row lock) → allowed / warned / blocked, with the blocked screen showing the unpaid invoice list and a **Pay Now** button.

Efficiency features that matter for real dealers:
- **Repeat last order** / duplicate any past order in one tap
- Keyboard-first entry (Tab through L → B → Qty → Enter adds line) — staff will do 50 lines a day
- Bulk paste from Excel for large orders
- Room-wise grouping of lines (Living Room / Bedroom 1 …) — normal in this trade
- Offline-tolerant draft saving, so a lost network never loses a half-typed order
- Order confirmation PDF auto-generated and WhatsApp'd

## 4.3 Credit engine internals

- `evaluate_order(customer_id, cart, payment_intent)` returns a **structured decision object**: `{ decision: ALLOW|WARN|BLOCK|NEEDS_APPROVAL, reasons[], limit, exposure, available, overdue_invoices[], suggested_payment }`. This exact object is stored on the order for audit — so you can always answer "why was this allowed?"
- Runs inside a transaction with `SELECT … FOR UPDATE` on the customer row → **two concurrent orders cannot both pass**.
- Re-evaluated at confirmation time (things change between cart and confirm).
- Nightly job: recompute ageing → advance the escalation ladder → fire notifications → auto-block where due → write `credit_snapshots`.
- Real-time triggers: on payment confirmation → recompute → auto-unblock + notify.
- **Simulation mode:** admin can ask "what if I set this dealer's limit to ₹5L?" and see the effect before committing. Also lets you dry-run rule changes across all customers before switching them on — essential when you go live with existing dealers.

## 4.4 Notification engine

- Template registry with variables (`{{customer_name}}`, `{{order_no}}`, `{{amount}}`, `{{due_date}}`, `{{pay_link}}`)
- Provider adapter interface → swap WhatsApp vendors without touching business code
- Queue with exponential-backoff retry, dead-letter queue, delivery webhooks
- Per-customer language, quiet hours, opt-out, daily cap, de-duplication
- Admin console: see every message sent to a customer, resend, and a **test-send** before any template goes live
- WhatsApp template pre-approval and SMS DLT registration are **lead-time items — start them in Week 1** (approval takes days to weeks)

## 4.5 Analytics implementation

- Nightly-built **reporting tables** + 15-minute **materialized views** for the live dashboard — never run heavy aggregates against the live orders table.
- Pre-computed cubes: sales by (month × customer × region × distributor × category × design).
- All reports exportable to Excel/CSV/PDF; large exports run as background jobs and are emailed/WhatsApp'd when ready.
- Scheduled digests: daily owner summary, weekly business review, monthly dealer statements.
- Every number on the dashboard is **drillable down to the individual order** — a dashboard you cannot drill into is a dashboard nobody trusts.

---

# PART 5 — WEB APPLICATION (Phase 1 deliverable)

## 5.1 Dealer portal (`liger.in/app`)
Login (phone + OTP) · Home (credit status, dues, quick reorder) · Catalogue (browse/search/filter with images) · **New Order** (the L×B screen) · Cart & checkout · My Orders + live status tracking · Invoices & downloads · **Ledger / statement** · Pay Now (all methods) · Payment history · Profile & notification preferences · Support/contact.

## 5.2 Admin console (`liger.in/admin`)
Dashboard · Orders (list, filter, detail, status updates, approvals queue) · Customers (master, 360° view, limits, score, block/unblock, insights) · Catalogue & rate cards · Payments (record, confirm cash, allocate, reconcile) · Invoices & credit notes · Credit centre (ageing, blocked list, overrides, escalation queue) · Follow-ups (task board) · Notifications log · Reports & exports · Users & roles · Settings · Audit log.

## 5.3 Quality bar (this is what "production, not MVP" means)
- Mobile-responsive — many dealers will use the site on a phone before the app exists
- Fast: LCP < 2.5s on 4G, API p95 < 300ms
- Every destructive action confirmed; every form validated client **and** server side
- Empty states, loading skeletons, and error states designed — not afterthoughts
- Accessible (keyboard navigation, contrast, labels)
- English + Hindi + Gujarati UI
- Printable/PDF versions of every document
- **No number ever shown that the user cannot drill into or trace**

---

# PART 6 — MOBILE APP (Phase 2, after the website is perfect)

- **React Native + Expo**, one codebase for Android + iOS. Android first — that is what your dealers use.
- Consumes the **same `/api/v1`**. Zero backend rework — that is the payoff of Part 2's API-first rule.
- Adds what only a phone can do: push notifications, camera (measurement photos, payment slips, delivery POD), offline order drafting with sync, biometric login, contact-based quick call, share order/invoice straight to WhatsApp.
- Two apps or one app with role-based screens — recommend **one app, role-switched**: cheaper to maintain, and your staff and dealers get the same reliability.
- Play Store / App Store accounts, privacy policy, and release pipeline (EAS Build + OTA updates for fast fixes).

---

# PART 7 — SECURITY, COMPLIANCE, RELIABILITY

**Security:** bcrypt/argon2 passwords · JWT with short expiry + rotating refresh · 2FA for admin · OTP rate-limited (max 5/hour/number) with lockout · RBAC enforced server-side · all input validated via Pydantic · ORM-only queries (no string SQL) · rate limiting per IP and per user · CORS locked to your domains · security headers + HTTPS/HSTS only · secrets in a secret manager, **never in `.env` committed to git** (⚠️ `backend/.env` exists in this repo — verify it is git-ignored and rotate anything already exposed) · signed webhook verification · uploaded files virus-scanned and served from a separate domain.

**Compliance (India):** GST-compliant invoice format with HSN, GSTIN, place of supply · e-invoicing (IRN/QR) if turnover crosses the threshold · e-way bill integration for goods movement · TRAI DLT registration for SMS · WhatsApp opt-in consent recorded per customer · DPDP Act — privacy policy, consent, data-deletion process · financial records retained 8 years.

**Reliability:** automated daily Postgres backups with **restore tested monthly** (an untested backup is not a backup) · point-in-time recovery · object-storage versioning · health checks + uptime monitoring with SMS alert to you · graceful degradation (if WhatsApp is down, orders still work and messages queue) · zero-downtime deploys · documented rollback.

**Testing:** unit tests on every pricing/credit/tax path (target 90%+ on those modules — this is where money is lost) · integration tests on order→credit→payment→unblock flows · a **golden test set of real historical orders**: the system must reproduce the amounts your books already show, to the paise · concurrency tests (two simultaneous orders on one limit) · E2E (Playwright) on the critical dealer journey · load test at 10× expected peak before go-live.

---

# PART 8 — ENVIRONMENTS, INFRA, DELIVERY

- **Environments:** local (Docker Compose) → staging (production-like, sanitised data) → production. Nothing reaches production without passing staging.
- **Infra:** managed Postgres (backups + PITR handled for you) · app containers behind a load balancer · Redis · Celery workers (separate pools per queue) · Celery Beat · S3/R2 + CDN · Sentry + Grafana.
- **CI/CD:** GitHub Actions — lint (ruff) → type-check (mypy/tsc) → tests → build image → auto-deploy staging → manual approve → production, with automatic Alembic migration and rollback plan.
- **Data migration (critical, do not underestimate):** Excel importers for customers, opening balances, designs + images, historical orders (at least 12 months, for scoring and analytics to be meaningful on day one), and open invoices. Every import: dry-run → validation report → confirm → import → reconciliation report proving totals match your books.

---

# PART 9 — ROADMAP

Sequenced so that **you can start collecting money as early as possible**, while nothing ships half-built.

| Phase | Weeks | Deliverable |
|---|---|---|
| **P0 — Foundations** | 1–2 | Schema rebuild (paise, ledger, audit), Alembic, RBAC, auth (staff + dealer OTP), settings framework, CI, staging. **Start WhatsApp + DLT + Razorpay onboarding now.** |
| **P1 — Catalogue & pricing** | 3–4 | Designs, images/CDN, categories, HSN, rate cards + tiers + special rates, sq.ft engine with min-11 & rounding, tax engine, catalogue import from your Excel |
| **P2 — Order taking** | 5–7 | The L×B order screen (live preview, live totals, credit strip), cart, quotations, order lifecycle, order PDF, staff order-on-behalf |
| **P3 — Credit engine** | 8–10 | Exposure, ageing, limits, colour states, block ladder, overrides, cash bonus, simulation mode, credit centre in admin |
| **P4 — Payments & invoicing** | 11–13 | Razorpay + webhooks, pay links, cash confirmation flow, FIFO allocation, receipts, GST invoices, credit notes, ledger & statements |
| **P5 — Notifications** | 14–15 | WhatsApp + SMS engine, all templates in 3 languages, escalation ladder live, delivery tracking, admin notification console |
| **P6 — Fulfilment & follow-up** | 16–17 | Production/dispatch/delivery tracking, follow-up task board, delivery-time follow-up chain |
| **P7 — Analytics & insights** | 18–20 | Owner dashboard, all slice-and-dice reports, region/distributor roll-ups, customer scoring, per-customer insight cards, digests, exports |
| **P8 — Hardening & migration** | 21–23 | Load testing, security review, full data migration + reconciliation, staff training, documentation, **parallel run alongside your offline books** |
| **P9 — Go-live** | 24 | Phased rollout: internal staff first → 10 pilot dealers → all dealers. Hypercare support period. |
| **P10 — Mobile app** | 25–32 | React Native app on the same API, Android then iOS, push notifications, store release |

**Parallel run in P8 is not optional.** For 2–4 weeks the system and your existing books run side by side and must agree. That is what buys the confidence to switch off the old way.

---

# PART 10 — RISKS & MITIGATIONS

| Risk | Mitigation |
|---|---|
| Dealers resist a system that blocks them | Phased rollout, dealer training, and generous initial limits + a grace period; tighten after adoption. Frame it as "see your account anytime, pay in one tap." |
| Wrong opening balances at migration | Reconciliation report + parallel run + sign-off per customer before any auto-block is switched on |
| **Auto-block goes live too early and stops real business** | Ship blocking in **shadow mode first** — it logs what it *would* have blocked for 2 weeks; you review, then enable enforcement |
| WhatsApp template approval delays | Start in Week 1; SMS fallback ready; email as third channel |
| Pricing disputes | Every order stores a frozen price snapshot + rule version; full audit trail settles any argument |
| Staff comfort with software | Keyboard-first order entry, Hindi/Gujarati UI, staff can place orders on behalf of dealers who never adopt the portal |
| Scope creep | This document is the scope. Anything new goes to a Phase-2 list, not into the current phase. |

---

# PART 11 — WHAT I NEED FROM YOU TO FINALISE THIS

None of this blocks starting P0, but each item is needed before its phase begins.

**Before P1 (pricing) — most important:**
1. **Your rate card / pricing sheet** — design numbers with rates, and how rates differ by product type
2. Do you have **customer tiers** (different rates for different dealers)? How many?
3. **Min 11 sq.ft** — per piece or per order line? And is there any product where it does not apply?
4. Any **rounding rule** on sq.ft (e.g. round up to nearest 0.25 / 0.5)?
5. How are **making/stitching charges** billed — per sq.ft, per piece, or included?
6. Hardware (rod, track, channel) — billed per piece/per foot?
7. GST %: single rate or different by category? Your GSTIN and state.
8. Design catalogue export + images (any format — Excel/Tally/photos folder)

**Before P3 (credit):**
9. Current **credit days** you allow (30/45/60?), and does it vary by dealer?
10. Your existing **credit limits per dealer**, if any, and current **outstanding balances**
11. Exact block trigger you want: at how many days overdue? (plan assumes warn +3, final +10, block +15)
12. Cash bonus: how much extra limit for cash-paying dealers — flat % or per dealer?

**Before P4 (payments):**
13. Bank account + **Razorpay** (or preferred gateway) onboarding — KYC takes days
14. Do you want dealers to pay **part-payment** online, or full invoice only?

**Before P5 (notifications):**
15. WhatsApp Business number (a number **not** already on regular WhatsApp) + Facebook Business verification
16. Admin numbers that should receive alerts, and which alerts each should get
17. Languages needed — English / Hindi / Gujarati / others?

**Before P8 (migration):**
18. 12 months of historical order + payment data, in whatever form it exists
19. List of staff who will use the system, and which role each gets

---

## Immediate next step

Confirm **Part 1 (Business Rules)** — especially §1.1 min-sq.ft handling, §1.4 the block ladder days, and §1.6 the scoring weights. Those three decide how the whole engine behaves.

Once you sign off Part 1, P0 starts: rebuild the schema on integer paise with a proper append-only ledger, and simultaneously begin the WhatsApp / DLT / payment-gateway onboarding, because those have external waiting times that nothing in the code can shorten.
