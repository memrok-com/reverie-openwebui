# Open WebUI compatibility probe

This probe checks released artifacts in a disposable Linux/amd64 Docker project, never a deployed stack. A pull request checks an expected installer refusal twice and verifies the supported-pair control. On `main`, the probe resolves upstream `:latest` once and tests that exact digest against the pinned adapter.

To run the supported control on a disposable Linux Docker host with Python and the Compose plugin:

```sh
python3 -m venv /tmp/reverie-compat-venv
/tmp/reverie-compat-venv/bin/pip install 'playwright==1.65.2'
/tmp/reverie-compat-venv/bin/python -m playwright install --with-deps chromium
/tmp/reverie-compat-venv/bin/python -B compatibility/pilot.py --output /tmp/reverie-compat-result
```

`--output` must be a new directory outside the checkout. `--ui-digest` accepts a full SHA-256 digest from the Open WebUI GHCR repository. The probe binds the pulled amd64 image IDs to the running containers. The browser reaches the test UI from the Docker host across the project's internal bridge; no ports are published. It uses the released adapter and installer, not worktree Functions or a local engine build.

The probe creates synthetic admin and ordinary accounts, installs the preset, then drives stock Open WebUI in headless Chromium. It tests ordinary chat, a completed dated scene, explicit Memorize, and later recall. Pass requires both saved-chat/provenance records and provider evidence bound to the saved source; plausible reply text alone is insufficient. **It does not test an upgrade or replace first-use acceptance.**

| Exit | Meaning |
| --- | --- |
| `0` | Tested and passed the synthetic journey |
| `1` | The installer explicitly does not support the reported Open WebUI version, or a completed probe detected a contract mismatch |
| `2` | Indeterminate: infrastructure, setup, incomplete evidence, or cleanup failed |

`result.json` records artifact identities and the outcome. `evidence.json` includes synthetic identifiers, hashes, and flags, not tokens or transcripts. Only the generated project is cleaned up, including its two disposable volumes. If automatic cleanup fails, use the exact project and manifest in the report on the same host. Never provide live volumes, accounts or credentials.

## Confirmed-pair intake

- Scheduled discovery and issue writing are opt-in (`REVERIE_COMPAT_ARMED`). An incompatible pair needs two matching observations from fresh projects and a passing pinned control with the same adapter image and installer package. An indeterminate result fails the job without filing. An unsupported version is an installer refusal, not evidence of a runtime regression.
- Only trusted `main` runs may write an issue. The exact UI/adapter pair has one issue across reruns, including closed issues. The machine-generated issue body links the qualifying attempt; use comments for additional engineering findings. Keep the issue even if a notification fails. Compact proof artifacts expire after 90 days; if verification was missed beyond that window, re-qualify the pair before acting on it.
- `python3 -B -m compatibility.intake scan --state /private/path/intake.sqlite` is a read-only GitHub reconciliation against open issues, exact workflow attempts and their verified proof artifacts. It stores one local receipt per pair and prints only new receipts. Use a private directory; empty output is an idle sweep. The receipt is intake, not investigation, fix approval or deployment.
- The optional scheduled wake (`REVERIE_WAKE_ENABLED`) requires a `main`-restricted `reverie-compat-wake` GitHub environment and a separately scoped private receiver. Its job does not run candidate code or download probe artifacts; it sends a signed issue number. An accepted wake is not proof of pickup. Disable the wake without disabling issue filing or reconciliation. Neither path promotes or deploys an image.
