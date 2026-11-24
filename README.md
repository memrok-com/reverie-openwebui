# Reverie for Open WebUI v0.1.0

Native Functions and a companion preset for stock **Open WebUI 0.13.4**, backed
by a private Reverie sidecar. This initial release supports explicit memory
formation from saved plain-text scenes and recall within an owned, unshared,
top-level folder. It is an early, bounded-data integration; it is not a general
memory system or a deployment manager.

Only a folder whose first default model is **Reverie Companion** enables memory.
Choosing the preset in an ordinary chat does not enable it. The global Filter
returns ordinary chats unchanged, including when the sidecar token is absent.
The preset's two Actions explain their scope outside enabled folders.

## Add to an existing installation

Use an existing stock Open WebUI **0.13.4** Compose installation. Back up its data
using your existing procedure. The installer supports only clean creation of
previously absent Reverie IDs; it has no overwrite, update or version bypass.
Review the generated bundle before granting an authorized administrator token.

Supply a strong, independently generated opaque token of at least 32 URL-safe
ASCII characters (`A-Z`, `a-z`, `0-9`, `_`, `-`) through the operator's runtime
environment as `REVERIE_ADAPTER_TOKEN`. Set the same value in both services;
there is no default or unauthenticated mode. Never put it in Function source,
client valves, command arguments, version control or a shared Compose dump.
The token is server-to-server authorization, separate from UI administrator and
reflection-provider credentials. Network isolation is still required.

Merge this fragment into your existing Compose configuration, preserving the
UI's existing networks, volumes, image and provider settings. `open-webui` below
is the existing UI service name. The sidecar publishes no ports and joins only
the dedicated internal network. No installer mounts or Docker socket are needed.

```yaml
services:
  open-webui:
    environment:
      REVERIE_ADAPTER_URL: http://reverie:8766
      REVERIE_ADAPTER_TOKEN: ${REVERIE_ADAPTER_TOKEN:?Supply runtime adapter token}
      REVERIE_REFLECTION_URL: ${REVERIE_REFLECTION_URL:?Supply chat-completions endpoint}
      REVERIE_REFLECTION_MODEL: ${REVERIE_REFLECTION_MODEL:?Supply reflection model}
      REVERIE_REFLECTION_API_KEY: ${REVERIE_REFLECTION_API_KEY:-}
    networks: [default, reverie_internal]
  reverie:
    image: ghcr.io/memrok-com/reverie-openwebui:v0.1.0
    environment:
      REVERIE_ADAPTER_TOKEN: ${REVERIE_ADAPTER_TOKEN:?Supply runtime adapter token}
    user: '10001:10001'
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    pids_limit: 64
    mem_limit: 256m
    cpus: 0.5
    volumes: [reverie_data:/data]
    networks: [reverie_internal]
networks:
  reverie_internal:
    internal: true
volumes:
  reverie_data:
```

Recreate the UI with these environment settings and start the sidecar through
your normal Compose process. Reflection uses the configured server endpoint and
model; no request field overrides them. Use a model without extended reasoning,
or set `REVERIE_REFLECTION_REASONING_EFFORT=none` in the UI when the endpoint
supports it. Redirects are refused. The adapter token is never used for reflection.
`GET /health` is unauthenticated liveness only; every job route requires the token.

Copy the packaged installer and Function sources through Compose, then run the
installer **inside the UI** against loopback. These Bash commands use a
temporary directory owned by the UI's container user, without host binds:

```bash
set -o pipefail
package_dir=$(docker compose exec -T open-webui mktemp -d /tmp/reverie-package.XXXXXX)
docker compose exec -T reverie tar -C /package -cf - install.py functions |
  docker compose exec -T open-webui tar --no-same-owner -C "$package_dir" -xf -
# BASE_MODEL_ID must already be available; USER_ID receives preset read access.
docker compose exec -T open-webui python3 -B "$package_dir/install.py" bundle \
  --base-model "$BASE_MODEL_ID" --user-id "$USER_ID"
# Review the bundle, then enter an authorized administrator token without echo.
read -rs -p 'Open WebUI administrator token: ' admin_token
printf '\n'
printf '%s\n' "$admin_token" | docker compose exec -T open-webui \
  python3 -B "$package_dir/install.py" apply --url http://127.0.0.1:8080 \
  --base-model "$BASE_MODEL_ID" --user-id "$USER_ID" --token-stdin
unset admin_token
docker compose exec -T open-webui rm -rf -- "$package_dir"
```

The installer checks exact `/api/version`, all Function/model ID conflicts and
base-model availability before writes. It creates four Functions, activates the
Filter globally, the native deletion Event and the two preset Actions, and
creates the companion preset with explicit user read access. It reads back
Function content and activation, preset fields and model discovery. It refuses
redirects, non-loopback URLs and conflicts. It never logs credentials or response
bodies. Creation is not atomic:
if any step fails, inspect the partial state and stop; rerunning cannot repair it.
There is no automatic rollback or restore tool.

## Use and verify

Create a private top-level folder. In **Folder options → Edit → Advanced**, set
**Reverie Companion** as its first default model. A per-chat selection does not
opt a folder in. Each user/folder pair has separate continuity.

Send a saved plain-text prompt. The first scene asks for a `YYYY-MM-DD` story
date after Send. Blank or Cancel stops the reply; use **Try Again** on the saved
prompt. Invalid dates get at most three attempts. Dates are fixed per chat; use
separate chats for different dates. A completed reply shows the native story-date
status. Automatic title changes are not implemented; rename chats manually if
you want date-prefixed sorting.

