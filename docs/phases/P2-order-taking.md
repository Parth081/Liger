# P2 — Order Taking (Weeks 5–7)

**Goal:** the screen the business actually lives on — `Design No → L × B × Qty`, live sq.ft, live design preview, live total, and a real order at the end of it. Fast enough for staff entering 50 lines a day.

**Entry gate:** P1 Definition of Done fully green (pricing is exact and covered).
**Rules implemented:** BR-ORD-01…11, BR-CAT-06/07, BR-SQFT-07/11, BR-AC-04/07/08. Credit gate is stubbed here and completed in P3.

---

## T1 — Data & migrations

| Id | Task |
|---|---|
| P2-T1-01 | `carts`, `cart_items` — owner-bound, never anonymous |
| P2-T1-02 | `orders`, `order_items` with **full frozen snapshots** (design_no, design_name, rate, rate_source, sqft breakdown, tax) per `DATA_MODEL` §5 |
| P2-T1-03 | `order_status_history` (append-only) |
| P2-T1-04 | `quotations`, `quotation_items` with `converted_order_id` |
| P2-T1-05 | Indexes `(customer_id, order_date DESC)`, `(status)`, `(expected_delivery_date)`; unique `idempotency_key` |
| P2-T1-06 | Order numbering series `LGR/<FY>/<00001>` wired to `core/numbering.py` |

## T2 — Domain services

| Id | Task |
|---|---|
| P2-T2-01 | Cart service: add/update/remove/clear; every item priced through P1's engine and stored as a snapshot |
| P2-T2-02 | **Order state machine** — explicit transition whitelist, rejected in the service layer, not the UI (BR-ORD-02) |
| P2-T2-03 | `orders/service.py::create_order()` — the full flow in `ARCHITECTURE` §3: idempotency → price → **credit gate hook** → persist → number → commit → emit `order.placed` |
| P2-T2-04 | Credit gate **interface** defined and called now, with a permissive stub. P3 drops in the real implementation without touching this code. |
| P2-T2-05 | Quotation service + convert-to-order (BR-ORD-05) |
| P2-T2-06 | Staff ordering on behalf of a dealer; `placed_by` + `channel` recorded (BR-ORD-08) |
| P2-T2-07 | Cancellation with mandatory reason; releases exposure (BR-ORD-09) |
| P2-T2-08 | Order confirmation PDF (WeasyPrint) — sq.ft breakdown visible per line, including `min_rule_applied` |
| P2-T2-09 | Domain events `order.placed`, `order.status_changed` emitted to Celery (**R10**) |

## T3 — API layer

| Id | Task |
|---|---|
| P2-T3-01 | Cart endpoints incl. `GET /cart/summary` returning totals **plus the credit evaluation** that drives the credit strip |
| P2-T3-02 | `POST /orders` with mandatory `Idempotency-Key`; 201 / 403 shapes exactly as in `API_SPEC` §4 |
| P2-T3-03 | `GET /orders`, `GET /orders/{uid}` — **dealer scope from the token**, cursor-paginated (BR-AC-07, **R13**) |
| P2-T3-04 | `POST /orders/{uid}/status`, `/cancel`, `/approve` — role-gated per transition |
| P2-T3-05 | `GET /orders/{uid}/pdf` |
| P2-T3-06 | Quotation endpoints |

## T4 — Frontend — **the order screen is the product**

| Id | Task |
|---|---|
| P2-T4-01 | Order entry layout: form left, **design preview + running bill right** |
| P2-T4-02 | Design No field: debounced lookup, instant image/name/category/rate, clear "not found" + search fallback (BR-CAT-07) |
| P2-T4-03 | L × B inputs accepting **feet + inches or decimal feet**; live `raw_sqft`; amber **"Min 11 sq.ft applied"** note showing both numbers (BR-SQFT-07) |
| P2-T4-04 | Live line amount, subtotal, GST, grand total — server-confirmed before submit (client arithmetic is never authoritative) |
| P2-T4-05 | **Credit strip** pinned at top: `Limit ₹X · Outstanding ₹Y · Available ₹Z`, green/amber/red, live as the cart grows (BR-CR-30…33) |
| P2-T4-06 | Red state the moment the cart crosses available credit, showing exactly how much over — **before** the dealer finishes typing the order |
| P2-T4-07 | **Keyboard-first entry**: Tab through Design → L → B → Qty → Enter adds the line and refocuses. Staff will do this hundreds of times a day. |
| P2-T4-08 | Repeat-last-order and duplicate-any-past-order in one tap (BR-ORD-11 usability) |
| P2-T4-09 | Bulk paste from Excel into the line grid |
| P2-T4-10 | Room-wise grouping of lines — presentational only (BR-ORD-10) |
| P2-T4-11 | Offline-tolerant draft autosave; a half-typed 40-line order survives a dropped network (BR-ORD-11) |
| P2-T4-12 | Blocked-order screen: unpaid invoice list, amount short, **Pay Now** (wired live in P4) |
| P2-T4-13 | Order list + detail (dealer and admin views), status timeline, PDF download |
| P2-T4-14 | Staff "order on behalf of" customer picker |
| P2-T4-15 | Full mobile-responsive pass at 360 px on the order screen specifically |

## T6 — Tests

| Id | Task |
|---|---|
| P2-T6-01 | Order creation: totals match `quote-cart` to the paise (**BR-PR-11**) |
| P2-T6-02 | Idempotency: same key twice → one order, identical response (**R6**) |
| P2-T6-03 | State machine: every legal transition passes, every illegal one is rejected |
| P2-T6-04 | Snapshot immutability: change the rate card, re-read the order → **unchanged** (**R9**) |
| P2-T6-05 | Dealer scoping: dealer A cannot read, list, or reference dealer B's orders (BR-AC-07) |
| P2-T6-06 | Quotation → order conversion carries prices correctly |
| P2-T6-07 | Playwright E2E: login → design lookup → 3 lines incl. a min-sq.ft line → place order → PDF |
| P2-T6-08 | Cancellation releases exposure |

---

## Verification

```powershell
cd backend; pytest -q tests/orders --cov=app/modules/orders --cov-report=term-missing
```
```powershell
cd frontend_new; npx playwright test
```

## Definition of Done — P2 exit gate

- [ ] A full order can be placed end to end by a dealer **and** by staff on their behalf
- [ ] Preview total, cart total, order total and PDF total are **identical to the paise**
- [ ] Min-11 rule visible to the dealer on every affected line, with both numbers shown
- [ ] Credit strip renders live and turns red before the order is submitted
- [ ] Idempotency proven under a double-tap
- [ ] Order snapshots immune to rate-card changes
- [ ] Keyboard-only entry of a 20-line order takes under 3 minutes (timed with real staff)
- [ ] Draft survives a network drop
- [ ] Dealer scoping proven — no cross-dealer data leak
- [ ] Order screen fully usable at 360 px
- [ ] E2E test green in CI
