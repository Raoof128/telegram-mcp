# Client acceptance: Claude Code

Owner-run, against disposable groups only (P §84); its results are evidence, never a gate.

**Prerequisite not yet met.** The daemon does not yet open `comms.db` and serve the comms
composition (a known gap of Part D; the smoke assembles it in process). Until it does, this run
cannot happen and every field below stays `PENDING OWNER`.

## Connect

```bash
comms client add --name claude-code --helper-path ~/.config/comms/claude-code.seed
```

Then add the server to the project's `.mcp.json` (or the user-scope equivalent):

```json
{"mcpServers": {"comms": {"command": "comms",
  "args": ["mcp", "--stdio", "--client-seed", "/Users/<you>/.config/comms/claude-code.seed"]}}}
```

and allow the server's tools in `.claude/settings.json` (`"allowedTools": ["mcp__comms"]`, design
D.11). Allowing the whole server also allows its destructive tools without a prompt; until the
owner rules on R-A20, add `ask` rules for the destructive ones (`comms_message_delete`, the
member ban/remove tools, `comms_group_delete`, `comms_campaign_send`).

Restart Claude Code; `/mcp` shows `comms` connected with its tools. The seed file stays 0600 and
never enters a repository.

To check the proxy by hand:

```bash
comms mcp --stdio --client-seed ~/.config/comms/claude-code.seed
```

## Record

Fill every field; a field left `PENDING OWNER` means the run did not happen. Paste this table,
filled, into `docs/verification/comms-v0.3-acceptance/<date>-claude-code.md` (D39).

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
