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
| `admin.revoke` | `auth.LogOutRequest` | https://core.telegram.org/method/auth.logOut | ends this authorisation at Telegram | comms v0.3 B14: once per `auth revoke-this-session`, never retried, only after the `started` event is committed and anchored; the local session is wiped whatever the outcome. |

## comms v0.3 (Task C14): the capability RPC sets (A21)

Each capability of the user actor is its own recorder operation (`cap.<capability id>`), so a
call may put only that capability's request classes on the wire. The three sets (`READ_RPCS`,
`WRITE_RPCS`, `ADMIN_RPCS` in `telethon_adapter.py`) are disjoint; none is allowed on
`mcp.retrieval` or the admin plane. The side-effect class and ambiguity policy are the
`SEMANTICS` table's (Task C3). The client runs with `request_retries=0`,
`flood_sleep_threshold=0`, `auto_reconnect=False` and `connection_retries=0`: Telethon's
automatic reconnect re-queues every in-flight request, which would replay a write, so a
dropped link fails the in-flight call and only the daemon's keeper reconnects.

| Operation | Request | Method page | Side effects | Note |
|---|---|---|---|---|
| `cap.history.read` | `channels.GetMessagesRequest` | https://core.telegram.org/method/channels.getMessages | none | `READ`, `retry_same_key` |
| `cap.history.read` | `messages.GetHistoryRequest` | https://core.telegram.org/method/messages.getHistory | none | `READ`, `retry_same_key` |
| `cap.history.read` | `messages.GetMessagesRequest` | https://core.telegram.org/method/messages.getMessages | none | `READ`, `retry_same_key` |
| `cap.history.read` | `messages.GetRepliesRequest` | https://core.telegram.org/method/messages.getReplies | none | `READ`, `retry_same_key` |
| `cap.history.search` | `messages.SearchRequest` | https://core.telegram.org/method/messages.search | none | `READ`, `retry_same_key` |
| `cap.member.list` | `channels.GetParticipantsRequest` | https://core.telegram.org/method/channels.getParticipants | none | `READ`, `retry_same_key` |
| `cap.member.list` | `messages.GetFullChatRequest` | https://core.telegram.org/method/messages.getFullChat | none | `READ`, `retry_same_key` |
| `cap.member.get` | `channels.GetParticipantRequest` | https://core.telegram.org/method/channels.getParticipant | none | `READ`, `retry_same_key` |
| `cap.member.get` | `messages.GetFullChatRequest` | https://core.telegram.org/method/messages.getFullChat | none | `READ`, `retry_same_key` |
| `cap.admin.list` | `channels.GetParticipantsRequest` | https://core.telegram.org/method/channels.getParticipants | none | `READ`, `retry_same_key` |
| `cap.admin.list` | `messages.GetFullChatRequest` | https://core.telegram.org/method/messages.getFullChat | none | `READ`, `retry_same_key` |
| `cap.admin.log.read` | `channels.GetAdminLogRequest` | https://core.telegram.org/method/channels.getAdminLog | none | `READ`, `retry_same_key` |
| `cap.invite.list` | `messages.GetExportedChatInvitesRequest` | https://core.telegram.org/method/messages.getExportedChatInvites | none | `READ`, `retry_same_key` |
| `cap.join_request.list` | `messages.GetChatInviteImportersRequest` | https://core.telegram.org/method/messages.getChatInviteImporters | none | `READ`, `retry_same_key` |
| `cap.topic.list` | `messages.GetForumTopicsRequest` | https://core.telegram.org/method/messages.getForumTopics | none | `READ`, `retry_same_key` |
| `cap.message.send` | `messages.SendMessageRequest` | https://core.telegram.org/method/messages.sendMessage | posts a message | `MESSAGE_SEND`, `retry_same_key` |
| `cap.message.edit` | `messages.EditMessageRequest` | https://core.telegram.org/method/messages.editMessage | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.message.delete` | `channels.DeleteMessagesRequest` | https://core.telegram.org/method/channels.deleteMessages | destroys or converts an object | `DESTRUCTIVE_NONIDEMPOTENT`, `resolve_only` |
| `cap.message.delete` | `messages.DeleteMessagesRequest` | https://core.telegram.org/method/messages.deleteMessages | destroys or converts an object | `DESTRUCTIVE_NONIDEMPOTENT`, `resolve_only` |
| `cap.message.forward` | `messages.ForwardMessagesRequest` | https://core.telegram.org/method/messages.forwardMessages | posts a message | `MESSAGE_SEND`, `retry_same_key` |
| `cap.message.pin` | `messages.UpdatePinnedMessageRequest` | https://core.telegram.org/method/messages.updatePinnedMessage | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.member.add` | `channels.InviteToChannelRequest` | https://core.telegram.org/method/channels.inviteToChannel | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.member.add` | `messages.AddChatUserRequest` | https://core.telegram.org/method/messages.addChatUser | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.member.remove` | `channels.EditBannedRequest` | https://core.telegram.org/method/channels.editBanned | sets a chat or member state | `SET_STATE`, `retry_same_key`; a saga of `member.ban`, `member.unban` |
| `cap.member.remove` | `messages.DeleteChatUserRequest` | https://core.telegram.org/method/messages.deleteChatUser | sets a chat or member state | `SET_STATE`, `retry_same_key`; a saga of `member.ban`, `member.unban` |
| `cap.member.ban` | `messages.DeleteChatUserRequest` | https://core.telegram.org/method/messages.deleteChatUser | sets a chat or member state | `SET_STATE`, `retry_same_key`; C18: a basic group has no ban list, so its ban is this removal (the saga's unban is then a no-op) |
| `cap.member.ban` | `channels.EditBannedRequest` | https://core.telegram.org/method/channels.editBanned | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.member.unban` | `channels.EditBannedRequest` | https://core.telegram.org/method/channels.editBanned | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.member.restrict` | `channels.EditBannedRequest` | https://core.telegram.org/method/channels.editBanned | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.admin.promote` | `channels.EditAdminRequest` | https://core.telegram.org/method/channels.editAdmin | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.admin.promote` | `messages.EditChatAdminRequest` | https://core.telegram.org/method/messages.editChatAdmin | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.admin.demote` | `channels.EditAdminRequest` | https://core.telegram.org/method/channels.editAdmin | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.admin.demote` | `messages.EditChatAdminRequest` | https://core.telegram.org/method/messages.editChatAdmin | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.invite.create` | `messages.ExportChatInviteRequest` | https://core.telegram.org/method/messages.exportChatInvite | creates an object | `CREATE`, `resolve_only` |
| `cap.invite.edit` | `messages.EditExportedChatInviteRequest` | https://core.telegram.org/method/messages.editExportedChatInvite | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.invite.revoke` | `messages.EditExportedChatInviteRequest` | https://core.telegram.org/method/messages.editExportedChatInvite | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.join_request.approve` | `messages.HideChatJoinRequestRequest` | https://core.telegram.org/method/messages.hideChatJoinRequest | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.join_request.reject` | `messages.HideChatJoinRequestRequest` | https://core.telegram.org/method/messages.hideChatJoinRequest | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.chat.set_title` | `channels.EditTitleRequest` | https://core.telegram.org/method/channels.editTitle | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.chat.set_title` | `messages.EditChatTitleRequest` | https://core.telegram.org/method/messages.editChatTitle | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.chat.set_description` | `messages.EditChatAboutRequest` | https://core.telegram.org/method/messages.editChatAbout | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.chat.set_photo` | `channels.EditPhotoRequest` | https://core.telegram.org/method/channels.editPhoto | creates an object | `CREATE`, `resolve_only` |
| `cap.chat.set_photo` | `messages.EditChatPhotoRequest` | https://core.telegram.org/method/messages.editChatPhoto | creates an object | `CREATE`, `resolve_only` |
| `cap.chat.set_permissions` | `messages.EditChatDefaultBannedRightsRequest` | https://core.telegram.org/method/messages.editChatDefaultBannedRights | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.topic.create` | `messages.CreateForumTopicRequest` | https://core.telegram.org/method/messages.createForumTopic | creates an object | `CREATE`, `resolve_only` |
| `cap.topic.edit` | `messages.EditForumTopicRequest` | https://core.telegram.org/method/messages.editForumTopic | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.topic.close` | `messages.EditForumTopicRequest` | https://core.telegram.org/method/messages.editForumTopic | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.topic.reopen` | `messages.EditForumTopicRequest` | https://core.telegram.org/method/messages.editForumTopic | sets a chat or member state | `SET_STATE`, `retry_same_key` |
| `cap.group.create` | `channels.CreateChannelRequest` | https://core.telegram.org/method/channels.createChannel | creates an object | `CREATE`, `resolve_only` |
| `cap.group.create` | `messages.CreateChatRequest` | https://core.telegram.org/method/messages.createChat | creates an object | `CREATE`, `resolve_only` |
| `cap.group.delete` | `channels.DeleteChannelRequest` | https://core.telegram.org/method/channels.deleteChannel | destroys or converts an object | `DESTRUCTIVE_NONIDEMPOTENT`, `resolve_only` |
| `cap.group.delete` | `messages.DeleteChatRequest` | https://core.telegram.org/method/messages.deleteChat | destroys or converts an object | `DESTRUCTIVE_NONIDEMPOTENT`, `resolve_only` |
| `cap.group.migrate` | `messages.MigrateChatRequest` | https://core.telegram.org/method/messages.migrateChat | destroys or converts an object | `DESTRUCTIVE_NONIDEMPOTENT`, `resolve_only` |

