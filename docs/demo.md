# Synthetic demo

`compose.yaml`, `demo/fixture.py` and `demo/flow.json` are isolated synthetic
assets, separate from the release installation path. Compose uses the pinned
stock Open WebUI 0.13.4 image, an internal network, no published ports, a
model-less deterministic fixture and a conspicuously synthetic-only shared
adapter token. Never reuse that token or feed real content to this fixture.

The demo requires a locally provisioned pinned engine via
`REVERIE_ENGINE_BINARY` and the UI image already present. It mounts demo sources
read-only. It is not production deployment configuration. The fixture emits
source-ID-grounded deterministic reflection JSON, supports `[fail-reflection]`,
and keeps bounded in-memory branch labels and hashes at `/evidence`.

A saved completed reply, explicit branch confirmation and verified save are
required. An unsent prompt or authored transcript cannot demonstrate memory
formation, provider behavior, or a completed-reply provenance record. Synthetic
checks do not establish model quality, discoverability or production readiness.

For the chat memory view, check **Memory for this chat** before memorizing,
after a published save (including an empty recollection result), after adding
later messages, and after selecting an alternate or editing through the saved
cutoff. Check the saved cutoff from its own button and then from the latest
reply on the same branch. On an alternate branch, use the latest reply on that
branch even if Open WebUI did not persist the branch selection. An older reply
above an ambiguous fork should ask you to use the latest reply instead.
Check an ordinary chat and another disposable owner's chat are refused, and
that inspecting causes no reflection, recall, provenance writes or cleanup.
For an existing v0.1.0 fixture, use the native single-Function addition and
preset append in the install guide; preserve all existing Functions, model
settings, chats and memory. The browser fixture and a compatible real engine
are required to establish native upgrade behavior beyond the offline contracts.
