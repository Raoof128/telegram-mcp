# Client acceptance: Codex

Owner-run, against disposable groups only (P §84); its results are evidence, never a gate.

**Prerequisite not yet met.** The daemon does not yet open `comms.db` and serve the comms
composition (a known gap of Part D; the smoke assembles it in process). Until it does, this run
cannot happen and every field below stays `PENDING OWNER`.

## Connect

```bash
comms client add --name codex --helper-path ~/.config/comms/codex.seed
```

Then add the proxy to `~/.codex/config.toml`:

```toml
[mcp_servers.comms]
command = "comms"
args = ["mcp", "--stdio", "--client-seed", "/Users/<you>/.config/comms/codex.seed"]
```

Restart Codex; its MCP listing shows `comms` with its tools. The seed file stays 0600 and never
enters a repository.

## Record

Fill every field; a field left `PENDING OWNER` means the run did not happen. Paste this table,
filled, into `docs/verification/comms-v0.3-acceptance/<date>-codex.md` (D39).

| Field | Value |
|---|---|
| Client and version | PENDING OWNER |
| Negotiated MCP protocol version | PENDING OWNER |
| Catalog digest the client saw | PENDING OWNER |
| Catalog digest of the artifact | PENDING OWNER |
| Auth | local stdio proxy with a `cml1` lease (`comms mcp --stdio`) |
| Visible tools (count; all 109 listed?) | PENDING OWNER |
| A read: `comms_group_list`, then `comms_context_recent` on a disposable group | PENDING OWNER |
| A write: `comms_message_send` from the bot into the disposable group | PENDING OWNER |
| A destructive call: `comms_message_delete` of that message (the client must mark it destructive and confirm) | PENDING OWNER |
| An ambiguity refusal: "Remove Ali from MQ" with two Alis and two MQ groups in the directory; the client asks, and no write is made | PENDING OWNER |
| A request-id replay: the same `comms_message_send` with the same `request_id` returns `replayed: true` and the same `op_ref`, and the group holds one message | PENDING OWNER |
| The P §80 prompts (`tests/evaluation/intent_prompts.json`): tools chosen per prompt | PENDING OWNER |
| `comms audit verify --all` afterwards | PENDING OWNER |

## Afterwards

Disable the client (`comms client disable <client-ref>`) if it was added only for this run,
and keep the evidence file.
