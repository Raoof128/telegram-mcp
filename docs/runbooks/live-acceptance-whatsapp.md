# Live acceptance: WhatsApp Cloud API and webhooks

Owner-run, against Meta test infrastructure or a dedicated disposable test number (P §85). The
results are evidence, never a gate: the run writes `docs/verification/live-acceptance/<date>.json`
and asserts nothing.

## Prepare, once

1. In the Meta app, add a test number and at most two disposable recipient numbers you own.
2. Create one approved template (for example `comms_live_check`, language `en`) and note its
   components.
3. Put the access token (`meta-access-token`), the app secret (`meta-app-secret`) and the
   webhook verify token (`meta-webhook-secret`) in the secret store.
4. Point the app's webhook at the comms webhook listener (`POST /webhooks/meta`, HTTPS).
5. Describe the accounts, identifiers only, in a JSON file outside the repository:

   ```json
   {"whatsapp_cloud": {"phone_number_id": "…", "waba_id": "…", "recipient": "+61…"},
    "whatsapp_webhooks": {"listener": "https://…/webhooks/meta"}}
   ```

## Run

```bash
COMMS_LIVE_ACCOUNTS=/path/outside/repo/accounts.json \
  uv run pytest tests/conformance/test_live_acceptance.py --run-live-acceptance -q -s
```

## What the run covers (P §85)

| Check | Expected |
|---|---|
| free-form in window | the recipient writes first; a text inside 24 h is accepted |
| template outside window | with no recent customer message, the frozen template is sent |
| media | upload by media id, retrieve through the Meta-only downloader, delete |
| reply | a reply to the recipient's message |
| status webhook | sent/delivered statuses reach the inbox and reconcile the job |
| duplicate webhook | Meta's redelivery is acknowledged and applied once |
| failed status | a send to an unreachable test number ends `FAILED_PERMANENT` |
| template lookup | the template list feeds the catalogue with the frozen version |
| groups (optional) | only when discovery reports the account group-capable; otherwise every group operation reports `PROVIDER_UNSUPPORTED` or `ACCOUNT_INELIGIBLE`, which is itself the expected result |

The Groups API endpoint shapes in `cloud/groups.py` are confirmed by this run; until then they
are unverified. Until the accounts description names an adapter, its cases report
`NOT_CONFIGURED`.

## Afterwards

Remove the test recipients, rotate any credential that left the secret store, and keep the
evidence file.
