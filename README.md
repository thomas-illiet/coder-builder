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

Windows is not a Docker image target in upstream Coder. Windows support would be a separate binary/archive workflow, not an image build.

## Project Layout

```text
.
|-- Makefile                    # Friendly command surface
|-- scripts/
|   |-- build-coder.py           # Main build orchestrator
|   |-- build-coder-in-docker.py # linux/amd64 Docker wrapper
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
