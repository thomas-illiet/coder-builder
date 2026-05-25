# Coder Builder

![Custom Coder Builder hacker banner](docs/assets/banner.svg)

Coder Builder builds custom Docker images from the [coder/coder](https://github.com/coder/coder) repository while keeping Coder's upstream build path intact. It resolves a Coder ref, prepares an isolated worktree, applies optional file overrides, and builds validated Linux Docker images for the platforms you choose.

## Quick Start

Check the local Docker wrapper path first:

```bash
make doctor
```

Preview a build without cloning, building, or pushing:

```bash
make dry-run REF=latest-release TAG=test PLATFORM=linux
```

Build locally:

```bash
make build REF=latest-release TAG=dev PLATFORM=linux
```

Start the built image and verify Coder answers `/healthz`:

```bash
make smoke-run IMAGE=coder-custom TAG=dev
```

Build an ARM image:

```bash
make build REF=latest-release TAG=dev PLATFORM=arm
```

Publish multi-arch images to GHCR:

```bash
make push IMAGE=ghcr.io/OWNER/REPO/coder TAG=latest PLATFORM=all
```

## Platforms

`PLATFORM` selects Docker image targets:

| Value | Result |
| --- | --- |
| `linux` | Build `linux/amd64` as `<image>:v<version>-amd64`. |
| `arm` | Build `linux/arm64` as `<image>:v<version>-arm64`. |
| `all` | Build `linux/amd64` and `linux/arm64`; with `--push`, publish a multi-arch manifest. |

`arm` is a Linux Docker image target, not a `darwin_arm64` macOS build. To
limit the embedded slim CLI archive to Apple Silicon macOS, use
`EMBEDDED_OS_ARCHES=darwin_arm64`. Windows and Darwin are not Docker image
targets in upstream Coder; they require separate binary/archive workflows.

## Project Layout

```text
.
|-- Makefile                    # Friendly command surface
|-- scripts/
|   |-- build-coder.py           # Main build orchestrator
|   |-- build-coder-in-docker.py # linux/amd64 Docker wrapper
|   |-- smoke-run-coder.py       # Runtime startup smoke test
|   `-- doctor.py                # Dependency checker
|-- Dockerfile                   # Reusable builder image
|-- overrides/                   # Path-mirrored file overrides
|-- docs/                        # Project documentation
`-- .github/workflows/           # Smoke checks and GHCR publishing
```

## Documentation

- [Dependencies](docs/dependencies.md)
- [Build Guide](docs/build.md)
- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [Overrides](docs/overrides.md)
- [Build Workflow](docs/workflow.md)
