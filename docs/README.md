# LIGER — Documentation Index

Start at [`../CLAUDE.md`](../CLAUDE.md). Everything else hangs off it.

## Core specifications

| Doc | What it is | Read when |
|---|---|---|
| [`../CLAUDE.md`](../CLAUDE.md) | **Entrypoint.** Project overview, the 14 non-negotiable rules, stack, conventions, execution protocol | Always, first |
| [`BUSINESS_RULES.md`](BUSINESS_RULES.md) | **Source of truth for behaviour.** Every rule has a `BR-` id | Before implementing any rule |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Module layout, request flows, jobs, security, scaling | Designing a module or integration |
| [`DATA_MODEL.md`](DATA_MODEL.md) | Every table, column and index | Writing a migration or model |
| [`API_SPEC.md`](API_SPEC.md) | Every endpoint, payload and error code | Adding or consuming an endpoint |
| [`EXECUTION.md`](EXECUTION.md) | Phase graph, parallel tracks, conflict map, Definition of Done | Starting a phase |
| [`PRODUCTION_PLAN.md`](PRODUCTION_PLAN.md) | Business plan, roadmap, risks, owner inputs needed | Overall context |

**Precedence when documents disagree:** `BUSINESS_RULES.md` > `DATA_MODEL.md` / `API_SPEC.md` > `ARCHITECTURE.md` > `PRODUCTION_PLAN.md`. Fix the spec first, then the code.

## Build phases

| Phase | Weeks | Focus |
|---|---|---|
| [P0 — Foundations](phases/P0-foundations.md) | 1–2 | Money in paise, RBAC, auth, settings, audit, CI, workers |
| [P1 — Catalogue & Pricing](phases/P1-catalogue-pricing.md) | 3–4 | Designs, images, rate cards, **sq.ft + min-11 engine**, GST |
| [P2 — Order Taking](phases/P2-order-taking.md) | 5–7 | The L × B order screen, cart, order lifecycle |
| [P3 — Credit Engine](phases/P3-credit-engine.md) | 8–10 | Ledger, ageing, limits, block ladder, scoring, **shadow mode** |
| [P4 — Payments & Invoicing](phases/P4-payments-invoicing.md) | 11–13 | Razorpay, webhooks, cash gate, allocation, GST invoices |
| [P5 — Notifications](phases/P5-notifications.md) | 14–15 | WhatsApp + SMS, templates in 3 languages, live ladder |
| [P6 — Fulfilment & Follow-up](phases/P6-fulfilment-followup.md) | 16–17 | Production, dispatch, delivery, follow-up tasks |
| [P7 — Analytics & Insights](phases/P7-analytics-insights.md) | 18–20 | Dashboard, region/distributor roll-ups, customer 360, digests |
| [P8 — Hardening & Migration](phases/P8-hardening-migration.md) | 21–23 | Data import, reconciliation, load test, security, parallel run |
| [P9 — Go-Live](phases/P9-go-live.md) | 24 | Phased rollout, **shadow → enforce switch**, hypercare |
| [P10 — Mobile App](phases/P10-mobile-app.md) | 25–32 | React Native on the same API, Android first |

## Running a phase

```
/build-phase P1
```

The command reads the rules, checks the entry gate, executes the phase's tracks, verifies, and reports against the Definition of Done.

## Three things that must not be forgotten

1. **T-EXT starts on day 1 of P0.** WhatsApp Business API, SMS DLT registration and Razorpay KYC take weeks of external approval. Nothing in the code can speed them up, and P5 cannot complete without them.
2. **Credit enforcement ships in shadow mode** (BR-CR-40) and is switched on only in P9, by the owner, after reviewing real decisions. Enabling it on day one will stop real business.
3. **The parallel run in P8 is not optional.** The system and the existing books run side by side until the numbers agree. That is what earns the right to switch off the old way.
