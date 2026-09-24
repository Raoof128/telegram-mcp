# Comms v0.3 rulings register

Every decision taken while executing plan `docs/superpowers/plans/2026-09-24-comms-v0.3.md` (rev 2) that deviates from it, or resolves a contradiction between plan and spec (spec A39). This register records the decision independently of any conversation.

**Pinned at pre-flight:**

| Document | SHA-256 |
|---|---|
| `docs/comms-spec-v0.3.md` (rev 2) | `7a415167bce71862…` |
| `docs/superpowers/specs/2026-09-24-comms-v0.3-design.md` (rev 2) | `66f6a6f5d633a0c0…` |

| ID | Date | Section | Discovery | Decision | Reason | Tests | SHA |
|---|---|---|---|---|---|---|---|
| R-000 | 2026-09-24 | Plan tooling | `executing-plans` helper scripts accept numeric task IDs only; this plan uses A1–D39. | Record each task's BASE and completion in the ledger by hand, after the same test run. | Keeps the execution record without renaming 130 tasks. | — | (this commit) |
| R-001 | 2026-09-24 | Spec A31 / P §4 / design D.4 | Task A1b's disjointness test measured that `msg_` and `tpl_` collide with WhatsVault's `msg` and `tpl` ID prefixes, which reach the context engine through the webhook archive. | Comms uses `cmg_` (message) and `ctp_` (template). The spec (A31 execution amendment), design and plan are amended explicitly. | A single prefix registry must be disjoint (5b-4 §1); an ambiguous ref would misroute reply, edit or delete. | `test_v03_prefixes_disjoint_from_telegram_whatsvault_and_5b4` | (A1b commit) |
