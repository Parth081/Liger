# LIGER — API Specification (`/api/v1`)

> One API serves the dealer web app, the admin console, and the future mobile app (**R4**).
> Update this document in the same commit as any endpoint change.

## Conventions

- Base: `/api/v1`. Breaking changes require `/api/v2` — never a silent change.
- **Resources are addressed by `uid` (UUID)**, never by internal `id`.
- **Money is sent and received as integer paise**, in fields ending `_paise` (**R1**). The client formats.
- Auth: `Authorization: Bearer <access_token>`. Web also accepts the httpOnly cookie.
- Mutating money endpoints require `Idempotency-Key: <uuid>` (**R6**). Replay returns the original response.
- Lists are **cursor-paginated** (**R13**): `?limit=50&cursor=<opaque>` → `{ items: [], next_cursor: "…" }`. `limit` capped at 100.
- Filtering/sorting via explicit query params only. No arbitrary query languages.
- All timestamps ISO-8601 UTC (`2026-08-01T10:15:00Z`).

**Error envelope** — every non-2xx:
```json
{ "error": { "code": "CREDIT_BLOCKED", "message": "Please clear your outstanding of ₹1,25,000 to place a new order.",
             "details": { "outstanding_paise": 12500000, "overdue_invoices": [] }, "request_id": "req_01H…" } }
```
Codes: `VALIDATION_ERROR` `UNAUTHORIZED` `FORBIDDEN` `NOT_FOUND` `CONFLICT` `IDEMPOTENT_REPLAY` `CREDIT_BLOCKED` `CREDIT_LIMIT_EXCEEDED` `DESIGN_NOT_FOUND` `RATE_NOT_FOUND` `INVALID_TRANSITION` `RATE_LIMITED` `PAYMENT_FAILED` `INTERNAL_ERROR`.

Every endpoint below lists **`[roles]`** — enforced server-side (**R11**, BR-AC-08). Dealer scope always comes from the token, never the request body.

---

## 1. Auth

| Method | Path | Roles | Notes |
|---|---|---|---|
| `POST` | `/auth/staff/login` | public | email + password → 202 if 2FA required |
| `POST` | `/auth/staff/2fa` | public | TOTP → tokens |
| `POST` | `/auth/otp/request` | public | `{phone}` — rate-limited 5/hr (BR-AC-09) |
| `POST` | `/auth/otp/verify` | public | `{phone, code}` → tokens |
| `POST` | `/auth/refresh` | any | rotating refresh |
| `POST` | `/auth/logout` | any | revokes refresh token |
| `GET` | `/auth/me` | any | identity + role + permissions + customer scope |

---

## 2. Catalogue

| Method | Path | Roles | Notes |
|---|---|---|---|
| `GET` | `/designs` | all | `?q=&category=&status=&cursor=` — trigram search (BR-CAT-06) |
| `GET` | `/designs/{design_no}` | all | **Order-form lookup.** Returns design + images + resolved `rate_paise` (BR-PR-01) + `gst_pct` + `hsn_code`. p95 < 300 ms. |
| `POST` | `/designs` | super_admin, admin | |
| `PATCH` | `/designs/{uid}` | super_admin, admin | |
| `POST` | `/designs/{uid}/images` | super_admin, admin | multipart → S3, generates 3 variants (BR-CAT-05) |
| `GET` | `/categories` | all | |
| `GET` | `/accessories` | all | BR-CAT-08 |
| `POST` | `/imports/designs` | super_admin | `?dry_run=true` first (BR-CAT-09) |

---

## 3. Pricing

| Method | Path | Roles | Notes |
|---|---|---|---|
| `POST` | `/pricing/calculate-line` | all | **Live preview.** Same engine as cart/order (BR-SQFT-11). |
| `POST` | `/pricing/quote-cart` | all | prices a whole cart incl. tax; no persistence, no credit check |
| `GET` | `/rate-cards` | super_admin, admin, accounts | |
| `POST` | `/rate-cards` | super_admin | creates a draft version |
| `POST` | `/rate-cards/{uid}/publish` | super_admin | sets `effective_from` (BR-PR-03) |
| `GET/POST/DELETE` | `/customers/{uid}/special-rates` | super_admin, admin | BR-PR-04 |

**`POST /pricing/calculate-line`**
```json
{ "design_no": "LGR-2201", "length_ft": 3, "length_in": 0, "breadth_ft": 3, "breadth_in": 0, "quantity": 4 }
```
```json
{ "design_no": "LGR-2201", "design_name": "Zebra Blind — Ivory",
  "raw_sqft": 9.00, "billable_sqft": 11.00, "min_rule_applied": true, "line_area": 44.00,
  "rate_paise": 12500, "rate_source": "base",
  "goods_amount_paise": 550000, "making_charge_paise": 44000, "line_discount_paise": 0,
  "taxable_paise": 594000, "gst_pct": 12.0, "line_total_paise": 665280,
  "notes": ["Minimum 11 sq.ft applied (actual 9.00 sq.ft)"] }
```
`notes` is what the UI shows the dealer (BR-SQFT-07). `rate_source` ∈ `special` / `base` (DEC-03: no tiers).

