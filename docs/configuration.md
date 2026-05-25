# Configuration

## CLI Options

`build-coder.py` owns the full build:

```bash
python3 build-coder.py --ref latest-release --image coder-custom --tag dev
```

Important options:

- `--ref`: Coder Git ref. Defaults to `latest-release`.
- `--image`: Docker repository without a tag. Defaults to `coder-custom`.
- `--tag`: Optional alias. `dev` becomes `coder-custom:dev`.
- `--push`: Push the versioned image and alias after validation.
- `--build-base`: Build Coder's `scripts/Dockerfile.base` locally.
- `--overrides-dir`: Directory containing path-mirrored overrides.
- `--dry-run`: Print the plan without mutating Docker or the repo cache.

`build-coder-in-docker.py` wraps the main build in a linux/amd64 builder:

```bash
python3 build-coder-in-docker.py --ref latest-release --tag dev
```

Wrapper-specific options:

- `--builder-image`: Local tag for the reusable builder image.
- `--rebuild-builder`: Rebuild the builder image before running.
- `--dry-run`: Show wrapper commands and the delegated build dry-run.

All other options are passed to `build-coder.py`.

## Environment Variables

- `CODER_CACHE_DIR`: Override the cache directory. Default: `.cache`.
- `CODER_BUILDER_IMAGE`: Override the default builder image tag.
- `GITHUB_TOKEN`: Used by `build-coder.py` for GitHub release API requests when present.
- `DOCKER_CONFIG`: Honored by Docker for registry credentials.

The script sets Go, Corepack, npm, pnpm, and XDG cache variables during the
build so dependency downloads stay inside `CODER_CACHE_DIR`.

## Docker Tags

The primary image is always versioned and architecture-specific:

```text
<image>:v<coder-version>-amd64
```

If `--tag latest` is used, the script also creates:

```text
<image>:latest
```

With `--push`, both tags are pushed.
