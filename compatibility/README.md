# Open WebUI compatibility probe

This probe checks released artifacts in a disposable Linux/amd64 Docker project, never a deployed stack. A pull request checks an expected installer refusal twice and verifies the supported-pair control. On `main`, scheduled and no-input manual runs resolve upstream `:latest` once and test that exact digest against the pinned adapter. Pinned manual runs test the supplied digest instead.

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
| `1` | The probe rejected a version outside the released installer's support gate, or detected a contract mismatch |
| `2` | Indeterminate: infrastructure, setup, incomplete evidence, or cleanup failed |

`result.json` records artifact identities and the outcome. `evidence.json` includes synthetic identifiers, hashes, and flags, not tokens or transcripts. Only the generated project is cleaned up, including its two disposable volumes. If automatic cleanup fails, use the exact project and manifest in the report on the same host. Never provide live volumes, accounts or credentials.

## Digest-bound report dispatch

The Actions workflow accepts two optional string inputs, required together for pinned testing:

- `ui_digest`: exactly `sha256:[a-f0-9]{64}` (full match), always in `ghcr.io/open-webui/open-webui`. Tags, repository names, uppercase hex and whitespace are rejected.
- `correlation`: exactly `[a-f0-9]{32,64}` (full match). Generate a fresh nonce per intended dispatch and reuse it when reconciling an ambiguous dispatch response.

The exact pinned run name is `compatibility-report-<correlation>`; the fixed prefix is `compatibility-report-`. Correlation is Actions run metadata, not a field in the report. Reconcile by the exact run name together with repository, workflow, dispatch event, branch and commit; multiple matching runs remain ambiguous. A rerun shares the run name but has its own attempt artifact. Invalid inputs fail before image resolution or probing and never fall back to latest or the refusal fixture.

Pinned dispatches are accepted on branches in the canonical repository, including `main`. A PR branch may be selected for review by dispatching that branch explicitly; this does not change the `pull_request` fixture. The probe job has only `contents: read`, checkout persists no credentials, and the issue writer is excluded whenever either input is present, independently of `REVERIE_COMPAT_ARMED`. There is no publishing, promotion or deployment. A branch report is review evidence, not a main-branch attestation.

Pinned testing uses only the supplied immutable candidate digest, the pinned adapter and supported control; it never resolves `:latest`. A determinate result uploads only `report.json` in `compatibility-report-${{ github.run_attempt }}` (90-day retention). The exact schema is:

```json
{"schema":1,"ui_digest":"sha256:<64 lowercase hex>","adapter_digest":"sha256:<64 lowercase hex>","installer":"supported","runtime":"pass","check":"tested/pass"}
```

`installer` is `supported` or `unsupported-version`. `runtime` is `pass`, `failed`, or `not-tested`. `check` is `tested/pass` for success, otherwise an existing `compatibility/intake.py` `FINDINGS` code. An unsupported version is precisely `installer=unsupported-version`, `runtime=not-tested`, `check=unsupported-version`: the released installer's gate refused installation; runtime compatibility is unknown and the gate is never bypassed. `released-installer-readback` means the version gate accepted the version but installation verification failed (`supported`, `not-tested`). Journey failures use `supported`, `failed` with the matching check code.

Success requires a validated pilot result with pinned image identities, ready bootstrap, the synthetic journey pass check and completed cleanup. Every negative report uses the existing qualifier: two matching observations from distinct projects and a green supported control, with matching adapter image and installer package evidence. Missing or mismatched evidence, a failing supported baseline, probe/setup errors and cleanup failures are indeterminate: the job fails and emits no verdict artifact. Reports cover synthetic fresh installs only.

No-input manual dispatches, PR fixtures, opt-in scheduled discovery, and legacy proof artifacts and issue intake retain their existing behavior. Report artifacts are separate from legacy `compatibility-proof-<attempt>` artifacts and are never issue-writing proof.

## Confirmed-pair intake

- Scheduled discovery and issue writing are opt-in (`REVERIE_COMPAT_ARMED`). An incompatible pair needs two matching observations from fresh projects and a passing pinned control with the same adapter image and installer package. An indeterminate result fails the job without filing. If the reported version is not `0.13.4`, the probe stops at the released installer's version gate, before installation. That outcome does not tell us whether the adapter would work if the gate were lifted.
- Only `main` runs in this repository may write an issue. The full digest pair in the machine marker prevents duplicate filing even after a title or body edit or closure. If the marker is unreadable, the original title conservatively reserves its digest prefixes only. Removing both identity cues (or deleting the issue) defeats issue-based deduplication. Leave the marker and canonical text intact and use comments for triage.
- A consumer polls GitHub; there is no inbound webhook or network credential for CI. Run `python3 -B -m compatibility.intake scan --state /private/path/intake.sqlite` from a reviewed checkout, with read access to issues and Actions artifacts. It requires the original canonical title and body and independently verifies the exact CI attempt before recording a receipt. Missing, expired or invalid proof skips that issue without blocking others; transport and authentication failures still fail the sweep. Proof expires after 90 days; obtain fresh evidence before acting on an unverified issue. Open, verified issues remain pending until explicitly acknowledged. `--gate` emits only typed evidence and a `wakeAgent` flag; idle scans do not start triage.
- After recording triage durably, run `python3 -B -m compatibility.intake ack --state /private/path/intake.sqlite --issue NUMBER`. This operation is local and idempotent. If the consumer stops before acknowledging, the next sweep returns the same issue; check for an existing triage record before repeating a handoff. Keep the ledger in a private directory. Acknowledgment does not close an issue, approve a fix, or promote an image.
