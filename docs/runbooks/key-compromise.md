# Key compromise

1. Rotate the key. Each rotation is staged, proved and activated in one audited transaction; rotating `audit-chain-key` seals the current epoch and opens the next.

   ```bash
   comms keys rotate audit-chain-key
   comms keys rotate audit-checkpoint-key
   comms keys rotate backup-key
   ```

2. For a backup signer, mark the old key by what you know. `VERIFICATION_ONLY` keeps what it signed checkable but refuses it for import; `REVOKED` refuses both and keeps it listed for forensics. Trust only ever decreases.

   ```bash
   comms keys list
   comms keys mark-signer --key-id <key-id-from-keys-list> --state VERIFICATION_ONLY
   ```

3. Verify both chains and the doctor.

   ```bash
   comms audit verify --all
   comms doctor
   ```

The `comms.db` key rotates with `comms keys rotate comms-db-key`: the database is rekeyed, reopened and verified before the old key is destroyed.
