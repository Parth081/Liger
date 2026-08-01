# P1 — Catalogue & Pricing (Weeks 3–4)

**Goal:** the design catalogue with images, and a pricing engine that turns `Design No + L × B × Qty` into an exact rupee amount — the calculation the whole business runs on.

**Entry gate:** P0 Definition of Done fully green.
**Rules implemented:** BR-CAT-01…09, BR-PR-01…11, BR-SQFT-01…11, BR-TAX-01…04.
**Decisions:** all resolved 2026-08-01 (see `BUSINESS_RULES.md` §0). **DEC-03: no price tiers — one rate per design for every customer.**

---

## T1 — Data & migrations

| Id | Task |
|---|---|
| P1-T1-01 | `categories`, `designs` (**unique index on `lower(design_no)`**), `design_images`, `accessories` |
| P1-T1-02 | `rate_cards`, `rate_card_items` (one rate per design — DEC-03, no tiers), `customer_special_rates`, `making_charges` |
| P1-T1-03 | Customer fields for tax/analytics: region/state/city (no price tier — DEC-03) |
| P1-T1-04 | `pg_trgm` extension + trigram indexes on `design_no` and `designs.name` for search |
| P1-T1-05 | Seed settings: `min_billable_sqft=11.00`, `sqft_rounding_step=0.25`, `max_dimension_in=600`, `max_rep_discount_pct=5` |

## T2 — Pricing engine *(the highest-risk code in the project)*

| Id | Task |
|---|---|
| P1-T2-01 | `pricing/sqft.py` — **one** function implementing BR-SQFT-01…09. Inputs in inches, min-11 per piece (DEC-01), rounding step (DEC-02), dimension guards. Returns `raw_sqft`, `billable_sqft`, `min_rule_applied`, `line_area`. |
| P1-T2-02 | `pricing/rate_resolver.py` — BR-PR-01 cascade: special → base → **explicit failure**. Returns `rate_paise` + `rate_source`. Never returns zero as a fallback. |
| P1-T2-03 | `pricing/charges.py` — making/stitching per sq.ft or per piece by product type (DEC-05); accessories priced by their own UOM (BR-CAT-08, DEC-12) |
| P1-T2-04 | `pricing/discount.py` — line and order discount, rep cap → `PENDING_APPROVAL` above the cap (BR-PR-07) |
| P1-T2-05 | `pricing/tax.py` — GST per line from the design (DEC-04); CGST+SGST vs. IGST by place of supply (BR-TAX-02); HSN captured |
| P1-T2-06 | `pricing/service.py::price_line()` and `price_cart()` — order-level discount apportioned pro-rata **before** tax, freight/packing, `ROUND_HALF_UP` at every stored step, final round-off to the rupee (BR-PR-09/10) |
| P1-T2-07 | Rate-card versioning: publish sets `effective_from`; historical pricing resolves against the version recorded on the document (BR-PR-03) |
| P1-T2-08 | Catalogue service: CRUD, status transitions, `design_no` case-insensitive lookup returning the resolved rate (BR-CAT-06) |
| P1-T2-09 | Image pipeline: upload → S3 → WebP variants thumb/card/zoom → CDN URLs (BR-CAT-05) |
| P1-T2-10 | Excel importer for designs with dry-run, validation report, and commit (BR-CAT-09) |

## T3 — API layer

| Id | Task |
|---|---|
| P1-T3-01 | `GET /designs`, `GET /designs/{design_no}` (**p95 < 300 ms**, returns resolved rate), `POST/PATCH /designs`, image upload |
| P1-T3-02 | `POST /pricing/calculate-line` and `POST /pricing/quote-cart` — exact response shape from `API_SPEC` §3, including the human-readable `notes` array (BR-SQFT-07) |
| P1-T3-03 | Rate-card endpoints incl. `POST /rate-cards/{uid}/publish` |
| P1-T3-04 | Customer special-rate endpoints |
| P1-T3-05 | `GET /categories`, `GET /accessories`, `POST /imports/designs?dry_run=` |

## T4 — Frontend

| Id | Task |
|---|---|
| P1-T4-01 | Admin catalogue: list, search, filter, create/edit, status change |
| P1-T4-02 | Image upload with drag-drop, reorder, cover selection, live preview |
| P1-T4-03 | Rate-card manager: versions, rate grid, bulk edit, publish with a confirmation diff |
| P1-T4-04 | Dealer catalogue browse: image grid, category filter, search, design detail with the rate |
| P1-T4-05 | Reusable `<DesignPreview design_no>` component — image, name, category, rate; the order form (P2) consumes this |
| P1-T4-06 | Excel import UI: upload → dry-run report → confirm |

## T6 — Tests *(this phase's tests are the safety net for every rupee)*

| Id | Task |
|---|---|
| P1-T6-01 | **All 8 worked examples in BR-SQFT** as parameterised tests, named `test_BR_SQFT_*` |
| P1-T6-02 | Boundary tests: exactly 11.00 sq.ft, 10.99, 11.01, dimension = 0, negative, above `max_dimension_in`, quantity 0/1/999 |
| P1-T6-03 | Rate cascade: special active, special expired, base present, nothing → **explicit rejection** |
| P1-T6-04 | Tax: intra-state CGST+SGST split, inter-state IGST, mixed GST% across lines in one order |
| P1-T6-05 | Rounding: order discount apportioned across 3 lines, paise reconcile exactly to the total |
| P1-T6-06 | Rate-card versioning: publishing a new card does **not** change a previously priced document |
| P1-T6-07 | **Golden dataset** — real Liger orders from the offline books reproduce to the paise. This is the regression gate for the rest of the project. |

---

## Verification

```powershell
cd backend; pytest -q tests/pricing --cov=app/modules/pricing --cov-report=term-missing
```
Coverage on `modules/pricing/` must be **100% branch**. Anything less fails the phase.

## Definition of Done — P1 exit gate

- [ ] All decisions reflected in Settings (DEC-01…05, DEC-11, DEC-12 — resolved 2026-08-01)
- [ ] `min_billable_sqft` and `sqft_rounding_step` read from Settings — **`11` appears nowhere in code**
- [ ] All 8 BR-SQFT worked examples pass
- [ ] 100% branch coverage on `modules/pricing/`
- [ ] Golden dataset of real historical orders reproduces to the paise
- [ ] `GET /designs/{design_no}` p95 < 300 ms with the full catalogue loaded
- [ ] A design with no resolvable rate is **rejected with a clear message**, never priced at zero
- [ ] Catalogue imported from the owner's Excel; images live on the CDN
- [ ] Rate card v1 published; a second version proves historical documents are untouched
- [ ] `min_rule_applied` surfaces in the API response and renders in the UI
