# Open WebUI compatibility probe

This probe checks released artifacts in a disposable Linux/amd64 Docker project. It does not run against a deployed stack. GitHub Actions runs a supported-pair control and an older image that the released installer does not support. The workflow has no schedule, issue writes or deploy step.

To run the supported control on a disposable Docker host with Python and Compose v2:

```sh
python3 -m venv /tmp/reverie-compat-venv
/tmp/reverie-compat-venv/bin/pip install 'playwright==1.65.2'
/tmp/reverie-compat-venv/bin/python -m playwright install --with-deps chromium
/tmp/reverie-compat-venv/bin/python -B compatibility/pilot.py --output /tmp/reverie-compat-result
```

`--output` must be a new directory outside the checkout. `--ui-digest` accepts a full SHA-256 digest from the Open WebUI GHCR repository. The probe binds the pulled amd64 image IDs to the running containers. It uses the released adapter and installer, not worktree Functions or a local engine build.

The probe creates synthetic admin and ordinary accounts, installs the preset, then drives stock Open WebUI in headless Chromium. It tests ordinary chat, a completed dated scene, explicit Memorize, and later recall. Pass requires both saved-chat/provenance records and provider evidence bound to the saved source; plausible reply text alone is insufficient. **It does not test an upgrade or replace first-use acceptance.**

| Exit | Meaning |
| --- | --- |
| `0` | Tested and passed the synthetic journey |
| `1` | The installer explicitly does not support the reported Open WebUI version, or a completed probe detected a contract mismatch |
| `2` | Indeterminate: infrastructure, setup, incomplete evidence, or cleanup failed |

`result.json` records artifact identities and the outcome. `evidence.json` includes synthetic identifiers, hashes, and flags, not tokens or transcripts. Only the generated project is cleaned up, including its two disposable volumes. If automatic cleanup fails, use the exact project and manifest in the report on the same host. Never provide live volumes, accounts or credentials.
