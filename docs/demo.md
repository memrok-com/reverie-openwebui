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
