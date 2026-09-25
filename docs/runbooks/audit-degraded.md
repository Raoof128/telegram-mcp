# Audit integrity degraded

The latch is set when an anchor refresh fails or a retention root does not verify. While it is set no new external effect starts; work already started still records.

1. See why.

   ```bash
   comms doctor
   comms audit verify --all
   ```

2. Repair. Repair refuses unless the anchor authenticates, names a retained ancestor of the head, and every link, seal, checkpoint and the lineage verify. Only then does it advance the anchor and clear the latch.

   ```bash
   comms audit repair
   ```

If repair refuses, do not force anything: keep the database and the anchor as they are and restore the anchor from its last good copy, or escalate.
