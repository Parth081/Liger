# LIGER — Business Rules (Canonical Specification)

> **This document is the source of truth for system behaviour.**
> Every rule has an id. Every implementation must cite its id in a code comment and in its test name.
> When code and this document disagree, **this document is right** — fix the code.
> To change behaviour: change this document first, get owner sign-off, then change code + tests.

Conventions used below:
- **Setting:** `key` — the rule's value lives in the `settings` table and is changeable by the owner without a deploy (rule **R8**).
- **Money** is always integer paise. `₹1,250.50` is stored as `125050`.
- ⚠️ marks an assumption pending owner confirmation.

---

## §0 — Decisions (RESOLVED by owner, 2026-08-01)

| Id | Question | Resolution |
|---|---|---|
| DEC-01 | Min 11 sq.ft per piece or per line? | ✅ **Per piece** — owner confirmed |
| DEC-02 | Rounding of billable sq.ft | ✅ Approved as assumed: round **up** to nearest 0.25 |
| DEC-03 | Customer price tiers | ✅ **NO TIERS.** One rate per design, identical for every customer. Rate cascade is special → base only. `customer_special_rates` retained as an admin exception tool. |
| DEC-04 | GST rate | ✅ Approved as assumed: per design, held on the design record |
| DEC-05 | Making / stitching charge basis | ✅ Approved as assumed: per sq.ft, varying by product type |
| DEC-06 | Default credit days | ✅ Approved as assumed: 30 |
| DEC-07 | Escalation ladder day-offsets | ✅ Approved as assumed: −3, 0, +3, +10, block +15, hard-block +45 |
| DEC-08 | Cash bonus extra limit | ✅ Approved as assumed: +10% of base limit |
| DEC-09 | Part-payment allowed online? | ✅ Approved as assumed: yes |
| DEC-10 | Languages | ✅ Approved as assumed: English, Hindi, Gujarati |
| DEC-11 | Products where min-sq.ft does not apply | ✅ Approved as assumed: none (accessories excluded by BR-CAT-08) |
| DEC-12 | Hardware billing basis | ✅ Approved as assumed: per piece / per running foot, per item |

All values remain **Settings** (rule R8), so any later change is a config edit, not a rebuild.

**Data still owed by the owner (not decisions):** the design catalogue with rates (Excel or any format), dealer list with phones/regions, opening balances, 12 months of order history (needed by P8).

---

## §1 — Units and sq.ft calculation (`BR-SQFT-*`)

| Id | Rule |
|---|---|
| **BR-SQFT-01** | Measurements are entered as **feet + inches** or decimal feet. Canonical storage is **inches**, `NUMERIC(10,2)`. Display is always feet-inches. |
| **BR-SQFT-02** | `raw_sqft = (length_in × breadth_in) / 144`, rounded to 2 decimals. |
| **BR-SQFT-03** | **Minimum billable area is 11 sq.ft.** `billable_sqft = max(raw_sqft, min_billable_sqft)`. Setting: `min_billable_sqft` (default `11.00`). |
| **BR-SQFT-04** | ⚠️ DEC-01 — the minimum applies **per piece**, before multiplying by quantity. |
| **BR-SQFT-05** | ⚠️ DEC-02 — after the minimum is applied, billable sq.ft is rounded **up** to the nearest `sqft_rounding_step` (default `0.25`). Setting value `0` disables rounding. |
| **BR-SQFT-06** | `line_area = billable_sqft × quantity`. |
| **BR-SQFT-07** | When the minimum was applied, the line **must** record `min_rule_applied = true`, and the UI **must** show both `raw_sqft` and `billable_sqft`. The dealer must never be surprised by the number on the invoice. |
| **BR-SQFT-08** | Length, breadth and quantity must each be `> 0`. Quantity is a positive integer. Reject with a field-level error, never silently coerce. |
| **BR-SQFT-09** | Maximum accepted dimension is `max_dimension_in` (default `600` in = 50 ft) — a guard against typos like `770` for `7.70`. |
| **BR-SQFT-10** | Every saved line stores a frozen snapshot: `raw_sqft`, `billable_sqft`, `min_rule_applied`, `rule_version`. Recalculation of a saved line is forbidden (rule **R9**). |
| **BR-SQFT-11** | One implementation only. Live preview, cart, quotation, order and invoice all call the same function. Divergence between preview and bill is a P1 defect. |

**Worked examples** (with defaults 11 sq.ft, step 0.25) — these are the required unit tests:

