# Install and operate Reverie

## Before you start

- Use an existing **stock Open WebUI 0.13.4** Compose deployment on **Linux amd64**. Back up its data using your existing procedure. This is a clean add-on install: existing chats are fine; existing Reverie Functions or a preset from a preview are not. There is no migration, overwrite, or restore tool in v0.1.
- Have an authorized Open WebUI administrator token, the ID of a base model already available in Open WebUI, and the ID of the user who should see **Reverie Companion**. Use IDs, not display names; the installer grants preset read access only to the specified user.
- Have an OpenAI-compatible chat-completions URL, model ID, and provider key if required for **memory formation**. Open WebUI sends the selected scene text to that endpoint when a player memorizes a scene. The existing chat provider is separate and receives recalled context on later prompts. Use endpoints appropriate for that content.

### Find the setup values

| Value | Where to find it |
| --- | --- |
| `BASE_MODEL_ID` | While signed in to Open WebUI, open `/api/models` on your own instance. Use the desired model's `id` from `data`, not its display name. Give the player access to that model. |
| `USER_ID` | Have the player open `/api/v1/auths/` while signed in to their own Open WebUI account and copy **only** the `id` field. The response also contains their session token; do not share the response. |
| Administrator token | In your own signed-in Open WebUI administrator session, open `/api/v1/auths/`, check that `role` is `admin`, and use the `token` field at the hidden installer prompt. Don't save it to a file or send it to anyone. |
| `REVERIE_REFLECTION_URL` | Use the full endpoint reachable from the UI container, for example `https://models.example.com/v1/chat/completions`, not a `/v1` base URL. `REVERIE_REFLECTION_MODEL` is the ID at that endpoint. |

## 1. Add the sidecar

Use the image from the [versioned release](https://github.com/memrok-com/reverie-openwebui/releases). Prefer the published digest if pinning a deployment. Keep the sidecar unexposed: it needs only an internal Compose network shared with the UI, no published ports or reverse-proxy route.

Set `REVERIE_ADAPTER_TOKEN` to a fresh random token of at least 32 characters (`A–Z`, `a–z`, `0–9`, `_`, `-`), for example from `openssl rand -hex 32`. Supply it to both services through runtime configuration. It is neither the Open WebUI admin token nor the provider key. Keep all credentials out of source files, command arguments, and shared Compose output.

Merge this example into the **existing** Compose project. Replace `open-webui` with your UI service name throughout this guide. Retain its existing image, data volume, provider settings, and networks; add `reverie_internal` alongside them. The example assumes the UI already uses `default`.

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

Recreate the UI with the new environment and start the sidecar through your usual Compose process. For formation, use a model without extended reasoning, or set `REVERIE_REFLECTION_REASONING_EFFORT=none` on the UI service **if your endpoint supports it**. Reflection follows the server settings and refuses redirects. `/health` is liveness only; every job route is authenticated.

## 2. Install the native Functions and preset

Set `BASE_MODEL_ID` and `USER_ID` in your shell to the IDs from the prerequisites. Run the following in Bash from your Compose project. It copies the package to a temporary directory inside the UI, displays the bundle for review, and submits the administrator token only over stdin to the loopback API.

```bash
(
  set -euo pipefail
  : "${BASE_MODEL_ID:?Set the Open WebUI base model ID}"
  : "${USER_ID:?Set the target Open WebUI user ID}"
  package_dir=$(docker compose exec -T open-webui mktemp -d /tmp/reverie-package.XXXXXX)
  trap 'docker compose exec -T open-webui rm -rf -- "$package_dir"' EXIT
  docker compose exec -T reverie tar -C /package -cf - install.py functions |
    docker compose exec -T open-webui tar --no-same-owner -C "$package_dir" -xf -
  docker compose exec -T open-webui python3 -B "$package_dir/install.py" bundle \
    --base-model "$BASE_MODEL_ID" --user-id "$USER_ID"
  read -r -p 'Install this bundle? [y/N] ' install_confirm
  [[ "$install_confirm" == y ]] || exit 0
  read -rs -p 'Open WebUI administrator token: ' admin_token
  printf '\n'
  printf '%s\n' "$admin_token" | docker compose exec -T open-webui \
    python3 -B "$package_dir/install.py" apply --url http://127.0.0.1:8080 \
    --base-model "$BASE_MODEL_ID" --user-id "$USER_ID" --token-stdin
  unset admin_token
)
```

Success says `Created and verified four Functions, active Event, global Filter and Reverie Companion preset.` The installer checks the UI version, base model, and ID conflicts before writing. Creation is not atomic. If it fails, inspect any partial state in native Admin Functions and model administration before making changes; rerunning does not repair it.

## 3. Check your setup

As the granted user, follow [Try two scenes](../README.md#try-two-scenes). If you cancel the date prompt, use **Try Again** on the saved prompt. Then send a plain-text prompt in an ordinary chat outside the enabled folder: expect no date prompt or Reverie memory. Within an enabled folder, changing the chat's model keeps recall active but may hide the preset's reply Actions. A failed reflection is not a saved memory; an unchanged successful repeat reports already memorized.

## Scene deletion and recovery

- **Delete** a chat in Open WebUI to remove its episode from Reverie. This also invalidates later memory contributions and corrections in that continuity; earlier memory remains. Memorize surviving scenes again as needed. Old replies are not rewritten, and the inspector may no longer have verified records for earlier replies.
- **Archive, move, or share** does not forget the chat. If play is paused, unarchive the original chat if necessary, return it to its original folder (Open WebUI does not do this automatically), and make it private. To update a changed branch, explicitly memorize the desired reply again. Deleting a folder without its contents does not purge memory.
- Keep the **Reverie native deletion** Event active. Open WebUI delivers deletion events on a best-effort basis; missing scene cleanup is retried before memory use in a surviving continuity. Failed cleanup pauses memory use rather than treating a lookup failure as deletion. **Account deletion has no automatic retry if initial cleanup fails or is missed**, so residual memory can remain. Logical deletion does not guarantee erasure from database pages or backups; minimal ID-only records can remain for safe retries.

This alpha does not support shared or nested folders, attachments or tools in enabled chats, Continue, redating, or restoring chats and memory. Model quality and formation latency vary by provider; the synthetic demo does not establish production readiness.

## Disable without deleting data

1. Change enabled folders to an ordinary default model.
2. In native Admin Functions, disable **Reverie continuity**, **Reverie native deletion**, **What reached this reply?**, and **Memorize through this reply**. Detach the Actions from the preset or disable/remove the preset through model administration.
3. Verify an ordinary chat. Then remove the sidecar service and its dedicated network configuration, remove the Reverie environment entries from the UI, and recreate it normally without changing its image or existing data volume.
4. Keep both named volumes. The UI volume contains chats and Reverie records; `reverie_data` contains engine databases. Do **not** use `docker compose down --volumes` to disable Reverie.
