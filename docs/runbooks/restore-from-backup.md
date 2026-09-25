# Restore from a backup

A backup is age-encrypted to your recipient and signed with the installation's `backup-key`. Import is staged first and changes nothing until you commit it.

1. Stage the import. `--from` names the backup without its suffix (the CLI reads `<name>.age` and `<name>.sig`, as `comms backup export --out <name>` wrote them). The age identity is read by the daemon from a private 0600 file its user owns, never typed or passed in the environment.

   ```bash
   comms backup import stage --from ~/comms-backups/nightly --identity /var/db/telegram-mcp/backup.key
   ```

   The stage reports the signer, the diff per section and any incompatibility. On a fresh installation the signer is unknown here: name it once with `--trust-key`. A backup from another installation or other provider accounts is a `BINDING_MISMATCH`; restore it only with an explicit `--adopt`, which records both bindings.

2. Commit within ten minutes, as the same admin user, with the handle the stage printed. The commit refuses if the live directory changed since staging.

   ```bash
   comms backup import commit --handle <handle-from-the-stage>
   ```

   The import opens a new audit-chain epoch whose first event is `admin.backup_import`. Anything the backup lacks is disabled, never deleted.

3. Verify.

   ```bash
   comms audit verify --all
   comms doctor
   ```
