# Contributing to Reverie for Open WebUI

Keep stock Open WebUI unmodified. This repository owns the native Functions, installer and private sidecar; [Reverie](https://github.com/memrok-com/reverie) owns the memory engine. The [README](README.md) is for players and self-hosters, and [install and operations](docs/install.md) is for admins.

## Develop and test

`functions/` binds Open WebUI's saved chats to Reverie; `adapter.py` transports authenticated jobs to the engine over stdio. `install.py` renders and installs the native bundle. Use synthetic accounts, stories and tokens in tests and reports.

Run fast checks from the repository root with Python 3.12:

```sh
python3 -B -m unittest discover -s tests -v
git diff --check
```

The engine-backed check skips without a binary. On Linux amd64, use the same build and test path as CI (Docker and network access are needed for staging the pinned engine and pulling base images):

```sh
sh scripts/stage-engine.sh
docker build --network none --tag reverie-openwebui:local .
docker run --rm --network none --entrypoint python3 \
  --mount "type=bind,src=$PWD,dst=/tests-source,readonly" \
  --workdir /tests-source --env REVERIE_TEST_ENGINE=/probe/reverie-engine \
  reverie-openwebui:local -B -m unittest discover -s tests -v
```

Staging fetches the pinned Reverie commit, compiles with the pinned Rust builder using `cargo --locked`, and places the binary and verbatim upstream license and notice in `.stage/engine/`. None of those staged files belong in a commit. The runtime image needs no build tools or downloads at startup.

For changes to the user flow, exercise native Open WebUI controls using the [isolated synthetic demo](docs/demo.md), including an ordinary chat outside an enabled folder. Report what you exercised and distinguish synthetic transport checks from real-provider behavior. Never use the demo token or services for real chats.

## Changes and releases

Use a pull request with the behavior change, compatibility impact, and test evidence. Keep user instructions with the affected behavior; don’t put test and build steps in the README. Preserve chat ownership, the private folder boundary, and the distinction between ordinary chats and opted-in continuity.

PR and `main` CI build and test with the real pinned engine. The versioned-release workflow requires a `vMAJOR.MINOR.PATCH` tag on `main` ancestry that matches the Function bundle version. Use a new version: never move a release tag or overwrite its image. The workflow builds and tests before pushing the Linux amd64 image. There is no `latest` tag or automatic deployment. Verify anonymous acquisition by digest before linking the image from a GitHub release.
