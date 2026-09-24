# The v0.3 cutover

Run once, after upgrading. It is resumable: rerunning continues from the last completed phase and fails closed on any conflict.

```bash
comms cutover run
comms cutover status
```

The phases: close and drain the legacy ingress, verify the legacy chain, seal it (the database then refuses legacy audit appends), anchor it, write the lineage and the comms genesis event, anchor that, retire `tgml1`, complete.

Then verify end to end.

```bash
comms audit verify --all
comms doctor
```

Before the cutover the legacy tools still answer on their own terms:

```bash
telegram-mcp doctor
telegram-mcp admin audit verify
```
