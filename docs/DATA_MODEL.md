# LIGER — Data Model

> PostgreSQL 16. Alembic is the only path to schema change (**R5**).
> **All money columns are `BIGINT`, in paise, and end with `_paise` (R1).**

## Global conventions

Every table (except the append-only ledger/audit tables noted) carries:

| Column | Type | Note |
|---|---|---|
| `id` | `BIGSERIAL PK` | internal |
| `uid` | `UUID UNIQUE DEFAULT gen_random_uuid()` | the only id exposed in APIs/URLs |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | UTC |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `created_by` / `updated_by` | `BIGINT → users.id` | nullable for system actions |
| `deleted_at` | `TIMESTAMPTZ NULL` | masters only — **never** on ledger, orders, invoices, audit |
| `version` | `INTEGER NOT NULL DEFAULT 1` | optimistic locking on mutable entities |

Rules: no `SELECT *`; every FK indexed; timestamps UTC; enums as Postgres `ENUM` types.

---

## 1. Identity & access

**`users`** — staff/admins
`name`, `email UNIQUE`, `phone`, `password_hash`, `role_id`, `totp_secret`, `is_2fa_enabled`, `is_active`, `last_login_at`, `failed_login_count`, `locked_until`

**`customer_users`** — dealer logins
`customer_id → customers`, `name`, `phone UNIQUE`, `email`, `is_primary`, `is_active`, `last_login_at`, `language`
*One customer may have several logins (owner, accountant, site person).*

**`roles`** — `code UNIQUE` (`super_admin|admin|accounts|sales_rep|production|dispatch`), `name`, `is_system`
**`permissions`** — `code UNIQUE` (`order.create`, `credit.override`, `payment.confirm_cash`, …), `description`
**`role_permissions`** — `role_id`, `permission_id`, PK both

**`otp_requests`** — `phone`, `code_hash`, `purpose`, `expires_at`, `attempts`, `consumed_at`, `ip`
Index `(phone, created_at)` for rate limiting (BR-AC-09).

**`refresh_tokens`** — `subject_type` (`user|customer_user`), `subject_id`, `token_hash`, `device`, `ip`, `expires_at`, `revoked_at`

**`audit_log`** *(append-only)* — `actor_type`, `actor_id`, `action`, `entity_type`, `entity_id`, `before JSONB`, `after JSONB`, `ip`, `user_agent`, `created_at`
Index `(entity_type, entity_id, created_at DESC)`.

---

## 2. Customers & geography

**`regions`** — `parent_id → regions` (self), `name`, `type` (`zone|state|city|territory`), `code`
**`customers`**

| Column | Type | Note |
|---|---|---|
| `code` | `VARCHAR UNIQUE` | Liger's dealer code |
| `business_name`, `legal_name` | | |
| `gstin`, `pan` | `VARCHAR` | GSTIN format-validated (BR-TAX-04) |
| `primary_phone` | `VARCHAR UNIQUE` | WhatsApp target |
| `email` | | |
| `region_id → regions`, `state`, `city`, `pincode` | | BR-AN-03 |
| `distributor_id → customers` | self-FK, nullable | dealer hierarchy, BR-AN-03 |
| `sales_rep_id → users` | | BR-AC-04 scoping |
| `credit_limit_paise` | `BIGINT` | base limit, BR-CR-05 |
| `credit_days` | `INTEGER DEFAULT 30` | DEC-06, BR-CR-03 |
| `cash_bonus_pct` | `NUMERIC(5,2) DEFAULT 10` | DEC-08, BR-CR-06 |
| `opening_balance_paise` | `BIGINT DEFAULT 0` | migrated from offline books, BR-LED-05 |
| `status` | `ENUM active|warned|red|blocked` | BR-CR-30…33 |
| `blocked_at`, `block_reason`, `unblocked_at`, `is_manual_block` | | BR-CR-52 |
| `language` | `ENUM en|hi|gu` | DEC-10 |
| `notes` | `TEXT` | |

Indexes: `(status)`, `(region_id)`, `(distributor_id)`, `(sales_rep_id)`, `lower(business_name)` trigram.

**`customer_addresses`** — `customer_id`, `type` (`billing|shipping`), address lines, `state`, `city`, `pincode`, `is_default`
**`customer_contacts`** — `customer_id`, `name`, `role` (`owner|accounts|site`), `phone`, `whatsapp_opt_in`, `opt_in_at` (BR-NOT-09)