| L (ft) | B (ft) | Qty | raw_sqft | billable/pc | line_area | Note |
|---|---|---|---|---|---|---|
| 7 | 4 | 1 | 28.00 | 28.00 | 28.00 | above minimum |
| 3 | 3 | 1 | 9.00 | **11.00** | 11.00 | minimum applied |
| 3 | 3 | 4 | 9.00 | **11.00** | **44.00** | minimum applied per piece (DEC-01) |
| 5 | 2.2 | 1 | 11.00 | 11.00 | 11.00 | exactly at the boundary — not rounded up |
| 4 | 2.6 | 1 | 10.40 | **11.00** | 11.00 | minimum applied |
| 7.5 | 4.1 | 2 | 30.75 | 30.75 | 61.50 | already on a 0.25 step |
| 6.4 | 3.3 | 1 | 21.12 | **21.25** | 21.25 | rounded up to step |
| 0 | 4 | 1 | — | — | — | **rejected** (BR-SQFT-08) |

---

## §2 — Catalogue (`BR-CAT-*`)

| Id | Rule |
|---|---|
| **BR-CAT-01** | `design_no` is unique, **case-insensitive**, and must accept Liger's existing offline numbering exactly as-is (no re-numbering at migration). |
| **BR-CAT-02** | A design carries: name, category, sub-category, collection, colour, width of goods, composition, **HSN code**, **GST %**, base rate, UOM, status. |
| **BR-CAT-03** | Categories: Curtain Fabric, Roller Blind, Zebra Blind, Roman Blind, Vertical Blind, Venetian Blind, Wooden Blind, Honeycomb Blind, Sheer, Blackout, Accessory. Extensible by admin. |
| **BR-CAT-04** | Status: `active` / `discontinued` / `out_of_stock`. Only `active` designs can be added to a new order. `discontinued` designs remain readable on historical orders forever. |
| **BR-CAT-05** | Images: one cover + gallery. Served as WebP in 3 sizes — thumb 200px, card 600px, zoom 1600px — from object storage behind a CDN. |
| **BR-CAT-06** | Typing a valid `design_no` in the order form shows image, name, category and the **rate that applies to this customer** (§3), within 300 ms. |
| **BR-CAT-07** | An unknown `design_no` gives a clear "Design not found" plus a search fallback. Never a silent zero-rate line. |
| **BR-CAT-08** | Accessories (rod, track, channel, bracket, chain, motor) are a separate catalogue with their own UOM — ⚠️ DEC-12. They are **not** priced per sq.ft and the min-sq.ft rule does not apply to them. |
| **BR-CAT-09** | Bulk import from Excel with dry-run validation, an error report, and a reconciliation summary before commit. |

---

## §3 — Pricing (`BR-PR-*`)

| Id | Rule |
|---|---|
| **BR-PR-01** | **Rate resolution, first match wins (DEC-03: no tiers):** (1) active customer-specific special rate for that design → (2) design rate from the active rate card → (3) **no rate found ⇒ the line cannot be priced and the order is rejected** with an explicit message. Never default to zero. The rate is otherwise identical for every customer. |
| **BR-PR-02** | The resolved source is recorded on the line as `rate_source` ∈ `special` / `base`, for audit. |
| **BR-PR-03** | Rate cards are **versioned** with `effective_from` / `effective_to`. Publishing a new version never alters existing orders (rule **R9**). |
| **BR-PR-04** | Customer special rates carry `valid_from` / `valid_to` and expire automatically. |
| **BR-PR-05** | `line_goods_amount = line_area × rate_applied`. |
| **BR-PR-06** | ⚠️ DEC-05 — making/stitching charge is added per line, by product type, either per sq.ft (`× line_area`) or per piece (`× quantity`). Stored separately from the goods amount so it is visible on the invoice. |
| **BR-PR-07** | Line discount may be a % or a flat ₹ amount. A sales rep is capped at `max_rep_discount_pct` (default `5`). Above that, the order goes to `PENDING_APPROVAL` — it is not silently rejected. |
| **BR-PR-08** | `line_taxable = line_goods_amount + making_charge − line_discount`. |
| **BR-PR-09** | Order level adds: order discount, freight/transport, packing, round-off. Order discount is apportioned across lines pro-rata **before** tax, so per-line GST stays correct. |
| **BR-PR-10** | Rounding: all intermediate money is computed in paise with `ROUND_HALF_UP` at each stored step. The final invoice total is rounded to the nearest rupee and the difference recorded in `round_off`. |
| **BR-PR-11** | The order total shown at checkout and the invoice total must be **identical to the paise**. A mismatch is a release blocker. |

