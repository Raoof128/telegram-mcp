# Telegram session compromise

1. Revoke the session. The daemon marks it revoking and bumps the security epoch before anything is sent, records the start on the audit chain, sends one `auth.LogOut` (never retried), and wipes the local session whatever Telegram answers.

   ```bash
   comms transport telegram revoke-session
   ```

2. From the Telegram app, check Settings → Devices and terminate anything you do not recognise.

3. Log in again. A different Telegram account is refused unless you pass `--new-account`, which moves the active account and keeps the old account's rows for history.

   ```bash
   comms transport telegram login
   ```

4. Check the audit trail and the doctor.

   ```bash
   comms audit verify --all
   comms doctor
   ```
