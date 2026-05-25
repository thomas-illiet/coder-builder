# Architecture

The project has three user-facing layers.

The root `Makefile` is the friendly command surface. It keeps common workflows
short: dependency checks, dry-runs, builds, pushes, runtime smoke tests, and
builder image refreshes.

`scripts/build-coder.py` is the main orchestrator. It resolves the Coder ref,
maintains the Git cache, creates an isolated worktree, applies overrides,
installs Go, runs upstream Coder build commands, and validates final Docker
images.

`scripts/build-coder-in-docker.py` is the isolation layer. It builds or reuses
the `linux/amd64` builder image and then runs `scripts/build-coder.py` inside
that container. This avoids Apple Silicon host toolchain issues while still
using the host Docker daemon through `/var/run/docker.sock`.

`scripts/smoke-run-coder.py` is the runtime validation layer. It starts
temporary Postgres and Coder containers, checks the Coder `/healthz` endpoint,
and removes the temporary Docker resources unless manual inspection is requested.

## Platform Model

Coder Builder supports Linux Docker image platforms:

- `linux` maps to `linux/amd64`.
- `arm` maps to `linux/arm64`.
- `all` builds both supported platforms.

Upstream Coder Docker images are Linux-only. Windows is not an image platform
for this project; it would require a separate binary/archive workflow. Darwin
targets are handled the same way: useful for embedded slim CLI binaries, but not
valid as Docker image platforms.

For a single platform, the build validates the local image, applies the optional
alias, and optionally pushes both tags. For `all`, the build creates two
architecture-specific images. With `--push`, it pushes both images and then
creates multi-arch manifests for the versioned tag and optional alias.

## Cache Layout

```text
.cache/
|-- coder-src/       # cached coder/coder clone
|-- worktrees/       # one isolated worktree per selected ref
|-- toolchains/      # downloaded Go toolchains
|-- go-build/        # Go build cache
|-- go/pkg/mod/      # Go module cache
|-- pnpm-store/      # pnpm store
|-- corepack/        # Corepack managed pnpm versions
|-- npm/             # npm cache used by Corepack/pnpm
`-- xdg-cache/       # XDG cache for Node tooling
```

The cached clone is never used directly for a build. Builds happen in detached
worktrees so generated files and overrides do not pollute the source cache.
Because those worktrees live under the project directory, `scripts/build-coder.py`
sets `GOFLAGS=-buildvcs=false` and relies on Coder's upstream ldflags for the
release version.

## Build Boundary

The project does not reimplement Coder's build logic. It prepares the
environment and then calls Coder's own Make targets:

```text
go mod download
make gen/mark-fresh
make OS_ARCHES=linux_amd64 build/coder_<version>_linux_amd64.tag
make OS_ARCHES=linux_arm64 build/coder_<version>_linux_arm64.tag
```

`OS_ARCHES` defaults to the image platform being built, which avoids building
every Coder release binary for Docker-only builds. Set `EMBEDDED_OS_ARCHES=all`
to keep upstream's full embedded release-binary set.

Each final image is validated with Docker inspect and `/opt/coder version`.
The GitHub workflow also starts the `linux/amd64` image and verifies `/healthz`
before pushing it.
