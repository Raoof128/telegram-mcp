# Install comms

Owner-run. Nothing here touches a provider until the credential steps.

1. Create the service accounts and the runtime paths. Both installers are idempotent; read the plan first.

   ```bash
   scripts/install_service_users.sh install --dry-run
   scripts/install_paths.sh install --dry-run
   ```

2. Provision the comms keys (audit chain, checkpoints, campaign commitments, backups). Material goes into the daemon's 0600 slot store only.

   ```bash
   comms keys provision
   ```

3. Start the daemon, run the cutover (on a fresh install it seals the empty legacy chain and
   opens the comms chain; until it completes, the daemon serves reads and holds every write),
   then check it.

   ```bash
   comms daemon
   comms cutover run
   comms keys list
   comms doctor
   ```

   Retention runs daily inside the daemon once the cutover completes.

4. Add provider credentials (each is proved live before it activates), and log the Telegram user session in.

   ```bash
   comms credential set telegram-bot-token
   comms credential set meta-access-token
   comms transport telegram login
   ```

5. Add each MCP client. Its `cml1` seed goes to that client's helper only.

   ```bash
   comms client add --name claude-code --helper-path ~/.config/comms/claude-code.seed
   ```

`comms doctor` must report no finding except `CREDENTIAL_NOT_CONFIGURED` for providers you do not use, and `"ok": true`. A Meta webhook secret reads `CREDENTIAL_UNCONFIRMED` until Meta has used it once (R-E6).