---

## 3. Catalogue

**`categories`** — `parent_id` (self), `name`, `code`, `product_type`, `sort_order`, `is_active`
**`designs`**

| Column | Type | Note |
|---|---|---|
| `design_no` | `VARCHAR` | **`UNIQUE INDEX ON lower(design_no)`** (BR-CAT-01) |
| `name`, `category_id`, `collection`, `colour`, `composition`, `width_of_goods` | | |
| `hsn_code` | `VARCHAR` | BR-TAX-03 |
| `gst_pct` | `NUMERIC(5,2)` | DEC-04, BR-TAX-01 |
| `base_rate_paise` | `BIGINT` | per sq.ft, BR-PR-01(3) |
| `uom` | `ENUM sqft|piece|rft` | |
| `cover_image_id` | | |
| `status` | `ENUM active|discontinued|out_of_stock` | BR-CAT-04 |

**`design_images`** — `design_id`, `url`, `variants JSONB` (thumb/card/zoom), `sort_order`, `alt_text` (BR-CAT-05)
**`accessories`** — `code UNIQUE`, `name`, `uom` (`piece|rft`), `rate_paise`, `hsn_code`, `gst_pct`, `is_active` (BR-CAT-08)

---

## 4. Pricing

**`rate_cards`** — `version INTEGER UNIQUE`, `name`, `effective_from DATE`, `effective_to DATE NULL`, `status` (`draft|published|archived`), `published_by`, `published_at` (BR-PR-03)
**`rate_card_items`** — `rate_card_id`, `design_id`, `rate_paise`; `UNIQUE (rate_card_id, design_id)` *(DEC-03: no tiers — one rate per design for all customers)*
**`customer_special_rates`** — `customer_id`, `design_id`, `rate_paise`, `valid_from`, `valid_to`, `reason`, `approved_by`; partial unique index on active rows (BR-PR-04)
**`making_charges`** — `product_type`, `mode ENUM per_sqft|per_piece`, `amount_paise`, `is_active` (BR-PR-06, DEC-05)

---

## 5. Cart, quotations, orders

**`carts`** — `owner_type` (`customer_user|user`), `owner_id`, `customer_id`, `updated_at`
*Cart is always bound to an authenticated owner — no anonymous rows.*
**`cart_items`** — `cart_id`, `design_id`, `design_no`, `length_in NUMERIC(10,2)`, `breadth_in`, `quantity`, `room_label`, computed snapshot columns (same as order items)

**`quotations`** / **`quotation_items`** — mirror of orders, **no credit check** (BR-ORD-05); `converted_order_id` when converted.

**`orders`**

| Column | Type | Note |
|---|---|---|
| `order_no` | `VARCHAR UNIQUE` | `LGR/<FY>/<00001>` gapless (BR-ORD-06) |
| `customer_id`, `placed_by_type`, `placed_by_id`, `channel` | | BR-ORD-08 |
| `status` | `ENUM` | BR-ORD-01 |
| `order_date`, `expected_delivery_date` | | |
| `subtotal_paise`, `order_discount_paise`, `taxable_paise`, `cgst_paise`, `sgst_paise`, `igst_paise`, `freight_paise`, `packing_paise`, `round_off_paise`, `grand_total_paise` | `BIGINT` | BR-PR-09/10 |
| `credit_decision` | `JSONB` | frozen decision object (BR-CR-21) |
| `rate_card_version`, `rule_version` | | BR-SQFT-10, BR-PR-03 |
| `idempotency_key` | `VARCHAR UNIQUE` | R6, BR-ORD-07 |
| `site_name`, `remarks` | | |

Indexes: `(customer_id, order_date DESC)`, `(status)`, `(expected_delivery_date)`.

**`order_items`**

`order_id`, `design_id`, **`design_no` / `design_name` / `category` snapshots**, `length_in`, `breadth_in`, `quantity`, `room_label`,
`raw_sqft NUMERIC(10,2)`, `billable_sqft NUMERIC(10,2)`, `min_rule_applied BOOLEAN`, `line_area NUMERIC(12,2)`,
`rate_paise`, `rate_source ENUM special|base`, `making_charge_paise`, `line_discount_paise`,
`taxable_paise`, `gst_pct`, `cgst_paise`, `sgst_paise`, `igst_paise`, `line_total_paise`, `hsn_code`
*(BR-SQFT-10, BR-PR-02 — every line is a frozen snapshot and is never recalculated.)*

