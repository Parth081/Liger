# P9 — Go-Live (Week 24)

**Goal:** Liger's business runs on the system, and credit enforcement is switched on deliberately — not accidentally.

**Entry gate:** P8 Definition of Done fully green, including the parallel run and the owner's sign-off on shadow-mode decisions.
**Rules implemented:** BR-CR-40 (the shadow → enforce switch).

---

## Rollout sequence — strictly in this order

| Step | Who | Duration | Enforcement mode |
|---|---|---|---|
| **1. Internal only** | Liger staff place all orders in the system; dealers still order by phone/WhatsApp as usual | 3–4 days | `shadow` |
| **2. Pilot dealers** | 10 dealers chosen with the owner — a mix of good payers and habitual late payers | 1 week | `shadow` |
| **3. Enforcement review** | Owner reviews every shadow decision from steps 1–2, dealer by dealer | 1–2 days | `shadow` |
| **4. Enforcement ON for pilots** | `credit_enforcement_mode = enforce` for pilot dealers only | 3–4 days | `enforce` (pilot) |
| **5. Full rollout** | All dealers onboarded in batches by region | 1 week | `enforce` |
| **6. Offline books retired** | Only after step 5 is stable | — | `enforce` |

**The enforcement switch is a deliberate, owner-approved decision (BR-CR-40). It is never flipped as part of a deploy.**

---

## T1 — Cutover

| Id | Task |
|---|---|
| P9-T1-01 | Final production data refresh from the owner's books; freeze offline entry at the agreed cutover time |
| P9-T1-02 | Final reconciliation; owner sign-off on the cutover balance |
| P9-T1-03 | Production deploy from a tagged release; migrations applied; smoke tests |
| P9-T1-04 | Verify every scheduled job runs on its first production night |
| P9-T1-05 | Verify the first real WhatsApp messages land correctly on real dealer numbers |
| P9-T1-06 | Verify the first real online payment reaches the bank account and posts to the ledger |

## T2 — Onboarding

| Id | Task |
|---|---|
| P9-T2-01 | Dealer accounts created; login credentials/OTP flow communicated |
| P9-T2-02 | Onboarding WhatsApp broadcast in each dealer's language with the guide and a login link |
| P9-T2-03 | Batch onboarding by region, with a named staff member supporting each batch |
| P9-T2-04 | Initial credit limits set per dealer from the score's suggestion plus the owner's judgement — **start generous, tighten later** |
| P9-T2-05 | Grace period: for the first 30 days, ladder days are relaxed so nobody is blocked by surprise on day one |

## T3 — Hypercare (2 weeks after go-live)

| Id | Task |
|---|---|
| P9-T3-01 | Daily standup: issues raised, blocked dealers, failed notifications, payment failures |
| P9-T3-02 | Sub-4-hour response on any order-blocking issue |
| P9-T3-03 | Daily reconciliation check for the first 14 days |
| P9-T3-04 | Monitor the block rate — a spike means a rule is mis-tuned, not that dealers suddenly stopped paying |
| P9-T3-05 | Weekly review with the owner; adjust limits and ladder timings |
| P9-T3-06 | Log every dealer complaint and resolve or convert it into a backlog item |

---

## Rollback plan

| Scenario | Action |
|---|---|
| Enforcement blocking dealers wrongly | Set `credit_enforcement_mode = shadow` — **a settings change, no deploy, effective immediately** |
| Notification storm | Pause the `notifications` queue; fix; resume from the dead-letter queue |
| Payment gateway issue | Disable online payments in settings; fall back to offline entry + admin confirmation |
| Data corruption | PITR restore to the last known-good point; runbook P8-T5-11 |
| Bad release | Redeploy the previous tag; migrations are reversible (verified in every phase) |

**Because every rule is a Setting (R8), the most likely go-live problems are fixed by a config change, not an emergency deploy.** That is the point of R8.

---

## Definition of Done — P9 exit gate

- [ ] All dealers onboarded and able to log in
- [ ] Orders flowing through the system daily; offline order-taking stopped
- [ ] Real payments received online and reconciled to the bank
- [ ] WhatsApp notifications delivering to real dealer numbers at expected rates
- [ ] Enforcement switched from `shadow` to `enforce` **by explicit owner decision**, after review
- [ ] First auto-blocks reviewed and confirmed correct
- [ ] Daily reconciliation clean for 14 consecutive days
- [ ] Owner using the dashboard as the primary view of the business
- [ ] Offline books formally retired
- [ ] Hypercare closed; issues either resolved or in the backlog
