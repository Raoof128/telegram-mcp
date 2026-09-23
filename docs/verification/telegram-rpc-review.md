# Telegram RPC side-effect review (Phases 4b and 4c)

Every request class the gateway can put on the wire, per operation. This is
the reviewer's record behind `OPERATIONS` in
`src/telegram_mcp/telegram/telethon_adapter.py`. The same set is pinned
independently by `REVIEWED_RPCS` in `tests/security/test_phase4_architecture.py`
and `ALLOWED` in `tests/telegram/recorder.py`. Pinned library:
`telethon==1.45.0`.

The adapter's own `_GatewayClient._call` refuses any request outside the
current operation's list before it reaches the sender, and charges every
request it sends to that operation's work budget. Telethon's login helpers
(`send_code_request`, `sign_in`, `start`, `get_me`, `is_user_authorized`) are
never called: in 1.45.0 they retry on `AuthRestartError`, send
`auth.resendCode` when a phone-code hash is cached, and pull
`updates.getDifference` after login (`telethon/client/auth.py`,
`telethon/client/users.py`).

| Operation | Request | Method page | Side effects | Note |
|---|---|---|---|---|
| `mcp.retrieval` | `messages.GetPeerDialogsRequest` | https://core.telegram.org/method/messages.getPeerDialogs | none | Reads dialog fields, including unread counts. Acknowledges nothing. |
| `mcp.retrieval` | `messages.GetHistoryRequest` | https://core.telegram.org/method/messages.getHistory | none | Does not mark messages read (`messages.readHistory` does, and it is prohibited). Does not increment views. |
| `mcp.retrieval` | `messages.GetMessagesRequest` | https://core.telegram.org/method/messages.getMessages | none | By id; private chats and basic groups. The `get_context` anchor (4c). |
| `mcp.retrieval` | `channels.GetMessagesRequest` | https://core.telegram.org/method/channels.getMessages | none | By id; supergroups and channels. Does not increment views (`messages.getMessagesViews` does, and it is prohibited). |
| `mcp.retrieval` | `messages.SearchRequest` | https://core.telegram.org/method/messages.search | none | One peer per request, `InputMessagesFilterEmpty`; `min_date`/`max_date` strict, 0 = unbounded (design §4.2). Never `messages.searchGlobal`. |
| `mcp.retrieval` | `messages.GetRepliesRequest` | https://core.telegram.org/method/messages.getReplies | none | The thread of one forum topic for `get_context`; it cannot cross into another topic (design §4.1). |
| `admin.discover` | `messages.GetDialogsRequest` | https://core.telegram.org/method/messages.getDialogs | none | Operator discovery only; never used by an MCP tool. |
| `admin.status` | `updates.GetStateRequest` | https://core.telegram.org/method/updates.getState | none | Authorisation probe. Returns counters only; pulls no content. |
| `admin.status` | `users.GetUsersRequest` | https://core.telegram.org/method/users.getUsers | none | Only with `[InputUserSelf]`, for the account's own id. |
| `admin.login` | `auth.SendCodeRequest` | https://core.telegram.org/method/auth.sendCode | sends a login code | Once per operator step; never retried and never resent. |
| `admin.login` | `auth.SignInRequest` | https://core.telegram.org/method/auth.signIn | authorises this session | Approved by Touch ID; the code is bound into the signed request by keyed digest. |
| `admin.login` | `account.GetPasswordRequest` | https://core.telegram.org/method/account.getPassword | none | SRP parameters for 2FA. |
| `admin.login` | `auth.CheckPasswordRequest` | https://core.telegram.org/method/auth.checkPassword | authorises this session | SRP proof; the password never leaves the daemon. |
| `admin.login` | `help.GetConfigRequest` | https://core.telegram.org/method/help.getConfig | none | Read by the one explicit, budgeted DC switch on `PHONE_MIGRATE`. |

**Absent in every phase:**
- `updates.GetDifferenceRequest`: 4b login no longer calls it.
- `auth.ResendCodeRequest`, `auth.LogOutRequest`.
- `account.UpdatePasswordSettingsRequest`, `account.ConfirmPasswordEmailRequest`.
- `contacts.ResolveUsernameRequest`, `channels.GetChannelsRequest`.
- Every takeout request.
- `messages.SearchGlobalRequest`.
- `messages.ReadHistoryRequest` and the other read-acknowledge methods.
- `messages.GetMessagesViewsRequest`.

Transport requests Telethon issues on the sender directly (not through
`_call`): `InvokeWithLayer`, `InitConnection`, `InvokeWithoutUpdates`, `Ping`,
`help.GetConfig`, `auth.ExportAuthorization`, `auth.ImportAuthorization`.
