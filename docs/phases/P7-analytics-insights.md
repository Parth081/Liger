# P7 — Analytics & Insights (Weeks 18–20)

**Goal:** the owner opens one screen and knows the business — this month's sales, who owes what, which region and which distributor is performing, and which dealer needs a call today.

**Entry gate:** P2 (orders) and P4 (payments/invoices) Definition of Done green. Meaningful output also needs the historical import from P8-T2 — run the importer early so this phase has real data to aggregate.
**Rules implemented:** BR-AN-01…09, BR-SCR-01…07.

---

## T1 — Data & reporting schema

| Id | Task |
|---|---|
| P7-T1-01 | Materialized view `mv_sales_daily` — date × customer × region × distributor × category × design → orders, qty, sq.ft, value |
| P7-T1-02 | Materialized view `mv_outstanding` — customer → outstanding, overdue, ageing buckets, colour state |
| P7-T1-03 | Materialized view `mv_customer_summary` — 12-month trend, AOV, order frequency, last order/payment |
| P7-T1-04 | Nightly reporting tables: `rpt_sales_monthly`, `rpt_collection_efficiency`, `rpt_ageing`, `rpt_design_performance` |
| P7-T1-05 | Distributor hierarchy roll-up support (`customers.distributor_id` self-FK) with recursive CTE (BR-AN-03) |
| P7-T1-06 | `saved_reports`, `report_runs` for async exports |
| P7-T1-07 | Supporting indexes; verify no dashboard query touches a live transactional table under load (BR-AN-08) |

## T2 — Domain services

| Id | Task |
|---|---|
| P7-T2-01 | Dashboard service: MTD/QTD/YTD, month-over-month, year-over-year, outstanding, overdue, ageing pyramid, collection efficiency, DSO, revenue frozen behind blocked customers (BR-AN-01) |
| P7-T2-02 | Sales query service: `group_by` any combination of month/customer/region/state/distributor/rep/design/category/product type (BR-AN-02) |
| P7-T2-03 | **Drill-down service — every aggregate resolves to the individual orders behind it (BR-AN-04).** A figure that cannot be drilled into must not ship. |
| P7-T2-04 | Region and distributor roll-up reporting (BR-AN-03) |
| P7-T2-05 | Customer 360 / insight card: trend, score + band with plain-language reasons, outstanding, overdue, available credit, last payment, favourite designs, AOV, frequency (BR-AN-05) |
| P7-T2-06 | **Insight nudge generator** (BR-AN-06): dormancy vs. own pattern ("ordered monthly for 2 years, nothing in 47 days"), repeated limit pressure, consistently early payer → safe to raise limit, declining trend, single-design concentration risk |
| P7-T2-07 | Digest builders: daily owner summary, weekly business review, monthly dealer statements (BR-AN-07) |
| P7-T2-08 | Export service: Excel/CSV/PDF, async for large sets, notify on completion (BR-AN-09) |
| P7-T2-09 | Sales-rep scoping — a rep sees only their own customers' analytics (BR-AC-04) |

## T5 — Workers

| Id | Task |
|---|---|
| P7-T5-01 | Refresh materialized views every 15 minutes (concurrent refresh, no read blocking) |
| P7-T5-02 | Nightly 03:30 rebuild of reporting tables |
| P7-T5-03 | 08:00 owner daily digest via WhatsApp + email |
| P7-T5-04 | Monday 08:00 weekly business review |
| P7-T5-05 | 1st of month 08:00 — statements of account to every dealer |
| P7-T5-06 | Nightly insight-nudge generation → follow-up tasks |
| P7-T5-07 | Async export runner on the `reports` queue |

## T3 — API layer

| Id | Task |
|---|---|
| P7-T3-01 | `GET /analytics/dashboard` |
| P7-T3-02 | `GET /analytics/sales?group_by=&from=&to=&filters…` |
| P7-T3-03 | `GET /analytics/sales/drilldown` |
| P7-T3-04 | `GET /analytics/outstanding`, `/collections`, `/top-customers`, `/regions`, `/distributors` |
| P7-T3-05 | `GET /customers/{uid}/360` |
| P7-T3-06 | `POST /reports/export`, `GET /reports/runs/{uid}` |

## T4 — Frontend

| Id | Task |
|---|---|
| P7-T4-01 | **Owner dashboard** — sales this month vs. last vs. same month last year; outstanding and overdue as the headline number; ageing pyramid; collection efficiency; DSO; blocked-revenue tile |
| P7-T4-02 | Every tile and every chart segment is **clickable through to the underlying orders** (BR-AN-04) |
| P7-T4-03 | Sales explorer: pivot-style group-by builder, date range, filters, chart + table, export |
| P7-T4-04 | Region view with state/city breakdown and per-region outstanding |
| P7-T4-05 | Distributor view with sub-dealer roll-up ("Distributor X: ₹Y across 12 dealers") |
| P7-T4-06 | Top customers / top designs / top categories leaderboards |
| P7-T4-07 | **Customer 360 page** — the insight card, with nudges shown as actionable items that create follow-up tasks |
| P7-T4-08 | Ageing report with drill-through to invoices and a one-click pay-link send |
| P7-T4-09 | Export UI with async job status |
| P7-T4-10 | Dashboard readable on a phone — the owner will check it from anywhere |

## T6 — Tests

| Id | Task |
|---|---|
| P7-T6-01 | Aggregates match a hand-computed fixture dataset exactly |
| P7-T6-02 | Drill-down totals equal the parent aggregate for every dimension |
| P7-T6-03 | Distributor roll-up sums sub-dealers correctly, including nested levels |
| P7-T6-04 | Sales-rep scoping — a rep cannot see another rep's customers in any report |
| P7-T6-05 | Cancelled orders and credit notes are excluded/deducted correctly everywhere |
| P7-T6-06 | Insight nudges fire on constructed scenarios (dormant dealer, limit-pressure dealer, early payer) |
| P7-T6-07 | Dashboard p95 < 1 s with 100k orders seeded |
| P7-T6-08 | Materialized-view refresh does not block reads |

---

## Verification

```powershell
cd backend; pytest -q tests/analytics --cov=app/modules/analytics --cov-report=term-missing
```

## Definition of Done — P7 exit gate

- [ ] Owner dashboard loads in under 1 s with 100k orders seeded
- [ ] **Every number on every screen drills down to the orders behind it**
- [ ] Sales sliceable by month, customer, region, state, distributor, rep, design, category — in any combination
- [ ] Distributor roll-up verified against hand-computed figures
- [ ] Customer 360 insight card live, with nudges creating follow-up tasks
- [ ] Daily, weekly and monthly digests delivering on schedule
- [ ] Excel/CSV/PDF exports working, async for large sets
- [ ] Sales-rep scoping proven — no cross-rep data leak
- [ ] No dashboard query reads a live transactional table under load
