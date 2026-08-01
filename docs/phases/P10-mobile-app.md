# P10 — Mobile App (Weeks 25–32)

**Goal:** put the same system in dealers' and staff's pockets, on the same API, with the things only a phone can do — push notifications, camera, offline drafts.

**Entry gate:** P9 complete and stable. The website is proven in real use before app work begins — this was the owner's explicit sequencing.
**Rules implemented:** all existing `BR-` rules, unchanged. **No new business logic** — that is the payoff of R4 (API-first).

---

## Why this phase is cheap

Every business rule already lives behind `/api/v1`. The app is a client. If P10 requires backend changes beyond additive endpoints (push tokens, app version check), then R4 was violated somewhere earlier and that is the real bug to fix.

**Stack:** React Native + Expo, TypeScript. **Android first** — that is what Liger's dealers use. iOS follows.
**Distribution:** one app, role-switched (dealer vs. staff screens). Cheaper to maintain than two apps, and both audiences get the same reliability.

---

## T1 — Foundation

| Id | Task |
|---|---|
| P10-T1-01 | Expo project, TypeScript, navigation, EAS Build pipeline |
| P10-T1-02 | Reuse the generated API client from the OpenAPI schema — **the same contract as web** |
| P10-T1-03 | Auth: dealer phone + OTP, staff email + password + 2FA; secure token storage (Keychain/Keystore); biometric unlock |
| P10-T1-04 | Design system ported from the web primitives — one visual language across web and app |
| P10-T1-05 | i18n (en/hi/gu) shared with web |
| P10-T1-06 | Offline-first data layer with queue-and-sync |

## T2 — Dealer app

| Id | Task |
|---|---|
| P10-T2-01 | Home: credit status (colour state), outstanding, quick reorder |
| P10-T2-02 | Catalogue browse with images, search, filters |
| P10-T2-03 | **Order entry — the L × B screen, phone-optimised**: big numeric inputs, live sq.ft, min-11 note, design preview, running total, credit strip |
| P10-T2-04 | Cart and checkout with the live credit gate; the blocked screen with Pay Now |
| P10-T2-05 | My orders with live status tracking and push updates |
| P10-T2-06 | Invoices, ledger, statements — view and share as PDF |
| P10-T2-07 | Pay Now — UPI intent, cards, netbanking through the gateway SDK |
| P10-T2-08 | Push notifications for every event in BR-NOT-03 |
| P10-T2-09 | **Offline draft orders** — compose without signal, auto-sync when connected |
| P10-T2-10 | Share order/invoice directly to WhatsApp |

## T3 — Staff app

| Id | Task |
|---|---|
| P10-T3-01 | Order on behalf of a dealer, with the dealer's live credit state visible |
| P10-T3-02 | Customer 360 on mobile — call the dealer directly from the insight card |
| P10-T3-03 | Follow-up task board with call-outcome logging |
| P10-T3-04 | Cash collection entry with **slip photo from the camera** → admin confirmation queue |
| P10-T3-05 | Delivery confirmation with POD photo capture |
| P10-T3-06 | Owner dashboard summary with push alerts for blocks and large orders |
| P10-T3-07 | Production/dispatch status updates from the factory floor |

## T4 — Backend additions *(the only backend work in this phase)*

| Id | Task |
|---|---|
| P10-T4-01 | `POST /devices` — push token registration per user/customer_user |
| P10-T4-02 | Push provider adapter (FCM/APNs) added to the notification engine — same templates, new channel |
| P10-T4-03 | `GET /app/version` — minimum supported version, force-update flag |
| P10-T4-04 | Sync endpoints for offline draft reconciliation (idempotent, **R6**) |

## T6 — Testing & release

| Id | Task |
|---|---|
| P10-T6-01 | Detox/Maestro E2E on the critical dealer journey |
| P10-T6-02 | **Real low-end Android device testing** — that is the actual target hardware |
| P10-T6-03 | Offline scenarios: compose offline, kill the app, reopen, sync — nothing lost, nothing duplicated |
| P10-T6-04 | Push delivery verified on both platforms |
| P10-T6-05 | Play Store listing, privacy policy, data-safety declaration, internal testing track |
| P10-T6-06 | Phased Play Store rollout (10% → 50% → 100%) |
| P10-T6-07 | App Store submission after Android is stable |
| P10-T6-08 | OTA update channel (EAS Update) for fast fixes without a store review |

---

## Definition of Done — P10 exit gate

- [ ] **No business logic added to the app** — every rule still enforced server-side
- [ ] Order entry on a phone is genuinely faster than the mobile website
- [ ] Offline drafts sync without loss or duplication
- [ ] Push notifications delivering on Android and iOS
- [ ] Payments work through the mobile gateway SDK
- [ ] Cash slip and delivery POD photo capture working from the camera
- [ ] Tested on real low-end Android hardware
- [ ] Live on Play Store; App Store submitted
- [ ] OTA update channel proven with one shipped fix
- [ ] Web and app show identical numbers for the same dealer, always
