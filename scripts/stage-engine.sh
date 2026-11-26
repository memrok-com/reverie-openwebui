#!/bin/sh
# CI-only native Linux amd64 build; runtime images never fetch or compile.
set -eu
revision=ca969c4e64bae35f9d1ea92df53535be85a6658e
builder=docker.io/library/rust:1.99.1-bookworm@sha256:e6f2cff35d0f309acf8433b4f05878ed77092602c5065cc81801177da0bb3a9c
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ]
[ "$(docker info --format '{{.Architecture}}')" = x86_64 ]
[ ! -e .stage/engine ] || { echo 'Engine staging destination already exists.' >&2; exit 1; }
work=$(mktemp -d)
container=
cleanup() {
    if [ -n "$container" ]; then docker rm -f "$container" >/dev/null; fi
    rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
git init -q "$work/source"
git -C "$work/source" fetch --depth=1 https://github.com/memrok-com/reverie.git "$revision"
git -C "$work/source" checkout --detach FETCH_HEAD
[ "$(git -C "$work/source" rev-parse HEAD)" = "$revision" ]
[ -f "$work/source/engine/Cargo.toml" ] && [ -f "$work/source/engine/Cargo.lock" ]
mkdir "$work/target"
container=$(docker create --platform linux/amd64 --memory 4g --memory-swap 4g --cpus 2 --pids-limit 256 \
    --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges:true \
    --mount "type=bind,src=$work/source,dst=/src,readonly" \
    --mount "type=bind,src=$work/target,dst=/target" \
    --workdir /src/engine --env CARGO_TARGET_DIR=/target --env CARGO_HOME=/target/cargo --env CARGO_BUILD_JOBS=2 \
    --env RUSTUP_TOOLCHAIN=1.99.1 "$builder" \
    cargo build --locked --release --bin reverie-engine)
docker start -a "$container"
[ "$(docker inspect --format '{{.State.ExitCode}}' "$container")" = 0 ]
git -C "$work/source" diff --exit-code HEAD
mkdir -p .stage/engine
install -m 0555 "$work/target/release/reverie-engine" .stage/engine/reverie-engine
cp "$work/source/LICENSE" "$work/source/NOTICE" .stage/engine/