**`order_status_history`** *(append-only)* — `order_id`, `from_status`, `to_status`, `actor_type`, `actor_id`, `reason`, `created_at` (BR-ORD-03)

---

## 6. Money: invoices, payments, ledger

**`invoices`** — `invoice_no UNIQUE` (gapless per FY, BR-TAX-05), `customer_id`, `order_id`, `invoice_date`, **`due_date`** (BR-CR-03), `place_of_supply`, `customer_gstin`, tax + total `_paise` columns, `status ENUM draft|issued|part_paid|paid|cancelled`, `pdf_url`
Indexes: `(customer_id, due_date)`, `(status, due_date)` ← drives ageing and the ladder.

**`invoice_items`** — line snapshot mirroring `order_items` + `hsn_code`, `gst_pct`
**`credit_notes`** / **`credit_note_items`** — `credit_note_no`, `invoice_id`, `reason`, amounts (BR-TAX-06)

**`payments`**

| Column | Note |
|---|---|
| `customer_id`, `amount_paise` | |
| `method` | `ENUM cash|cheque|neft|rtgs|imps|upi|card|netbanking|wallet` |
| `status` | `ENUM initiated|pending_confirmation|confirmed|failed|reversed` (BR-PAY-05) |
| `gateway`, `gateway_payment_id`, `gateway_order_id`, `gateway_signature` | |
| `reference_no`, `slip_url` | offline payments (BR-PAY-02) |
| `confirmed_by → users`, `confirmed_at` | cash gate (BR-PAY-05) |
| `reversed_at`, `reversal_reason` | BR-PAY-09 |
| `idempotency_key UNIQUE` | R6 |

**`payment_allocations`** — `payment_id`, `invoice_id`, `amount_paise`, `is_auto` (BR-PAY-06). Σ allocations ≤ payment amount; remainder is "on account".

**`ledger_entries`** *(APPEND-ONLY — no UPDATE, no DELETE, ever — R2/BR-LED-01)*

`customer_id`, `entry_type ENUM opening|invoice|payment|credit_note|adjustment|reversal`,
`debit_paise`, `credit_paise`, `balance_after_paise`, `ref_type`, `ref_id`, `narration`, `posted_at`, `posted_by`
Index `(customer_id, posted_at)`. Statement for any range is reconstructable from this table alone (BR-LED-03).

**`gateway_events`** *(append-only)* — `provider`, `event_id UNIQUE`, `event_type`, `payload JSONB`, `signature_valid`, `processed_at`, `error` (BR-PAY-04)

**`payment_links`** — `customer_id`, `invoice_id`, `amount_paise`, `provider_link_id`, `short_url`, `expires_at`, `paid_at` (BR-PAY-10)

---

## 7. Credit

**`credit_snapshots`** *(daily, append-only)* — `customer_id`, `snapshot_date`, `outstanding_paise`, `overdue_paise`, `bucket_current/b1_30/b31_60/b61_90/b90_plus` `_paise`, `exposure_paise`, `effective_limit_paise`, `available_paise`, `status`, `score`; `UNIQUE (customer_id, snapshot_date)` (BR-CR-54)

**`credit_events`** *(append-only)* — `customer_id`, `event_type` (`warned|red|blocked|unblocked|limit_changed|override_granted|override_expired|shadow_decision`), `old_value JSONB`, `new_value JSONB`, `reason`, `actor_id`, `is_shadow BOOLEAN`, `created_at` (BR-CR-51, BR-CR-40)

**`credit_overrides`** — `customer_id`, `extra_limit_paise`, `valid_until`, `reason`, `granted_by`, `revoked_at` (BR-CR-50)

**`customer_scores`** — `customer_id`, `score NUMERIC(5,2)`, `band ENUM A_PLUS|A|B|C|D|NEW`, `factors JSONB`, `suggested_limit_paise`, `computed_at`; `UNIQUE (customer_id, computed_at::date)` (BR-SCR-01/06)

**`escalation_state`** — `invoice_id`, `last_step_sent` (`pre_due|due|warn1|warn2|blocked`), `last_sent_at`, `next_due_at`; `UNIQUE (invoice_id)` — makes the ladder idempotent (BR-CR-49)

---

## 8. Fulfilment

