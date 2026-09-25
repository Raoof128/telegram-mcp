# Restore from a backup

A backup is age-encrypted to your recipient and signed with the installation's `backup-key`. Import is staged first and changes nothing until you commit it.

1. Stage the import. The age identity is read from a private 0600 file you name, never typed or passed in the environment.

   ```bash
   comms backup import stage
   ```

   The stage reports the signer, the diff per section and any incompatibility. On a fresh installation the signer is unknown here: name it once with `--trust-key`. A backup from another installation or other provider accounts is a `BINDING_MISMATCH`; restore it only with an explicit `--adopt`, which records both bindings.

2. Commit within ten minutes, from the same admin session. The commit refuses if the live directory changed since staging.

   ```bash
   comms backup import commit
   ```

   The import opens a new audit-chain epoch whose first event is `admin.backup_import`. Anything the backup lacks is disabled, never deleted.

3. Verify.

   ```bash
   comms audit verify --all
   comms doctor
   ```