**Absent in every phase:**
- `updates.GetDifferenceRequest`: 4b login no longer calls it.
- `auth.ResendCodeRequest` (and `auth.LogOutRequest` outside `admin.revoke`).
- `account.UpdatePasswordSettingsRequest`, `account.ConfirmPasswordEmailRequest`.
- `contacts.ResolveUsernameRequest`, `channels.GetChannelsRequest`.
- Every takeout request.
- `messages.SearchGlobalRequest`.
- `messages.ReadHistoryRequest` and the other read-acknowledge methods.
- `messages.GetMessagesViewsRequest`.

Transport requests Telethon issues on the sender directly (not through
`_call`): `InvokeWithLayer`, `InitConnection`, `InvokeWithoutUpdates`, `Ping`,
`help.GetConfig`, `auth.ExportAuthorization`, `auth.ImportAuthorization`.

## comms v0.3 D39-PRE (Task E10a): what Telethon sends on its own, and the update stream

The recorder sees only requests that pass through the adapter's `_call_reviewed`. Telethon
1.45.0 also sends requests itself. Reading `connect`, `_on_login`, `get_me`,
`is_user_authorized`, `set_receive_updates`, `_update_loop`, `_keepalive_loop` and
`_updates/messagebox.py` gives this set, pinned as `UPDATE_RPCS` and checked against the
installed Telethon source by `tests/telegram/test_update_rpcs.py`:

