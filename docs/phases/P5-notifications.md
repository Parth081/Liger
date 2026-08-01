# P5 — Notifications & Follow-up (Weeks 14–15)

**Goal:** the system chases money by itself. WhatsApp and SMS on every order and credit event, to both the dealer and the admin, with the escalation ladder from P3 now actually sending.

**Entry gate:** P3 (ladder + triggers) and P4 (pay links, receipts) Definition of Done green.
**Rules implemented:** BR-NOT-01…10, BR-CR-41…47.
**Blocked by:** T-EXT — WhatsApp Business API approved, DLT templates registered. **If these are not done, this phase cannot complete.** That is why T-EXT starts on day 1 of P0.
**Blocked by decisions:** DEC-10 (languages).

---

## T1 — Data & migrations

| Id | Task |
|---|---|
| P5-T1-01 | `notification_templates` — unique `(key, channel, language)`, provider template id, approval status |
| P5-T1-02 | `notifications` — status lifecycle, provider message id, attempts, error, timestamps, **unique `dedupe_key`** |
| P5-T1-03 | `notification_preferences` — channels, language, quiet hours, marketing opt-out |
| P5-T1-04 | `customer_contacts.whatsapp_opt_in` + `opt_in_at` (BR-NOT-09) |
| P5-T1-05 | Seed settings: `quiet_hours=21:00-08:00`, `max_msgs_per_customer_per_day=4`, admin recipient numbers per event type |

## T2 — Domain services

| Id | Task |
|---|---|
| P5-T2-01 | `NotificationProvider` adapter interface; WhatsApp implementation (chosen vendor) — **no vendor types outside the adapter** (BR-NOT-10) |
| P5-T2-02 | SMS provider adapter with DLT template ids |
| P5-T2-03 | Email adapter for documents; in-app bell store |
| P5-T2-04 | Template engine: variable substitution, language selection from the customer record, media attachment (design image, invoice/receipt PDF), buttons (Pay Now) |
| P5-T2-05 | Dispatch service: quiet hours, per-customer daily cap, de-duplication by `dedupe_key`, opt-out honoured for marketing but never for transactional notices (BR-NOT-05/06/09) |
| P5-T2-06 | Channel fallback: WhatsApp fails → SMS → email, recorded per attempt |
| P5-T2-07 | Delivery-status webhook handler (sent/delivered/read/failed) |
| P5-T2-08 | Admin fan-out: configurable admin recipients per event type (BR-NOT-03) |

## T3 — Templates *(can be authored from P1 onward — start early, approval takes time)*

All in **English, Hindi, Gujarati** (DEC-10):

| Key | Trigger | Contents |
|---|---|---|
| `order.placed` | BR-ORD-03 | order no, items count, total, expected delivery |
| `order.confirmed` | BR-ORD-03 | + PDF attached |
| `order.in_production` / `.ready` / `.dispatched` | BR-ORD-03 | status + LR no where applicable |
| `order.delivered` | BR-CR-46 | + payment reminder if unpaid |
| `invoice.issued` | BR-TAX-05 | amount, due date, PDF, pay link |
| `credit.pre_due` | BR-CR-41 (−3) | amount, due date, pay link |
| `credit.due_today` | BR-CR-42 (0) | " |
| `credit.warn1` | BR-CR-43 (+3) | "clear due to keep ordering" + pay link |
| `credit.warn2_final` | BR-CR-44 (+10) | "account will be blocked in 5 days" + pay link |
| `credit.blocked` | BR-CR-45 (+15) | outstanding, invoice list, pay link |
| `credit.unblocked` | BR-CR-47 | confirmation + available credit |
| `credit.limit_warning` | BR-CR-14 | 80% utilisation reached |
| `credit.order_exceeds_limit` | BR-CR-13 | shortfall + pay link |
| `payment.received` | BR-PAY-08 | amount, receipt PDF, new outstanding |
| `payment.cash_pending` | BR-PAY-05 | **to admin** — cash awaiting confirmation |
| `admin.daily_digest` | BR-AN-07 | yesterday's orders, collections, new blocks |

## T5 — Workers

| Id | Task |
|---|---|
| P5-T5-01 | `notifications` queue with exponential backoff, max attempts, dead-letter queue (BR-NOT-02) |
| P5-T5-02 | **Replace the P3 ladder stubs with real sends** — each step at most once per invoice, idempotent on re-run (BR-CR-49) |
| P5-T5-03 | Quiet-hours deferral queue; block notices exempt |
| P5-T5-04 | Hourly retry sweep for failed sends |
| P5-T5-05 | Delivery-status ingestion and reconciliation |
| P5-T5-06 | Owner daily digest at 08:00 IST |

## T4 — Frontend

| Id | Task |
|---|---|
| P5-T4-01 | Admin notifications log: filter by customer/channel/status, view payload, resend |
| P5-T4-02 | Template manager: view, edit, preview in each language, **test-send** before go-live (BR-NOT-08) |
| P5-T4-03 | Per-customer message timeline on the customer 360 view |
| P5-T4-04 | Dealer notification preferences (channels, language, quiet hours) |
| P5-T4-05 | Admin recipient configuration per event type |
| P5-T4-06 | In-app notification bell |

## T6 — Tests

| Id | Task |
|---|---|
| P5-T6-01 | Every template renders in all 3 languages with all variables populated — no `{{unresolved}}` |
| P5-T6-02 | De-duplication: the same ladder step twice → one message |
| P5-T6-03 | Quiet hours defer non-critical messages; block notices still send |
| P5-T6-04 | Daily cap enforced per customer |
| P5-T6-05 | Provider failure → retry → fallback channel → dead letter, all recorded |
| P5-T6-06 | Admin fan-out reaches every configured recipient for that event type |
| P5-T6-07 | 60-day ladder simulation produces exactly the expected message sequence |
| P5-T6-08 | Opt-out suppresses marketing but never transactional notices |

---

## Verification

```powershell
cd backend; pytest -q tests/notifications --cov=app/modules/notifications --cov-report=term-missing
```

## Definition of Done — P5 exit gate

- [ ] WhatsApp Business API live; all templates approved by the provider
- [ ] DLT registration complete; SMS fallback working
- [ ] DEC-10 confirmed; all templates exist in every chosen language
- [ ] The P3 escalation ladder sends real messages, exactly once per step per invoice
- [ ] Both dealer and admin numbers receive their configured events
- [ ] Delivery status (sent/delivered/read/failed) visible per message in admin
- [ ] Quiet hours, daily cap and de-duplication all proven by test
- [ ] Provider failure degrades gracefully — **orders still work when WhatsApp is down**
- [ ] Test-send works before any template goes live
- [ ] Opt-in consent recorded per customer with timestamp and source
