# Reverie for Open WebUI

Give a roleplay character a memory across saved chats. A private folder holds one continuity; each chat is a dated scene. Play scenes out of order: a reply can recall scenes you memorized at earlier story dates, not later ones. You decide what to memorize. Ordinary chats are unchanged.

**v0.1.1** · Stock Open WebUI **0.13.4** · Docker Compose on Linux amd64

## Get started

- **Open WebUI admins:** Follow [Install and operate Reverie](docs/install.md) to add the sidecar to your existing stack and grant a player access to **Reverie Companion**. You’ll need an OpenAI-compatible chat-completions endpoint for memory formation as well as a model for play.
- **Players:** If you can’t select **Reverie Companion** as a folder default, ask your admin to install it and grant your account access. Selecting it in a single chat doesn’t turn on memory.

## Try two scenes

1. Create a private top-level folder. In **Folder options → Edit → Advanced**, set **Reverie Companion** as the first default model.
2. Start a saved chat in that folder. Describe your character receiving a brass compass. After you send, enter a story date such as `2010-06-12`. A completed reply shows the date.
3. On that reply, choose **Memorize through this reply** and confirm. **Memorizing…** above the reply changes to a save result. If the save isn’t verified or you lose the result, check **Memory for this chat** before retrying. Saving a chat alone doesn’t create memory.
4. Under the latest completed reply, open **Memory for this chat** to see how far you memorized and what this chat contributed. A memorized scene can have no recollections; an edited or different branch may need re-memorizing.
5. Start a *new chat in the same folder*, set a later date such as `2011-06-12`, and ask what the character kept from the meeting. Open **What reached this reply?** under the new reply to check whether the compass scene was supplied to the model. The inspector, not whether the model mentions the compass, tells you what memory reached the reply.

## Current limits

Scenes are plain text, dates are fixed per chat, and memorizing is manual. If you edit or replace a memorized branch, play can pause until you memorize the desired reply again. Keep memorized chats private and in their original folder: archiving, moving, or sharing one can also pause memory.

Deleting a scene can invalidate later memory without changing old replies. Account deletion can leave residual memory if cleanup fails; this is not an assured-erasure setup.

There is no migration or restore tool, or automatic repair of a partial install. See [deletion and recovery](docs/install.md#scene-deletion-and-recovery) or [disable without losing data](docs/install.md#disable-without-deleting-data).

[Contributing](CONTRIBUTING.md) · [Apache-2.0](LICENSE)
