# Provider credential rotation

A new value is read from stdin, proved live against the provider, activated in one audited transaction, and re-checked; only then is the old value destroyed. A failed re-check restores the old one.

```bash
comms credential rotate telegram-bot-token
comms credential rotate meta-access-token
comms credential rotate meta-app-secret
comms credential rotate meta-webhook-secret
```

Revoking destroys the value locally; the adapter then reports `NOT_CONFIGURED`. Revoke at the provider too (BotFather, Meta Business settings).

```bash
comms credential revoke telegram-bot-token
```
