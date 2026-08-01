# P8 — Hardening & Data Migration (Weeks 21–23)

**Goal:** move Liger's real business into the system and prove the numbers match the owner's books, before anyone depends on it.

**Entry gate:** P0–P7 Definition of Done all green.
**Rules implemented:** BR-LED-05, BR-CR-40, plus every non-functional requirement in `ARCHITECTURE` §6, §7, §9, §10.

> This phase is where projects like this succeed or fail. The software being correct is not the same as the *data* being correct. Budget the full three weeks.

---

## T2 — Data migration *(importer scripts can be written from P1 onward)*

| Id | Task |
|---|---|
| P8-T2-01 | Customer importer: master data, region, distributor hierarchy, price tier, contacts, GSTIN |
| P8-T2-02 | **Opening balance importer** — posts `opening` ledger entries per customer (BR-LED-05) |
| P8-T2-03 | Design catalogue importer with images (if not already done in P1) |
| P8-T2-04 | Historical orders importer — **minimum 12 months**, so scoring and analytics are meaningful on day one |
| P8-T2-05 | Open invoices importer with correct original invoice dates, so ageing is real from the first day |
| P8-T2-06 | Historical payments + allocations importer |
| P8-T2-07 | Rebuild `ledger_entries` from the imported documents; verify `balance_after` chains correctly |
| P8-T2-08 | Every importer supports **dry-run → validation report → confirm**, and is re-runnable without duplicating |
| P8-T2-09 | **Reconciliation report** — per customer: system outstanding vs. the owner's book figure, with variances listed |
| P8-T2-10 | **Per-customer sign-off workflow.** A customer is not enforcement-eligible until their balance is signed off. |
| P8-T2-11 | Generate the first `credit_snapshots` and `customer_scores` from imported history |

## T6 — Testing & quality

| Id | Task |
|---|---|
| P8-T6-01 | Load test at **10× expected peak**: order placement, dashboard, catalogue search. Record p95 and the breaking point. |
| P8-T6-02 | Concurrency soak: sustained parallel order placement against shared credit limits (BR-CR-20 under real load) |
| P8-T6-03 | Full E2E regression across every role and every critical journey |
| P8-T6-04 | **Golden dataset regression** — historical orders still reproduce to the paise |
| P8-T6-05 | Chaos checks: WhatsApp down, gateway down, Redis down, worker backlog — verify graceful degradation, orders keep working |
| P8-T6-06 | Failover and restore drill: **restore production backup into staging and verify integrity** |
| P8-T6-07 | Accessibility pass (keyboard, contrast, labels) on dealer and admin flows |
| P8-T6-08 | Cross-browser + real-device testing on low-end Android, which is what dealers actually use |

## T5 — Security & compliance

| Id | Task |
|---|---|
| P8-T5-01 | Full security review against `ARCHITECTURE` §6; fix every finding before go-live |
| P8-T5-02 | Penetration test of auth, OTP, payment and webhook endpoints |
| P8-T5-03 | Verify dealer scoping exhaustively — **attempt cross-dealer access on every endpoint** |
| P8-T5-04 | Rate limiting verified under abuse conditions |
| P8-T5-05 | Secrets audit: nothing in git, everything in the secret manager, all exposed credentials rotated (**R14**) |
| P8-T5-06 | GST invoice format validated by the owner's CA |
| P8-T5-07 | DPDP: privacy policy, consent records, data-deletion process documented |
| P8-T5-08 | Backup + PITR verified; **restore drill performed and timed** |

## T5b — Operations readiness

| Id | Task |
|---|---|
| P8-T5-09 | Monitoring dashboards: API latency, error rate, queue depth, notification failures, webhook lag, nightly job status |
| P8-T5-10 | **Business alerts** to the owner's phone: auto-block fired, ledger drift, gateway silent 30 min, nightly credit job failed |
| P8-T5-11 | Runbooks: gateway outage, WhatsApp outage, stuck queue, failed migration, ledger drift, rollback |
| P8-T5-12 | On-call and escalation path defined for the hypercare period |

## T4 — Training & documentation

| Id | Task |
|---|---|
| P8-T4-01 | Staff training: order entry, cash confirmation, credit centre, follow-ups |
| P8-T4-02 | Dealer-facing one-page guide in Hindi/Gujarati, plus a short WhatsApp-shareable video |
| P8-T4-03 | Admin manual: settings, limits, overrides, rate cards, reports |
| P8-T4-04 | Support process and issue-reporting channel for the rollout |

## T-CRITICAL — Parallel run

| Id | Task |
|---|---|
| P8-TC-01 | **Run the system alongside the existing offline books for 2–4 weeks.** Every order entered in both. |
| P8-TC-02 | Daily comparison of totals, outstanding, and per-customer balances; investigate every variance |
| P8-TC-03 | **Shadow-mode credit review** — the owner reviews every order the engine would have blocked (BR-CR-40) and confirms the rules behave correctly against real dealers |
| P8-TC-04 | Tune limits, credit days and ladder timings per dealer based on what shadow mode revealed |
| P8-TC-05 | Owner sign-off that the system's numbers match the books |

---

## Verification

```powershell
cd backend; pytest -q --cov=app --cov-report=term-missing
```
```powershell
cd frontend_new; npx playwright test
```

## Definition of Done — P8 exit gate

- [ ] All customers, designs, opening balances, 12 months of history, and open invoices imported
- [ ] **Reconciliation report shows zero unexplained variance**; every customer balance signed off by the owner
- [ ] Golden dataset still reproduces historical orders to the paise
- [ ] Load test passed at 10× peak; p95 within target
- [ ] Concurrency holds under sustained load
- [ ] Security review and pen test findings all closed
- [ ] Cross-dealer access attempts fail on every endpoint
- [ ] Backup restored into staging successfully and timed
- [ ] Monitoring and business alerts live and firing to the owner's phone
- [ ] Runbooks written; on-call defined
- [ ] Staff trained; dealer guide distributed
- [ ] **Parallel run complete with matching numbers, and shadow-mode credit decisions reviewed and approved by the owner**
