---
description: Execute a Liger build phase (P0–P10) with its parallel tracks
argument-hint: P0 | P1 | P2 | P3 | P4 | P5 | P6 | P7 | P8 | P9 | P10
---

Execute Liger build phase: **$ARGUMENTS**

Follow this sequence exactly. Do not skip steps.

## 1. Load context
- Read `CLAUDE.md` in full — the 14 non-negotiable rules (R1–R14) apply to every line of code you write.
- Read `docs/EXECUTION.md` — phase dependencies and the track conflict map.
- Read `docs/phases/$ARGUMENTS-*.md` — the phase you are executing.
- Read every `BR-` rule cited by that phase in `docs/BUSINESS_RULES.md`. **Implement from the spec, not from memory.**
- Read `docs/DATA_MODEL.md` and `docs/API_SPEC.md` for the parts you are touching.
- For any frontend task, read `frontend_new/AGENTS.md` first — this Next.js version differs from training data. Binding.

## 2. Check the entry gate
The phase doc opens with an **Entry gate**. Verify the previous phase's Definition of Done is actually met — run its verification commands, don't assume.

**If the gate is not green: stop and report which items are unmet.** Do not build on an unfinished foundation.

## 3. Check blocking decisions
If the phase lists blocked `DEC-` decisions (`docs/BUSINESS_RULES.md` §0), check whether the owner has signed them off. If not, implement the assumed default **as a Setting** (rule R8), and list the unconfirmed decisions clearly in your final report.

## 4. Plan the tracks
List the phase's tracks (`T1`, `T2`, …) and their task ids. Confirm against the track conflict map in `docs/EXECUTION.md` that no two tracks touch the same file.

Execute tracks **sequentially by default**. Only fan out to parallel subagents if the user explicitly asks for it in this conversation.

## 5. Execute
For each task, in track order:
- Cite the `BR-` id in a code comment and in the test name (rule R12)
- Write the migration, then the service, then the API, then the frontend
- Write the tests with the code, not after
- Run the verification commands after each task — green means green

## 6. Verify
```powershell
cd backend; alembic upgrade head; alembic downgrade -1; alembic upgrade head
```
```powershell
cd backend; pytest -q --cov=app --cov-report=term-missing; ruff check app; mypy app
```
```powershell
cd frontend_new; npx tsc --noEmit; npm run lint
```

## 7. Report
Go through the phase's **Definition of Done** line by line. For each item state: passed, failed, or not attempted — with the evidence.

**Never report a phase complete while any check is failing.** If something is blocked, finish everything else and say plainly what was left and why.

## Hard stops
- No `Float` for money — integer paise only (R1)
- No hardcoded rule values — Settings only (R8)
- No business logic in routers or in the frontend (R4)
- No `UPDATE`/`DELETE` on ledger, audit, or status-history tables (R2)
- No secrets committed (R14)
- No credit enforcement enabled before P9 — shadow mode only (BR-CR-40)