---

## §4 — Tax (`BR-TAX-*`)

| Id | Rule |
|---|---|
| **BR-TAX-01** | GST % comes from the **design** (⚠️ DEC-04), applied per line on `line_taxable`. |
| **BR-TAX-02** | Place of supply decides the split: customer's state **equals** Liger's state ⇒ CGST + SGST at half each; **differs** ⇒ IGST at the full rate. |
| **BR-TAX-03** | HSN code is captured on every invoice line. |
| **BR-TAX-04** | Customer GSTIN is validated for format and stored; B2C (no GSTIN) is allowed and invoiced accordingly. |
| **BR-TAX-05** | Invoice numbering is a gapless per-financial-year series, allocated inside the invoice transaction. Gaps are a compliance defect. |
| **BR-TAX-06** | A cancelled or reduced invoice is corrected by a **credit note**, never by editing the invoice. |
| **BR-TAX-07** | E-invoicing (IRN/QR) and e-way bill are integration points designed for from P4, activated when the turnover threshold applies. |

---

## §5 — Order lifecycle (`BR-ORD-*`)

| Id | Rule |
|---|---|
| **BR-ORD-01** | States: `DRAFT → PENDING_APPROVAL → CONFIRMED → IN_PRODUCTION → READY → DISPATCHED → DELIVERED → CLOSED`, with branches `CANCELLED`, `ON_HOLD_CREDIT`, `PARTIALLY_DELIVERED`. |
| **BR-ORD-02** | Allowed transitions are an explicit whitelist. Any other transition is rejected by the service layer, not merely hidden in the UI. |
| **BR-ORD-03** | Every transition writes `order_status_history` with actor, timestamp, reason, and fires the notification bound to that transition (§8). |
| **BR-ORD-04** | The **credit gate (§6) runs twice**: at checkout, and again at `CONFIRMED`. Conditions change between the two. |
| **BR-ORD-05** | A **quotation** may be created without any credit check. Converting a quotation to an order runs the full gate. |
| **BR-ORD-06** | Order numbers: `LGR/<FY>/<00001>`, gapless, allocated in-transaction. |
| **BR-ORD-07** | Order creation is idempotent on `Idempotency-Key` (rule **R6**). A repeated key returns the original order, never a second one. |
| **BR-ORD-08** | Orders can be placed by a dealer (web/app) or by staff **on behalf of** a dealer. `placed_by` and `channel` are always recorded. |
| **BR-ORD-09** | Cancellation is allowed up to `IN_PRODUCTION` by admin only, with a mandatory reason. After that it requires a credit note. Cancelling releases the credit exposure immediately. |
| **BR-ORD-10** | Lines may be grouped by room label (Living Room, Bedroom 1…). Grouping is presentational and never affects pricing. |
| **BR-ORD-11** | Draft orders auto-save locally and survive a lost network. A half-typed 40-line order must never be lost. |

---

## §6 — Credit engine (`BR-CR-*`) — the core of the system

### Definitions

| Id | Definition |
|---|---|
| **BR-CR-01** | `outstanding = opening_balance + Σ invoiced − Σ confirmed_payments − Σ credit_notes`, derived from the ledger (rule **R3**). |
| **BR-CR-02** | `exposure = outstanding + Σ (CONFIRMED but not yet invoiced order values)`. **Confirmed orders consume credit immediately** — otherwise a dealer places ten orders in a day and beats the check. |
| **BR-CR-03** | `due_date = invoice_date + customer.credit_days` (⚠️ DEC-06, default 30, per customer). |
| **BR-CR-04** | Ageing buckets: `current`, `1–30`, `31–60`, `61–90`, `90+`, computed per invoice on its own due date. |
| **BR-CR-05** | `effective_limit = base_limit + cash_bonus + active_override`. |
| **BR-CR-06** | `cash_bonus = base_limit × cash_bonus_pct` (⚠️ DEC-08, default 10%), granted only while the customer's confirmed-cash ratio over the trailing 6 months ≥ `cash_ratio_threshold` (default `0.30`). |
| **BR-CR-07** | `available_credit = effective_limit − exposure`. May be negative; displayed as negative, never clamped to zero. |

### Order gate — evaluated in this order, first match wins