**`production_jobs`** — `order_id`, `stage`, `started_at`, `completed_at`, `assigned_to`, `notes`
**`dispatches`** — `order_id`, `transporter`, `lr_no`, `vehicle_no`, `docket_url`, `dispatched_at`, `dispatched_by`
**`deliveries`** — `order_id`, `delivered_at`, `received_by`, `pod_image_url`, `remarks` (BR-CR-46)

---

## 9. Notifications

**`notification_templates`** — `key`, `channel ENUM whatsapp|sms|email|push|in_app`, `language ENUM en|hi|gu`, `body`, `variables JSONB`, `provider_template_id`, `approval_status`; `UNIQUE (key, channel, language)` (BR-NOT-07/08)

**`notifications`** — `recipient_type` (`customer|user`), `recipient_id`, `phone`/`email`, `channel`, `template_key`, `language`, `payload JSONB`, `status ENUM queued|sent|delivered|read|failed`, `provider_message_id`, `attempts`, `last_error`, `queued_at`, `sent_at`, `delivered_at`, `read_at`, `ref_type`, `ref_id`, `dedupe_key UNIQUE` (BR-NOT-04/06)

**`notification_preferences`** — `customer_id`, `channels JSONB`, `language`, `quiet_hours_start/end`, `marketing_opt_out` (BR-NOT-05)

---

## 10. Operations

**`settings`** — `key UNIQUE`, `value`, `value_type`, `group`, `description`, `updated_by`, `updated_at` (**R8**)

Seeded keys: `min_billable_sqft=11.00`, `sqft_rounding_step=0.25`, `max_dimension_in=600`, `credit_days_default=30`, `hard_block_days=45`, `ladder_pre_due=-3`, `ladder_warn1=3`, `ladder_warn2=10`, `ladder_block=15`, `warn_utilisation_pct=80`, `cash_bonus_pct_default=10`, `cash_ratio_threshold=0.30`, `credit_enforcement_mode=shadow`, `max_rep_discount_pct=5`, `quiet_hours=21:00-08:00`, `max_msgs_per_customer_per_day=4`, `global_limit_ceiling`, score weights, `max_page_size=100`.

**`settings_history`** *(append-only)* — `key`, `old_value`, `new_value`, `changed_by`, `changed_at`
**`import_jobs`** — `type`, `file_url`, `status`, `rows_total/ok/failed`, `error_report_url`, `dry_run BOOLEAN`, `created_by` (BR-CAT-09, BR-LED-05)
**`follow_up_tasks`** — `customer_id`, `type` (`payment_chase|reorder_nudge|delivery_followup|limit_review`), `due_date`, `assignee_id`, `status`, `outcome`, `notes`, `related_ref` (BR-AN-06, BR-CR-44)
**`idempotency_keys`** — `key UNIQUE`, `endpoint`, `request_hash`, `response JSONB`, `status_code`, `created_at`, `expires_at` (**R6**)
**`saved_reports`** / **`report_runs`** — definition, params, schedule, output url, status (BR-AN-09)

---

## 11. Analytics objects

**Materialized views** (refreshed every 15 min — BR-AN-08):
- `mv_sales_daily` — date × customer × region × distributor × category → orders, qty, sqft, value
- `mv_outstanding` — customer → outstanding, overdue, ageing buckets, status
- `mv_customer_summary` — customer → 12-month trend, AOV, frequency, last order/payment

**Reporting tables** (nightly): `rpt_sales_monthly`, `rpt_collection_efficiency`, `rpt_ageing`, `rpt_design_performance`.

Partitioning trigger points (do when volume demands, design allows it now): `orders`, `ledger_entries`, `notifications`, `audit_log` by month.

---

## 12. Migration & seeding order (P0/P8)

1. `settings` + seeds → 2. `roles`/`permissions` + seeds → 3. `users` (super admin) → 4. `regions` →
5. `customers` (+ `opening_balance_paise` from the offline books) → 6. `categories`/`designs`/`design_images` →
7. `rate_cards` + items → 8. historical `orders`/`order_items` (12 months) → 9. `invoices` → 10. `payments` + `payment_allocations` →
11. **`ledger_entries` rebuilt from 8–10** → 12. reconciliation report vs. the owner's books → 13. first `credit_snapshots` + `customer_scores`

**Gate:** enforcement stays in `shadow` (BR-CR-40) until step 12 is signed off per customer.
