# Uninstall comms

1. Take a backup first (see `restore-from-backup.md`): it is the only copy of the directory.

   ```bash
   comms backup export
   ```

2. Revoke the Telegram session at Telegram, and every provider credential.

   ```bash
   comms transport telegram revoke-session
   comms credential revoke telegram-bot-token
   comms credential revoke meta-access-token
   ```

3. Disable every MCP client, then stop the daemon.

   ```bash
   # <client-ref> is the cli_ ref `comms client add` printed
   comms client disable <client-ref>
   ```

4. Remove the service accounts and paths with the installers' `uninstall` action, after reading their dry-run plan.

Out of repo, owner-approved only: the retired Secure Enclave approval key and the old consent bundle are removed by the runbook in `docs/comms-spec-v0.2.md`, never automatically.