---

## 4. Cart, quotations, orders

| Method | Path | Roles | Notes |
|---|---|---|---|
| `GET` | `/cart` | dealer, sales_rep, admin | `?customer_uid=` for staff ordering on behalf (BR-ORD-08) |
| `POST` | `/cart/items` | dealer, sales_rep, admin | prices and stores the snapshot |
| `PATCH/DELETE` | `/cart/items/{uid}` | " | |
| `DELETE` | `/cart` | " | |
| `GET` | `/cart/summary` | " | totals **+ live credit evaluation** — drives the credit strip (BR-CR-21) |
| `POST` | `/quotations` | dealer, sales_rep, admin | no credit check (BR-ORD-05) |
| `POST` | `/quotations/{uid}/convert` | " | runs the full gate |
| `POST` | `/orders` | dealer, sales_rep, admin | **`Idempotency-Key` required.** Full flow in ARCHITECTURE §3 |
| `GET` | `/orders` | scoped | `?status=&customer_uid=&from=&to=&region=&cursor=` — dealers see only their own (BR-AC-07) |
| `GET` | `/orders/{uid}` | scoped | full order + lines + status history + credit decision |
| `POST` | `/orders/{uid}/status` | role-gated per transition | validated whitelist (BR-ORD-02) |
| `POST` | `/orders/{uid}/cancel` | admin | mandatory reason; releases exposure (BR-ORD-09) |
| `POST` | `/orders/{uid}/approve` | admin | for `PENDING_APPROVAL` (BR-CR-13, BR-PR-07) |
| `GET` | `/orders/{uid}/pdf` | scoped | order confirmation |

**`POST /orders` → 201**
```json
{ "uid": "…", "order_no": "LGR/2026-27/00042", "status": "CONFIRMED",
  "grand_total_paise": 4520000,
  "credit_decision": { "decision": "WARN", "reasons": ["UTILISATION_ABOVE_80"],
    "effective_limit_paise": 50000000, "exposure_paise": 41200000, "available_paise": 8800000 } }
```

**`POST /orders` → 403 blocked** (BR-CR-10/11/13) — this response drives the dealer's block screen:
```json
{ "error": { "code": "CREDIT_BLOCKED",
  "message": "Please clear your outstanding of ₹1,25,000 to place a new order.",
  "details": { "outstanding_paise": 12500000, "available_paise": -2000000,
    "overdue_invoices": [ { "invoice_no": "LGR/INV/2026-27/0112", "amount_paise": 8500000,
                            "due_date": "2026-06-15", "days_overdue": 47 } ],
    "suggested_payment_paise": 8500000, "pay_link": "https://…" } } }
```

---

## 5. Credit

| Method | Path | Roles | Notes |
|---|---|---|---|
| `GET` | `/credit/customers/{uid}/status` | scoped | limit, exposure, available, colour state, ageing (BR-CR-30…33) |
| `POST` | `/credit/evaluate` | internal/staff | dry-run gate for a hypothetical cart |
| `GET` | `/credit/ageing` | admin, accounts | `?region=&bucket=&cursor=` — the ageing report |
| `GET` | `/credit/blocked` | admin, accounts | blocked customers + revenue frozen |
| `PATCH` | `/customers/{uid}/limit` | super_admin, admin | mandatory reason; audit-logged (BR-CR-51) |
| `POST` | `/customers/{uid}/block` | super_admin, admin | manual block (BR-CR-52) |
| `POST` | `/customers/{uid}/unblock` | super_admin, admin | mandatory reason |
| `POST` | `/customers/{uid}/overrides` | super_admin, admin | extra limit + expiry (BR-CR-50) |
| `POST` | `/credit/simulate` | super_admin, admin | "what if" for one customer or a rule change across all (BR-CR-53) |
| `GET` | `/customers/{uid}/credit-events` | admin, accounts | full audit trail |
| `GET` | `/customers/{uid}/score` | admin, accounts | score, band, factor breakdown, suggested limit (BR-SCR-06) |

---

## 6. Payments, invoices, ledger

