# Configuration

## Make Variables

The recommended command surface is the root `Makefile`:

```bash
make build REF=latest-release IMAGE=coder-custom TAG=dev PLATFORM=linux
```

Common variables:

- `REF`: Coder Git ref. Defaults to `latest-release`.
- `IMAGE`: Docker repository without a tag. Defaults to `coder-custom`.
- `TAG`: Optional alias. Defaults to `dev` in Makefile commands.
- `PLATFORM`: Docker image target. Defaults to `linux`.
- `PYTHON`: Python interpreter. Defaults to `python3`.
- `BUILDER_IMAGE`: Local reusable builder image tag.
- `SMOKE_IMAGE_REF`: Full image tag tested by `make smoke-run`. Defaults to
  `<IMAGE>:<TAG>`.
- `SMOKE_PLATFORM`: Docker platform used by the smoke-run Coder container.
  Defaults to `linux/amd64`.
- `SMOKE_PORT`: Optional host port for local testing. Defaults to an automatic
  free port.
- `SMOKE_TIMEOUT`: Startup timeout in seconds. Defaults to `180`.

`PLATFORM` accepts:

- `linux`, `amd64`, or `linux/amd64` for `linux/amd64`.
- `arm`, `arm64`, or `linux/arm64` for `linux/arm64`.
- `all` for both supported platforms.

## Main Build Script

`scripts/build-coder.py` owns the full build:

```bash
python3 scripts/build-coder.py \
  --ref latest-release \
  --image coder-custom \
  --tag dev \
  --platform linux
```

Important options:

- `--ref`: Coder Git ref. Defaults to `latest-release`.
- `--image`: Docker repository without a tag. Defaults to `coder-custom`.
- `--tag`: Optional alias. A bare value becomes `<image>:<value>`.
- `--platform`: `linux`, `arm`, or `all`, plus explicit Docker platform aliases.
- `--push`: Push built images after validation.
- `--build-base`: Build Coder's `scripts/Dockerfile.base` locally.
- `--overrides-dir`: Directory containing path-mirrored overrides.
- `--dry-run`: Print the plan without mutating Docker or the repo cache.

Windows values are rejected because this script builds Docker images only.

## Docker Wrapper

`scripts/build-coder-in-docker.py` wraps the main build in a `linux/amd64`
builder:

```bash
python3 scripts/build-coder-in-docker.py --ref latest-release --tag dev --platform arm
```

Wrapper-specific options:

- `--builder-image`: Local tag for the reusable builder image.
- `--rebuild-builder`: Rebuild the builder image before running.
- `--dry-run`: Show wrapper commands and the delegated build dry-run.

All other options are passed to `scripts/build-coder.py`.

## Startup Smoke Test

`scripts/smoke-run-coder.py` starts a built Coder image with an ephemeral
Postgres container and validates that `/healthz` returns `OK`:

```bash
python3 scripts/smoke-run-coder.py --image-ref coder-custom:dev
```

Important options:

- `--image-ref`: Full Docker image reference to test, including tag.
- `--platform`: Docker platform for the Coder container. Defaults to
  `linux/amd64`.
- `--port`: Optional host port. Defaults to an automatic free port.
- `--timeout`: Seconds to wait for startup. Defaults to `180`.
- `--keep-running`: Leave containers running after a successful check.

## Environment Variables

- `CODER_CACHE_DIR`: Override the cache directory. Default: `.cache`.
- `CODER_BUILDER_IMAGE`: Override the default builder image tag.
- `GITHUB_TOKEN`: Used by `scripts/build-coder.py` for GitHub release API requests when present.
- `DOCKER_CONFIG`: Honored by Docker for registry credentials.

The script sets Go, Corepack, npm, pnpm, and XDG cache variables during the
build so dependency downloads stay inside `CODER_CACHE_DIR`. It also adds
`-buildvcs=false` to `GOFLAGS` unless you already provided a `-buildvcs=` value.

## Docker Tags

Architecture-specific primary tags are always versioned:

```text
<image>:v<coder-version>-amd64
<image>:v<coder-version>-arm64
```

Single-platform builds tag the optional alias locally and push it when `--push`
is set:

```text
<image>:latest
```

`--platform all --push` creates and pushes multi-arch manifests for:

```text
<image>:v<coder-version>
<image>:latest
```
