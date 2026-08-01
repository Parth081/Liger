# P6 — Fulfilment & Follow-up (Weeks 16–17)

**Goal:** track an order from production to delivered hands, and keep the follow-up going right up to delivery — including for customers who are already blocked (BR-CR-46).

**Entry gate:** P2 (orders) and P5 (notifications) Definition of Done green.
**Rules implemented:** BR-ORD-01…03, BR-CR-44/46, BR-AN-06, BR-AC-05/06.

---

## T1 — Data & migrations

| Id | Task |
|---|---|
| P6-T1-01 | `production_jobs` — order, stage, started/completed, assigned_to, notes |
| P6-T1-02 | `dispatches` — transporter, LR no, vehicle no, docket url, dispatched_at/by |
| P6-T1-03 | `deliveries` — delivered_at, received_by, POD image url, remarks |
| P6-T1-04 | `follow_up_tasks` — customer, type, due date, assignee, status, outcome, notes, related ref |
| P6-T1-05 | Order fields: `expected_delivery_date`, `site_name`, `remarks`; partial-delivery support |

## T2 — Domain services

| Id | Task |
|---|---|
| P6-T2-01 | Production stage tracking wired into the order state machine (`CONFIRMED → IN_PRODUCTION → READY`) |
| P6-T2-02 | Dispatch service: LR/transport capture, docket upload, `DISPATCHED` transition |
| P6-T2-03 | Delivery service: POD photo, received_by, `DELIVERED`; partial delivery → `PARTIALLY_DELIVERED` |
| P6-T2-04 | **BR-CR-46 rule:** orders already `IN_PRODUCTION` or beyond continue to completion even when the customer becomes blocked. Blocking stops *new* orders; it never abandons work in progress. |
| P6-T2-05 | Follow-up task engine: auto-create on `credit.warn2` (BR-CR-44), on delivery of an unpaid order, and on a reorder-gap nudge (BR-AN-06) |
| P6-T2-06 | Task assignment to the customer's sales rep by default; reassignment allowed |
| P6-T2-07 | Delivery-time follow-up chain: on `DELIVERED` with an unpaid invoice, start a payment follow-up sequence |
| P6-T2-08 | Invoice auto-generation trigger on delivery (hands off to P4's invoice service) |

## T3 — API layer

| Id | Task |
|---|---|
| P6-T3-01 | `POST /orders/{uid}/status` extended for production stages — role-gated to `production` |
| P6-T3-02 | `POST /orders/{uid}/dispatch` — role-gated to `dispatch`/`admin` |
| P6-T3-03 | `POST /orders/{uid}/deliver` with POD upload |
| P6-T3-04 | `GET/POST/PATCH /follow-ups` — filter by assignee, type, due date, status |
| P6-T3-05 | `GET /orders?status=in_production|ready` — production and dispatch work queues |

## T4 — Frontend

| Id | Task |
|---|---|
| P6-T4-01 | **Production board** — confirmed orders with specs (design, sizes, sq.ft, room labels). **No money visible at all** (BR-AC-05). |
| P6-T4-02 | Dispatch screen: ready orders, transporter/LR/vehicle entry, docket upload |
| P6-T4-03 | Delivery confirmation with POD photo capture |
| P6-T4-04 | Dealer order tracking timeline with live status |
| P6-T4-05 | **Follow-up task board** — kanban by status, filter by assignee/type/overdue, log call outcome |
| P6-T4-06 | Delivery calendar / expected-delivery view for planning |
| P6-T4-07 | Blocked-but-in-production indicator so staff know work continues while collection is chased |

## T6 — Tests

| Id | Task |
|---|---|
| P6-T6-01 | Full lifecycle: confirmed → production → ready → dispatched → delivered, each transition audited and notified |
| P6-T6-02 | Illegal transitions rejected (e.g. confirmed → delivered) |
| P6-T6-03 | **BR-CR-46:** a customer blocked mid-production — the in-flight order still completes, new orders are refused |
| P6-T6-04 | Delivery of an unpaid order creates a follow-up task and starts the payment chase |
| P6-T6-05 | Production role cannot read any money field on any endpoint (BR-AC-05) |
| P6-T6-06 | Partial delivery leaves the order in `PARTIALLY_DELIVERED` with correct remaining quantities |

---

## Verification

```powershell
cd backend; pytest -q tests/fulfilment --cov=app/modules/fulfilment --cov-report=term-missing
```

## Definition of Done — P6 exit gate

- [ ] Full order lifecycle tracked end to end with notifications at every stage
- [ ] Production role provably cannot see money anywhere in the API
- [ ] BR-CR-46 verified — blocked customers' in-flight orders still complete
- [ ] POD photo capture works on a phone browser
- [ ] Follow-up tasks auto-create on final warning, on unpaid delivery, and on reorder gaps
- [ ] Task board usable by sales staff with call-outcome logging
- [ ] Delivery triggers invoice generation
