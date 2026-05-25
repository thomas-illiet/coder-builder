# Build Guide

## Recommended Build

Use the Docker wrapper when building from macOS or any host where local build
tools are not guaranteed to match Coder's expectations:

```bash
python3 build-coder-in-docker.py --ref latest-release --tag dev
```

The wrapper builds or reuses `Dockerfile.build-coder`, then runs the main build
inside a `linux/amd64` container.

## Direct Build

Use the main script directly only when the host has the required tools:

```bash
python3 build-coder.py --ref latest-release --tag dev
```

Required tools include Docker, Git, GNU Make, Node 22, Corepack, zstd, protoc,
and standard protobuf includes.

## Push To A Registry

```bash
python3 build-coder-in-docker.py \
  --ref latest-release \
  --image ghcr.io/OWNER/REPO/coder \
  --tag latest \
  --push
```

This publishes both:

- `ghcr.io/OWNER/REPO/coder:v<version>-amd64`
- `ghcr.io/OWNER/REPO/coder:latest`

## Dry Run

```bash
python3 build-coder-in-docker.py --dry-run --ref latest-release --tag test
```

Dry-run resolves the latest release and prints the build steps without building
or pushing images.

## Troubleshooting

- Docker socket unreachable: start Docker Desktop or Docker Engine.
- `google/protobuf/timestamp.proto` missing: rebuild the builder image with
  `--rebuild-builder`; the Dockerfile installs `libprotobuf-dev`.
- Node version mismatch: use the Docker wrapper. It pins `node:22-bookworm`.
- Slow first build: Go modules, pnpm packages, and Coder worktrees are cached
  under `.cache`.
