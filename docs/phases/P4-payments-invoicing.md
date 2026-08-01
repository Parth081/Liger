# P4 — Payments & Invoicing (Weeks 11–13)

**Goal:** money actually moves. Dealers pay online in one tap, cash is controlled by an admin confirmation gate, invoices are GST-compliant, and every rupee lands in the append-only ledger.

**Entry gate:** P3 Definition of Done fully green (ledger + credit gate live in shadow mode).
**Rules implemented:** BR-PAY-01…10, BR-LED-01…05, BR-TAX-01…07, BR-CR-47.
**Blocked by decisions:** DEC-09 (part-payment allowed?). **T-EXT:** Razorpay KYC must be approved — sandbox work can start earlier.

---

## T1 — Data & migrations

| Id | Task |
|---|---|
| P4-T1-01 | `invoices`, `invoice_items` with GST columns, `due_date`, indexes `(customer_id, due_date)`, `(status, due_date)` |
| P4-T1-02 | `credit_notes`, `credit_note_items` |
| P4-T1-03 | `payments` with `status ENUM initiated|pending_confirmation|confirmed|failed|reversed`, gateway columns, `slip_url`, `confirmed_by`, unique `idempotency_key` |
| P4-T1-04 | `payment_allocations` |
| P4-T1-05 | `gateway_events` (append-only) with unique `event_id` |
| P4-T1-06 | `payment_links` |
| P4-T1-07 | Invoice numbering series — gapless per financial year (BR-TAX-05) |

## T2 — Domain services

| Id | Task |
|---|---|
| P4-T2-01 | Invoice service: create from a delivered order, freeze line snapshots, compute `due_date = invoice_date + credit_days` (BR-CR-03), allocate a gapless number in-transaction |
| P4-T2-02 | GST: CGST/SGST vs IGST by place of supply, HSN per line, GSTIN validation, B2C handling (BR-TAX-01…04) |
| P4-T2-03 | Credit notes — the **only** way to reduce an issued invoice (BR-TAX-06) |
| P4-T2-04 | Razorpay adapter behind a `PaymentGateway` interface — swappable, no vendor types in business code |
| P4-T2-05 | `POST /payments/online/initiate` → gateway order; **nothing posts to the ledger here** |
| P4-T2-06 | **Webhook handler** — signature verify → store raw event → idempotent on `event_id` → post to ledger → emit `payment.confirmed` (BR-PAY-03/04). The only path by which an online payment becomes real. |
| P4-T2-07 | Offline payment entry (cash/cheque/NEFT/RTGS/IMPS/UPI) with reference + slip upload → `PENDING_CONFIRMATION` |
| P4-T2-08 | **Cash confirmation gate** — admin confirm posts to the ledger and triggers re-evaluation + auto-unblock (BR-PAY-05, BR-CR-47). Until confirmed it frees **zero** credit. |
| P4-T2-09 | Allocation: FIFO oldest-invoice-first by default, manual re-allocation, partial payments, on-account remainder (BR-PAY-06, DEC-09) |
| P4-T2-10 | Reversal: bounce/chargeback posts a reversing entry, re-ages the invoice, resumes the ladder at the correct step (BR-PAY-09) |
| P4-T2-11 | Payment links — expiring, invoice-referenced so allocation is automatic (BR-PAY-10) |
| P4-T2-12 | PDF generation: tax invoice, receipt, credit note, customer statement (BR-PAY-08, BR-LED-03) |
| P4-T2-13 | E-invoice (IRN/QR) and e-way bill integration points designed and stubbed, activated by a setting (BR-TAX-07) |
| P4-T2-14 | Settlement reconciliation job — gateway settlements vs. recorded payments, daily |

## T3 — API layer

| Id | Task |
|---|---|
| P4-T3-01 | `POST /payments/online/initiate` (idempotent) |
| P4-T3-02 | `POST /webhooks/razorpay` — public + signature verified, idempotent, always 200 on duplicates |
| P4-T3-03 | `POST /payments/offline`, `/{uid}/confirm`, `/{uid}/reject`, `/{uid}/reverse` |
| P4-T3-04 | `GET /payments?status=pending_confirmation` — the admin cash queue |
| P4-T3-05 | `POST /payments/{uid}/allocations`; `GET /payments/{uid}/receipt.pdf` |
| P4-T3-06 | `POST /payment-links` |
| P4-T3-07 | Invoice endpoints + `GET /invoices/{uid}/pdf`; `POST /credit-notes` |
| P4-T3-08 | `GET /customers/{uid}/ledger`, `GET /customers/{uid}/statement.pdf` |

## T4 — Frontend

| Id | Task |
|---|---|
| P4-T4-01 | **Dealer Pay Now**: outstanding summary, select invoices or pay full, part-payment (DEC-09), Razorpay checkout, success/failure states |
| P4-T4-02 | Dealer payment history + downloadable receipts |
| P4-T4-03 | Dealer ledger/statement view with date range and PDF export |
| P4-T4-04 | **Admin cash queue** — pending confirmations with slip photo, confirm/reject with reason. Show the credit impact of confirming *before* the click. |
| P4-T4-05 | Offline payment entry form for accounts staff |
| P4-T4-06 | Allocation UI — auto FIFO shown, manual re-allocation allowed |
| P4-T4-07 | Invoice list/detail/PDF; credit note creation |
| P4-T4-08 | Payment-link generator with WhatsApp send (queued; delivery lands in P5) |
| P4-T4-09 | Wire the P2 blocked-order screen's **Pay Now** button to the live flow — the loop from "blocked" to "paid" to "unblocked" now closes |

## T6 — Tests

| Id | Task |
|---|---|
| P4-T6-01 | Webhook idempotency: the same `event_id` five times → one ledger entry |
| P4-T6-02 | Webhook signature rejection; replayed and out-of-order events are safe |
| P4-T6-03 | **Cash gate: an unconfirmed cash payment frees zero credit**; confirming it unblocks the customer |
| P4-T6-04 | FIFO allocation across three invoices, including a partial payment and an on-account remainder |
| P4-T6-05 | Reversal re-ages the invoice and resumes the ladder at the correct step |
| P4-T6-06 | Invoice numbering is gapless under 50 concurrent creations |
| P4-T6-07 | GST: intra-state, inter-state, B2C, mixed rates in one invoice |
| P4-T6-08 | Ledger balance after each entry is correct across a mixed 200-transaction sequence |
| P4-T6-09 | E2E: blocked dealer → pays online → webhook → auto-unblock → places an order successfully |
| P4-T6-10 | Credit note reduces outstanding correctly and never edits the original invoice |

---

## Verification

```powershell
cd backend; pytest -q tests/payments tests/invoicing --cov=app/modules/payments --cov=app/modules/invoicing --cov-report=term-missing
```

## Definition of Done — P4 exit gate

- [ ] DEC-09 signed off
- [ ] 100% branch coverage on `modules/payments/` and `modules/invoicing/`
- [ ] Online payment posts to the ledger **only** via a verified webhook
- [ ] Webhook proven idempotent and replay-safe
- [ ] Cash frees no credit until an admin confirms; confirming unblocks within seconds
- [ ] FIFO allocation correct, manual re-allocation available, partial payments work
- [ ] Invoice numbering gapless under concurrency
- [ ] GST correct for intra-state, inter-state and B2C
- [ ] Statement reconstructs purely from ledger entries and matches the derived outstanding
- [ ] Nightly gateway-settlement reconciliation runs clean
- [ ] **The full loop works end to end: blocked → pay → unblocked → order placed**