Use **Memorize through this reply** on a completed reply and confirm the selected
branch. Only that prefix is source material. Wait for the verified save status;
unchanged repeats report already memorized. Reflection failure never reports a
successful save. Changed published source branches block further play until
explicitly memorized again. **What reached this reply?** shows structured recall
and authorized source titles/dates for a verified completed reply.

Use Open WebUI’s native **Delete** once to remove a scene’s Reverie episode and
invalidate later memory contributions and corrections. Cleanup calls the existing
engine forget operation without a reflection provider. Unaffected earlier memory
remains; surviving scenes can be memorized again. Already-written replies stay
unchanged. Cached recall provenance for the affected character is discarded
conservatively, so the inspector may show no verified record for surviving replies.
Their source anchors remain available for branch verification and re-memorizing.

The active Event handles single-chat, bulk-chat, folder-content and account deletion.
Events are best effort in Open WebUI; an owner/character/chat registry in
`reverie.sqlite`, written before the first date or formation RPC, also reconciles
confirmed missing scenes before memory use. Presence checks select only registered
chat IDs bound to their owners. Database errors never count as deletion. Failed
cleanup pauses memory use and retries next time in a surviving continuity, including
after a restart or an interrupted event. Concurrent operations are checked again
before memory is used; a changed continuity requires a retry. Keep the Event active
for prompt cleanup. If an account-deletion event is missed or cannot finish, there
is no next use by that account to retry cleanup. v0.1 has no background sweep;
residual memory may remain.

**Keep surviving memorized chats in their original private folder.** Archive,
move and share are reversible and do not trigger forget. They can pause the
continuity until the original chat is unarchived if needed, returned to its
original folder, and made private. Open WebUI does not restore folders on unarchive.
Changed branches can be explicitly memorized again. Folder deletion without
contents does not purge memory; surviving chats remain subject to the scope guard.

Deletion removes logical engine memory and local provenance, with minimal ID-only
registry tombstones retained for safe retry. It does not promise physical erasure
from database pages or backups, rewrite old upstream replies, or touch other
continuities. This installer supports clean installation only: unknown legacy source
anchors fail closed; migration from private preview releases is not supported.

After installation, check package readback success, a completed scene and explicit
save in an enabled folder, and an ordinary chat outside enabled folders. The
ordinary chat must receive no memory changes. Switching only the per-chat model
inside an enabled folder keeps recall enabled but may hide preset Actions.

Nested/shared folders, attachments, tools, Continue, moved/copied chats, schema
migration, redating and restore are outside v0.1. Records live
in the UI data volume; engine databases live in `reverie_data`. Keep both volumes.
The synthetic demo does not establish real-provider quality, whether memory
formation fits the adapter's wait limit on your provider, or production
readiness. Unsent prompts have no completed saved reply or verified provenance;
a preview or demo transcript is not evidence of an actual save.

## Disable and remove

First change enabled folders to an ordinary default model. In native Admin
Functions, disable the global **Reverie continuity** Filter and deactivate the
**Reverie native deletion** Event and the
**What reached this reply?** and **Memorize through this reply** Actions. Remove
their attachments from the companion preset or disable/remove that preset in
native model administration. Verify an ordinary chat, then stop and remove the
sidecar service and its dedicated network configuration. Remove the adapter
environment entries from the UI and recreate it normally. Preserve both the UI
and Reverie named volumes; do not use `down --volumes`. No data migration or
provider operation is performed by removal.

## Build and checks

The runtime Dockerfile uses pinned Python 3.12 on Debian bookworm, UID/GID 10001,
and an owned writable `/data`. Its default entrypoint supports a read-only root
filesystem, never downloads dependencies and never compiles at runtime. Build
from the repository root with these externally verified, native Linux amd64 files:

```text
.stage/engine/reverie-engine
.stage/engine/LICENSE
.stage/engine/NOTICE
```

The engine must come from `memrok-com/reverie` commit
`d679b6fc8cdcd329946f5dce74dc0610319b7f0f` (v0.3.0), built with the pinned
Rust 1.99.1 bookworm builder. Stage exact upstream LICENSE and NOTICE alongside
the binary; they are distributed under `/package/engine/` in the image. No engine
binary is committed. `.dockerignore` permits only package inputs and these three
staged files. Build with `docker build -t reverie-openwebui:local .`.

CI's `scripts/stage-engine.sh` fetches that exact commit, verifies HEAD, mounts
source read-only, builds locked in a bounded disposable Docker container and
stages the binary and upstream attribution. It supports native Linux amd64 only.
PR/main CI builds the runtime image and runs tests with the real engine. Stable
semver tags matching the embedded bundle version and contained in main can
publish only their versioned GHCR tag after tests. There is no `latest`, scheduled
workflow or external deployment; GitHub releases can be created separately.

```sh
python3 -B -m unittest discover -s tests -v
# With an already provisioned pinned engine:
REVERIE_TEST_ENGINE=/path/to/reverie-engine python3 -B -m unittest discover -s tests -v
git diff --check
```

Tests include synthetic Function/stdio contracts and local HTTP auth regressions.
The real-engine test skips when no binary is provided. See [demo notes](docs/demo.md)
for the isolated synthetic fixture. Adapter sources are licensed under
[Apache-2.0](LICENSE); bundled upstream engine attribution is retained verbatim.
