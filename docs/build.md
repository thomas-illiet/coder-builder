# Build Guide

## Recommended Build

Use the Makefile commands for normal work. They call the Docker wrapper, which
keeps the host setup small and consistent across macOS and Linux.

```bash
make doctor
make dry-run REF=latest-release TAG=test PLATFORM=linux
make build REF=latest-release TAG=dev PLATFORM=linux
make smoke-run IMAGE=coder-custom TAG=dev
```

The wrapper builds or reuses `Dockerfile`, then runs `scripts/build-coder.py`
inside a `linux/amd64` builder container.

## Local Startup Smoke Test

After a local build, run the same startup check used by CI for `linux/amd64`:

```bash
make smoke-run IMAGE=coder-custom TAG=dev
```

This starts temporary Postgres and Coder containers, waits for Coder's
`/healthz` endpoint to return `OK`, then cleans everything up. To keep Coder
running for manual UI testing, use:

```bash
make run-local IMAGE=coder-custom TAG=dev
```

## Platform Selection

`PLATFORM` controls the Docker image target:

```bash
make build PLATFORM=linux   # linux/amd64
make build PLATFORM=arm     # linux/arm64
make build PLATFORM=all     # linux/amd64 and linux/arm64
```

The underlying script accepts the same selection:

```bash
python3 scripts/build-coder-in-docker.py --ref latest-release --tag dev --platform arm
```

Windows is intentionally rejected for Docker images. Coder's upstream Docker
images are Linux-only; Windows would require a separate binary/archive flow.
On Linux hosts, ARM builds require Docker/QEMU support for `linux/arm64`.

## Push To A Registry

Push a single platform:

```bash
make push IMAGE=ghcr.io/OWNER/REPO/coder TAG=latest PLATFORM=linux
```

This publishes:

- `ghcr.io/OWNER/REPO/coder:v<version>-amd64`
- `ghcr.io/OWNER/REPO/coder:latest`

Push both supported platforms:

```bash
make push IMAGE=ghcr.io/OWNER/REPO/coder TAG=latest PLATFORM=all
```

This publishes:

- `ghcr.io/OWNER/REPO/coder:v<version>-amd64`
- `ghcr.io/OWNER/REPO/coder:v<version>-arm64`
- `ghcr.io/OWNER/REPO/coder:v<version>` as a multi-arch manifest
- `ghcr.io/OWNER/REPO/coder:latest` as a multi-arch manifest

Without `--push`, `PLATFORM=all` builds local architecture-specific images only.
Docker manifests require pushed source images.

## Direct Build

Use the main script directly only on Linux amd64 hosts with all native tools
installed:

```bash
make doctor-direct
python3 scripts/build-coder.py --ref latest-release --tag dev --platform linux
```

See [Dependencies](dependencies.md) for the native tool list.

## Troubleshooting

- Docker socket unreachable: start Docker Desktop or Docker Engine.
- `google/protobuf/timestamp.proto` missing in direct mode: install protobuf
  development headers, or use the Docker wrapper.
- Node version mismatch in direct mode: install Node.js 22, 23, or 24, or use
  the Docker wrapper.
- Slow first build: Go modules, pnpm packages, toolchains, and Coder worktrees
  are cached under `.cache`.
- Stale builder image: run `make rebuild-builder`.