| Method | Path | Roles | Notes |
|---|---|---|---|
| `POST` | `/payments/online/initiate` | dealer, accounts | → gateway order + checkout params. `Idempotency-Key` |
| `POST` | `/webhooks/razorpay` | public + signature | **the only path that posts an online payment to the ledger** (BR-PAY-03/04) |
| `POST` | `/payments/offline` | accounts, admin, sales_rep | cash/cheque/NEFT → `PENDING_CONFIRMATION` (BR-PAY-05) |
| `POST` | `/payments/{uid}/confirm` | accounts, admin | **the cash gate** — posts to ledger, re-evaluates block (BR-PAY-05, BR-CR-47) |
| `POST` | `/payments/{uid}/reject` | accounts, admin | reason required |
| `POST` | `/payments/{uid}/reverse` | admin | bounce/chargeback (BR-PAY-09) |
| `GET` | `/payments` | scoped | `?status=pending_confirmation` → admin's cash queue |
| `POST` | `/payments/{uid}/allocations` | accounts, admin | manual re-allocation (BR-PAY-06) |
| `GET` | `/payments/{uid}/receipt.pdf` | scoped | BR-PAY-08 |
| `POST` | `/payment-links` | accounts, admin | expiring link, sent on WhatsApp (BR-PAY-10) |
| `GET` | `/invoices` | scoped | `?status=&overdue=true&cursor=` |
| `GET` | `/invoices/{uid}` · `/invoices/{uid}/pdf` | scoped | |
| `POST` | `/invoices` | accounts, admin | from a delivered order |
| `POST` | `/credit-notes` | accounts, admin | BR-TAX-06 |
| `GET` | `/customers/{uid}/ledger` | scoped | `?from=&to=` — append-only entries (BR-LED-03) |
| `GET` | `/customers/{uid}/statement.pdf` | scoped | |

---

## 7. Customers, fulfilment, notifications

| Method | Path | Roles | Notes |
|---|---|---|---|
| `GET/POST/PATCH` | `/customers` · `/customers/{uid}` | admin, accounts, sales_rep(own) | |
| `GET` | `/customers/{uid}/360` | admin, accounts, sales_rep(own) | the insight card (BR-AN-05/06) |
| `GET/POST` | `/customers/{uid}/contacts` · `/addresses` | " | |
| `GET` | `/regions` | all | |
| `POST` | `/orders/{uid}/dispatch` | dispatch, admin | transporter, LR, vehicle |
| `POST` | `/orders/{uid}/deliver` | dispatch, admin | POD photo, received_by (BR-CR-46) |
| `GET` | `/notifications` | admin | `?customer_uid=&status=&channel=` — delivery log (BR-NOT-04) |
| `POST` | `/notifications/test-send` | super_admin | template preview before go-live (BR-NOT-08) |
| `POST` | `/notifications/{uid}/resend` | admin | |
| `GET/PATCH` | `/me/notification-preferences` | dealer | BR-NOT-05 |
| `POST` | `/webhooks/whatsapp` | public + signature | delivery/read receipts |

---

## 8. Analytics & admin

| Method | Path | Roles | Notes |
|---|---|---|---|
| `GET` | `/analytics/dashboard` | admin+ | BR-AN-01 |
| `GET` | `/analytics/sales` | admin+, sales_rep(own) | `?group_by=month,customer,region,distributor,category,design&from=&to=` (BR-AN-02) |
| `GET` | `/analytics/sales/drilldown` | " | the orders behind any figure (**BR-AN-04**) |
| `GET` | `/analytics/outstanding` · `/collections` · `/top-customers` · `/regions` · `/distributors` | admin+ | BR-AN-01/03 |
| `POST` | `/reports/export` | admin+ | async job → `report_run` (BR-AN-09) |
| `GET` | `/reports/runs/{uid}` | admin+ | status + download url |
| `GET/PATCH` | `/settings` | super_admin | audit-logged (**R8**) |
| `GET` | `/settings/history` | super_admin | |
| `GET/POST/PATCH` | `/users` · `/roles` | super_admin | |
| `GET` | `/audit-log` | super_admin | `?entity_type=&entity_id=&actor=&cursor=` |
| `GET/POST` | `/follow-ups` | admin, accounts, sales_rep(own) | task board |
| `POST` | `/imports/{type}` | super_admin | `customers` `designs` `opening_balances` `history` — `?dry_run=true` |
| `GET` | `/health` · `/health/deep` | public / internal | |

---

## 9. Rules that apply to every endpoint

1. **No business logic in routers.** Router = validate → call service → serialise.
2. **Dealer scoping comes from the token**, never a body/query parameter (BR-AC-07).
3. **Money in paise**, always, both directions (**R1**).
4. Lists are cursor-paginated and capped (**R13**).
5. Money mutations are idempotent (**R6**).
6. Every privileged action is audit-logged (BR-AC-08).
7. Responses never leak internal `id`s, stack traces, or provider payloads.
8. Every change here is mirrored in the generated TypeScript client — the frontend never hand-writes a request type.