| Id | Condition | Decision | Message to dealer |
|---|---|---|---|
| **BR-CR-10** | Customer status = `BLOCKED` | **BLOCK** | "Please clear your outstanding of ₹X to place a new order." Shows the unpaid invoice list + **Pay Now**. |
| **BR-CR-11** | Any invoice overdue > `hard_block_days` (⚠️ DEC-07, default 45) | **BLOCK** | Same as above. *This is the rule that solves the owner's actual problem — age, not just amount.* |
| **BR-CR-12** | Order is fully prepaid (online captured, or admin-confirmed cash) | **ALLOW** | Bypasses BR-CR-13/14 — a prepaid order consumes no credit. |
| **BR-CR-13** | `order_value > available_credit` | **BLOCK / NEEDS_APPROVAL** | "Order exceeds available credit. Available ₹X, this order ₹Y, short by ₹(Y−X)." Offers: pay the difference now, or request admin approval. |
| **BR-CR-14** | Order pushes exposure past `warn_utilisation_pct` (default 80%) | **ALLOW + WARN** | Amber banner to dealer, notification to admin. |
| **BR-CR-15** | Any invoice overdue but within `hard_block_days` | **ALLOW + WARN** | Amber banner + reminder fired. Setting `overdue_soft_block` can flip this to BLOCK. |
| **BR-CR-16** | None of the above | **ALLOW** | — |

| Id | Rule |
|---|---|
| **BR-CR-20** | The gate runs inside a transaction with `SELECT … FOR UPDATE` on the customer row (rule **R7**). **Required test:** two concurrent orders, each individually within the limit but jointly over it — exactly one must be accepted. |
| **BR-CR-21** | The gate returns a structured decision `{decision, reasons[], effective_limit, exposure, available, overdue_invoices[], suggested_payment}`. This object is **stored on the order** so "why was this allowed?" is always answerable. |
| **BR-CR-22** | The decision is never computed in the frontend. The UI renders what the API returned. |

### Colour states — identical on web, admin and app

| Id | State | Condition |
|---|---|---|
| **BR-CR-30** | 🟢 **Green** | no overdue, and exposure < 60% of effective limit |
| **BR-CR-31** | 🟡 **Amber** | exposure 60–90%, **or** overdue 1–15 days |
| **BR-CR-32** | 🔴 **Red** | exposure ≥ 90%, **or** overdue 16–45 days |
| **BR-CR-33** | ⚫ **Blocked** | overdue > `hard_block_days`, **or** manual admin block |

### Escalation and blocking ladder (⚠️ DEC-07 — all offsets are Settings)

| Id | Day vs. due date | Action | Channel |
|---|---|---|---|
| **BR-CR-40** | — | **Shadow mode.** On first go-live, `credit_enforcement_mode = shadow` — the engine logs every decision it *would* have made but blocks nobody. The owner reviews for 2 weeks, then switches to `enforce`. **Enabling enforcement on day one is forbidden.** |
| **BR-CR-41** | −3 | Pre-due reminder | WhatsApp |
| **BR-CR-42** | 0 | Payment due today | WhatsApp |
| **BR-CR-43** | +3 | **Warning 1** — "clear due to keep ordering" + pay link | WhatsApp + SMS |
| **BR-CR-44** | +10 | **Warning 2 (final)** — "account will be blocked in 5 days" + pay link + admin copied + call task created | WhatsApp + SMS + task |
| **BR-CR-45** | +15 | **Auto-block.** No new orders. | WhatsApp + SMS + admin alert |
| **BR-CR-46** | +15 → delivery | Orders already in production or dispatched **continue to completion**, and follow-up continues until goods are delivered and paid. Blocking stops *new* orders; it does not abandon work in progress. | WhatsApp per status change |
| **BR-CR-47** | any | Payment confirmed ⇒ recompute ⇒ **auto-unblock within seconds** if conditions clear, with a confirmation message. | WhatsApp + SMS |
| **BR-CR-48** | — | The ladder is tracked **per invoice**; the block state is **per customer**. A customer with any invoice past +15 is blocked. |
| **BR-CR-49** | — | Each ladder step fires **at most once per invoice**, recorded, and is never re-sent on job re-run (idempotent). |

### Overrides and admin control

