# Internal working documents

These are the project's design and execution records, kept in the repository
because they are the evidence behind the claims made elsewhere — not because they
are polished reading.

They are published deliberately. A security-sensitive project should be able to
show *how* its decisions were reached, not only assert that they were sound.

## What is here

| Directory | Contents |
|---|---|
| [`specs/`](specs/) | The design specification: threat model, security invariants, and the reasoning behind each architectural decision. |
| [`plans/`](plans/) | Phase-by-phase implementation plans, written before the code and followed task by task. |
| [`findings/`](findings/) | Verification records — external assumptions with the primary-source quote that confirms or refutes each one. |

## Start here

- **[The design specification](specs/2026-08-27-whatsvault-design.md)** — the
  authoritative document. Everything else serves it.
- **[Phase-0 verification](findings/2026-08-27-phase0-verification.md)** — the
  record that keeps the live write path switched off. Each external assumption is
  either quoted from a primary source or marked unverified, and gates stay closed
  until observed. This is the discipline the project runs on: a plausible reading
  of a vendor's documentation is not evidence.

## How to read them

A few conventions will look unusual out of context:

- Plans open with a note addressed to **agentic workers** and reference skills by
  name (`superpowers:executing-plans`). Much of this project was built with an AI
  coding agent working from these plans, and the notes are how the work was
  actually directed. They are left as written rather than sanitised.
- Steps use `- [ ]` checkboxes, and most follow a strict test-first cycle: write
  the failing test, watch it fail for the right reason, write the minimal code,
  watch it pass, commit.
- **Ledger numbers** (`#23`, `#54`) refer to entries in the corrections ledger in
  the master plan. Where the code cites one in a comment, it is pointing at the
  recorded reason that line exists.

These documents are historical. Where one disagrees with the code, the code and
its tests are correct — but the disagreement is usually worth understanding.