| Request | Sent by | What it reads | Effect at Telegram |
|---|---|---|---|
| `help.GetConfigRequest` (inside `InvokeWithLayer`/`InitConnection`) | `connect`, every connection | the DC configuration | none |
| `users.GetUsersRequest` | `connect` → `get_me`, when the saved update state is empty | the account's own user | none |
| `updates.GetStateRequest` | `connect` → `_on_login` (same condition); `is_user_authorized`; `set_receive_updates` | the account's update state (pts, qts, date, seq) | none |
| `updates.GetDifferenceRequest` | `connect` → `_on_login` (same condition); the update loop on a gap | updates missed since a state | none |
| `updates.GetChannelDifferenceRequest` | the update loop on a channel gap | a channel's missed updates | none |

The keepalive is the MTProto service ping (`_keepalive_ping`), a transport message with no
account effect.

**Correction to the sections above.** The "absent in every phase" list names
`updates.GetDifferenceRequest`, and the transport list omits `users.GetUsers` and
`updates.GetState`. Both are wrong for `connect`: whenever the session's saved update state is
empty (a fresh session file), Telethon's `connect` sends `get_me` and `_on_login`, which is
`users.GetUsers`, `updates.GetState` and `updates.GetDifference`, **whether or not updates are
received**. The 4b login path does not call `GetDifference` itself; `connect` does. All three
are reads of the account's own state.

**The update stream.** The comms daemon opts in (`TelegramConfig.receive_updates=True`); the
legacy construction stays `receive_updates=False`. With it, the update loop adds
`updates.GetDifference`/`GetChannelDifference` on a gap. One `events.Raw` handler translates
each update (`neutral_updates`) and hands it only to the sink of the single claimed owner
(A24); before a sink exists an update is dropped. The daemon runs Telethon on its own thread
and loop, and posts each batch to its own loop, where `UserUpdateConsumer` writes it (one
SQLite connection, one thread).
