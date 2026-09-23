"""The independent read-state witness (spec §38.3), run as its own process.

It opens B's session (never A's) and prints, as JSON, three markers that
Telegram reports from B's side, which the gateway process cannot forge:

- ``dm_outbox``: B's ``read_outbox_max_id`` in the DM with A. It moves if A reads B's messages.
- ``group_outbox``: the same for the basic group. C stays idle for the whole window.
- ``channel_views``: the view counter of B's channel post. It moves only on
  ``GetMessagesViews``, which the gateway never sends.
"""

import asyncio
import json
import sys

from telethon import TelegramClient, functions, types


async def main(
    path: str, api_id: int, api_hash: str, a_id: int, group_id: int, channel_id: int, post_id: int
) -> None:
    client = TelegramClient(path, api_id, api_hash, receive_updates=False)
    await client.connect()
    a = await client.get_input_entity(a_id)
    group = await client.get_input_entity(-group_id)
    channel = await client.get_input_entity(int(f"-100{channel_id}"))
    dialogs = await client(
        functions.messages.GetPeerDialogsRequest(
            peers=[types.InputDialogPeer(a), types.InputDialogPeer(group)]
        )
    )
    outbox = {type(d.peer).__name__: d.read_outbox_max_id for d in dialogs.dialogs}
    posts = await client(
        functions.channels.GetMessagesRequest(channel, [types.InputMessageID(post_id)])
    )
    print(
        json.dumps(
            {
                "dm_outbox": outbox["PeerUser"],
                "group_outbox": outbox["PeerChat"],
                "channel_views": posts.messages[0].views,
            }
        )
    )
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), sys.argv[3], *map(int, sys.argv[4:8])))