| Id | Rule |
|---|---|
| **BR-CR-50** | Admin may raise a limit, or unblock, with a **mandatory reason** and an **expiry date**. Overrides auto-revert on expiry. |
| **BR-CR-51** | Every limit change, block, unblock, warning and override is written to `credit_events` with actor, old value, new value, reason. Immutable. |
| **BR-CR-52** | A manual block by admin outranks all automatic logic and can only be cleared by an admin. |
| **BR-CR-53** | **Simulation:** admin can preview "what if this customer's limit were ₹X" and "what would this rule change do across all customers" **before** committing. Required before any ladder or threshold change goes live. |
| **BR-CR-54** | A nightly job recomputes ageing, advances the ladder, applies auto-blocks, and writes a daily `credit_snapshot` per customer. It is idempotent and safe to re-run. |

---

## §7 — Payments and ledger (`BR-PAY-*`, `BR-LED-*`)

| Id | Rule |
|---|---|
| **BR-PAY-01** | Online methods: UPI, credit/debit cards, net banking, wallets, via Razorpay into Liger's account. |
| **BR-PAY-02** | Offline methods: cash, cheque, NEFT/RTGS/IMPS, UPI-to-bank — entered by staff with a reference number and optional slip photo. |
| **BR-PAY-03** | **The ledger moves only on a signature-verified webhook.** A browser redirect is never trusted as proof of payment. |
| **BR-PAY-04** | Webhook handling is idempotent — the raw event is stored in `gateway_events` and a repeated `event_id` is a no-op. Replays and out-of-order delivery must be safe. |
| **BR-PAY-05** | **Cash gate:** a cash payment lands as `PENDING_CONFIRMATION` and frees **zero** credit. Only an admin marking "cash received" posts it to the ledger and re-evaluates the block state. |
| **BR-PAY-06** | Payments allocate **oldest invoice first (FIFO)** by default; admin can re-allocate manually. Partial payments and unallocated "on account" balances are supported. |
| **BR-PAY-07** | ⚠️ DEC-09 — part-payment against an invoice is allowed online. |
| **BR-PAY-08** | A confirmed payment generates a PDF receipt, delivered on WhatsApp and stored. |
| **BR-PAY-09** | Cheque bounce / gateway reversal posts a reversing entry, re-ages the invoice, and re-applies the ladder from the correct step. |
| **BR-PAY-10** | Payment links are single-purpose, expiring, and carry the invoice reference so allocation is automatic. |
| **BR-LED-01** | The ledger is **append-only** (rule **R2**). No `UPDATE`, no `DELETE`, ever. |
| **BR-LED-02** | Entry types: `opening`, `invoice`, `payment`, `credit_note`, `adjustment`, `reversal`. Every entry stores `balance_after`. |
| **BR-LED-03** | A customer statement for any date range is reconstructable purely from ledger entries. |
| **BR-LED-04** | Σ(ledger) must equal the derived outstanding at all times. A nightly reconciliation job asserts this and alerts on any drift. |
| **BR-LED-05** | Opening balances migrated from the offline books are posted as `opening` entries, per customer, signed off individually before enforcement is enabled. |

---

## §8 — Notifications (`BR-NOT-*`)

| Id | Rule |
|---|---|
| **BR-NOT-01** | Primary channel WhatsApp Business API; fallback SMS via a DLT-registered sender; email for documents; in-app bell; push in the mobile phase. |
| **BR-NOT-02** | Every send is a **queued job** with exponential-backoff retry and a dead-letter queue (rule **R10**). A failing provider never blocks an order. |
| **BR-NOT-03** | Both the **customer number and the configured admin number(s)** are notified on: order placed, order confirmed, credit warning, credit breach, block, unblock, payment received, cash pending confirmation, dispatched, delivered. Admin recipients are configurable per event type. |
| **BR-NOT-04** | Delivery status (sent / delivered / read / failed) is tracked per message and visible per customer in the admin console. |
| **BR-NOT-05** | Quiet hours (default 21:00–08:00 IST) — non-critical messages queue until morning. Block notices are exempt. |
| **BR-NOT-06** | Rate limit: max `max_msgs_per_customer_per_day` (default 4). De-duplication ensures the same reminder is never sent twice for the same invoice-step. |
| **BR-NOT-07** | ⚠️ DEC-10 — templates exist in English, Hindi and Gujarati; the customer's `language` selects one. |
| **BR-NOT-08** | Every template is variable-driven (`{{customer_name}}`, `{{order_no}}`, `{{amount}}`, `{{due_date}}`, `{{pay_link}}`) and pre-approved with the provider. Admin can test-send before going live. |
| **BR-NOT-09** | WhatsApp opt-in consent is recorded per customer with timestamp and source, as required. Opt-out is honoured immediately for marketing, never for transactional/legal notices. |
| **BR-NOT-10** | Provider access is behind an adapter interface — swapping vendors must not touch business code. |

