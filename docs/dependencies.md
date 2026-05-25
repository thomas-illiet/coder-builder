# Dependencies

Coder Builder has two dependency profiles:

- Docker wrapper: recommended for macOS and for most Linux hosts.
- Direct build: supported for Linux amd64 hosts with native build tools installed.

Run the wrapper check before building:

```bash
make doctor
```

Run the direct host check only if you intentionally want to bypass the Docker wrapper:

```bash
make doctor-direct
```

## macOS

Use the Docker wrapper on macOS. The wrapper runs the build in a `linux/amd64`
builder container and talks to the host Docker daemon through
`/var/run/docker.sock`.

Required host dependencies:

- Python 3.10 or newer.
- Docker Desktop with the daemon running.

Recommended commands:

```bash
make doctor
make dry-run REF=latest-release TAG=test PLATFORM=linux
make build REF=latest-release TAG=dev PLATFORM=linux
```

For Apple Silicon, keep the wrapper path. The host does not need Node, GNU Make,
zstd, protoc, or protobuf headers because those are installed in the reusable
builder image.

## Linux With Docker Wrapper

The wrapper is also the simplest Linux path when you do not want to manage the
full Coder build toolchain on the host.

Required host dependencies:

- Python 3.10 or newer.
- Docker Engine with the daemon running.

Recommended commands:

```bash
make doctor
make build REF=latest-release TAG=dev PLATFORM=linux
make build REF=latest-release TAG=dev PLATFORM=arm
```

Use `PLATFORM=all` when publishing both `linux/amd64` and `linux/arm64`:

```bash
make push IMAGE=ghcr.io/OWNER/REPO/coder TAG=latest PLATFORM=all
```

For `PLATFORM=arm` or `PLATFORM=all` on Linux, Docker must be able to build and
run `linux/arm64` containers. The GitHub workflow enables QEMU automatically;
local Linux hosts may need binfmt/QEMU support configured separately.

## Linux Direct Build

Use direct builds only on Linux amd64 hosts where the native toolchain is under
your control.

Required host dependencies:

- Python 3.10 or newer.
- Git.
- GNU Make available as `make`.
- Docker Engine with the daemon running.
- Node.js 22, 23, or 24.
- Corepack.
- zstd.
- protoc.
- Standard protobuf includes, including `google/protobuf/timestamp.proto`.
- tar.

On Debian or Ubuntu, the native dependencies are typically:

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  ca-certificates \
  docker.io \
  git \
  make \
  nodejs \
  protobuf-compiler \
  libprotobuf-dev \
  python3 \
  tar \
  zstd
```

Node.js from the OS package manager may be too old. If `make doctor-direct`
reports a Node version error, install Node.js 22 through your preferred Node
distribution channel and run:

```bash
corepack enable
```

Direct build example:

```bash
python3 scripts/build-coder.py --ref latest-release --tag dev --platform linux
```

## Windows

Windows is not supported as a Docker image platform for Coder Builder. Upstream
Coder builds Linux Docker images and separate Windows binaries/archives. This
project rejects `--platform windows` for image builds so the failure mode is
clear.
