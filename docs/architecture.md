# Architecture

The project has two execution layers.

`build-coder.py` is the main orchestrator. It resolves the Coder ref, maintains
the Git cache, creates an isolated worktree, applies overrides, installs Go,
runs upstream Coder build commands, and validates the final Docker image.

`build-coder-in-docker.py` is an optional isolation layer. It builds the
linux/amd64 builder image and then runs `build-coder.py` inside that container.
This avoids Apple Silicon host toolchain issues while still using the host
Docker daemon through `/var/run/docker.sock`.

## Cache Layout

```text
.cache/
|-- coder-src/       # cached  coder/coder clone
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
Because those worktrees live under the project directory, `build-coder.py`
sets `GOFLAGS=-buildvcs=false` and relies on Coder's upstream ldflags for the
release version. This prevents Go from stamping the parent project's Git
revision into the Coder binary.

## Build Boundary

The project does not reimplement Coder's build logic. It prepares the
environment and then calls Coder's own Make targets:

```text
go mod download
make gen/mark-fresh
make build/coder_<version>_linux_amd64.tag
```

The final image is validated with Docker inspect and `/opt/coder version`.