---

## §9 — Customer score and limit recommendation (`BR-SCR-*`)

| Id | Rule |
|---|---|
| **BR-SCR-01** | Score is 0–100, recomputed nightly, stored as a time series so a dealer's trajectory is visible. |
| **BR-SCR-02** | Weights: payment punctuality **30%**, overdue history **20%**, business volume **20%**, order consistency **10%**, cash ratio **10%**, tenure **5%**, disputes/returns **5%** (negative). All weights are Settings. |
| **BR-SCR-03** | Bands: `85+ A+`, `70–84 A`, `55–69 B`, `40–54 C`, `<40 D (watch)`. |
| **BR-SCR-04** | `suggested_limit = band_multiplier × trailing_3_month_avg_monthly_purchase`, capped by `global_limit_ceiling`. |
| **BR-SCR-05** | The score **suggests**; it never changes a limit automatically. **The admin always decides** (BR-CR-50). |
| **BR-SCR-06** | The factor breakdown is stored and shown in plain language — "pays 12 days late on average" — not as an unexplained number. |
| **BR-SCR-07** | A customer with fewer than 3 months of history gets band `NEW` and no suggested limit, to avoid scoring noise. |

---

## §10 — Analytics and insights (`BR-AN-*`)

| Id | Rule |
|---|---|
| **BR-AN-01** | Owner dashboard shows: this month's sales vs. last month vs. same month last year; MTD/QTD/YTD; total outstanding; overdue; ageing pyramid; collection efficiency %; DSO; revenue frozen behind blocked customers. |
| **BR-AN-02** | Every figure is sliceable by month / customer / region / state / distributor / sales rep / design / category / product type, in any combination. |
| **BR-AN-03** | Customers carry region, state, city and an optional **distributor → sub-dealer hierarchy**, with roll-up reporting ("Distributor X: ₹Y this month across 12 dealers in Gujarat"). |
| **BR-AN-04** | **Every number drills down to the individual orders behind it.** A figure that cannot be drilled into will not be trusted and must not ship. |
| **BR-AN-05** | Per-customer insight card: 12-month trend, score + band with reason, outstanding, overdue, available credit, last payment, favourite designs, average order value, order frequency. |
| **BR-AN-06** | Auto-generated nudges, e.g. "ordered monthly for 2 years, nothing in 47 days — call them"; "crossed 90% of limit twice this quarter — review limit"; "pays 8 days early — safe to raise limit". |
| **BR-AN-07** | Digests: daily owner summary (yesterday's orders, collections, new blocks), weekly business review, monthly statement of accounts auto-sent to every dealer. |
| **BR-AN-08** | Analytics read from materialized views / reporting tables, never from live transactional tables under load (rule **R13**). |
| **BR-AN-09** | All reports export to Excel/CSV/PDF. Large exports run as background jobs and notify when ready. |

---

## §11 — Access control (`BR-AC-*`)

| Id | Role | Permissions |
|---|---|---|
| **BR-AC-01** | **Super Admin** | Everything: settings, rate cards, limits, users, financial adjustments, exports |
| **BR-AC-02** | **Admin / Manager** | Orders, customers, limits up to a ceiling, confirm cash, approve over-limit orders, all reports |
| **BR-AC-03** | **Accounts** | Payments, cash confirmation, invoices, credit notes, statements, ageing. **No** rate-card edits |
| **BR-AC-04** | **Sales Rep** | Own customers only — place orders, view credit status, follow-ups, own sales reports. **Cannot** change limits or confirm cash |
| **BR-AC-05** | **Production** | Confirmed orders + specs, production status updates. **No money visibility at all** |
| **BR-AC-06** | **Dispatch** | Ready orders, dispatch/delivery updates, LR entry |
| **BR-AC-07** | **Customer (Dealer)** | Own catalogue, own orders, own invoices, own ledger, pay online, own statements. Strictly scoped — a dealer must never see another dealer's data |
| **BR-AC-08** | — | Permissions are enforced **server-side on every endpoint** (rule **R11**). Every privileged action is audit-logged with actor, IP and timestamp. |
| **BR-AC-09** | — | Staff authenticate with email + password + 2FA; dealers with phone + OTP, rate-limited to 5 OTPs/hour/number with lockout. |
