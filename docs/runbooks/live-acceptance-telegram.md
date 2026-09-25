# Live acceptance: Telegram (bot and user actors)

Owner-run. It uses disposable test groups only (P §84) and its results are evidence, never a
gate: the run writes `docs/verification/live-acceptance/<date>.json` and asserts nothing.

## Prepare, once

1. Create two disposable supergroups (one a forum) and one disposable basic group. Never use a
   group with real members.
2. Add the test bot and make it an administrator with every right in one supergroup and with
   no rights in the other.
3. Log the test user account in (`comms transport telegram login` on the daemon host) and add a second,
   disposable user as an ordinary member for member operations.
4. Put the bot token in the secret store (`telegram-bot-token`). Credentials never go in a file
   in the repository or in the accounts description.
5. Describe the accounts, identifiers only, in a JSON file outside the repository:

   ```json
   {"telegram_bot": {"admin_chat": "-100…", "plain_chat": "-100…"},
    "telegram_user": {"forum_chat": "-100…", "basic_chat": "-…", "member_user_id": 123}}
   ```

## Run

```bash
COMMS_LIVE_ACCOUNTS=/path/outside/repo/accounts.json \
  uv run pytest tests/conformance/test_live_acceptance.py --run-live-acceptance -q -s
```

## What the run covers (P §84)

Only operations legitimately available to the test actor are expected to pass; a refusal the
actor's rights explain is a correct result.

| Area | Bot | User |
|---|---|---|
| bot send / user send | `sendMessage` | `messages.sendMessage` with `random_id` |
| member list | not available (reported `PROVIDER_UNSUPPORTED`) | `channels.getParticipants` |
| remove (ban then unban saga) | yes | yes; a basic group removes with `deleteChatUser` |
| restrict | yes | supergroups only |
| promote/demote (exact rights, P §74 profiles) | yes | yes; a basic group only as `full_admin` |
| invite creation | yes | yes |
| history / search | not available (local updates only) | `messages.getHistory`, `messages.search` |
| topic operations | forum only | forum only |
| message edit/delete | own messages | own messages |

Until the accounts description names an adapter, its cases report `NOT_CONFIGURED`. The case
bodies against real accounts are filled in at the first owner-run session; the evidence file of
that session is the proof.

## Afterwards

Delete the disposable groups, revoke the test bot's token (`comms credential rotate
telegram-bot-token`) if it was ever shared, and keep the evidence file.
