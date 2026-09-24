## What and why

<!-- What changes, and what problem it solves. Link the issue if there is one. -->

## Verification

<!-- What you ran and what it showed. "Tests pass" is weaker than the output. -->

- [ ] `make check` is green (lint, format, full suite)
- [ ] A test was added that **fails without this change**
- [ ] Documentation updated if behaviour or a CLI verb changed

## Security boundary

Does this touch the MCP surface, the approval chain, key handling, the audit log,
or the ingest ACK path?

- [ ] No — this changes none of those
- [ ] Yes — completed below

If yes:

- **Invariant affected:**
- **What I tried in order to break it, and what happened:**
- **Regression test added in `tests/adversarial/`:**

## Notes for the reviewer

<!-- Anything you are unsure about, or deliberately left out. -->
