#!/usr/bin/env python3
"""Build custom Linux Coder Docker images from the coder/coder repo.

This script owns the full build flow:

1. Resolve a Coder Git ref, including the latest non-prerelease GitHub release.
2. Maintain a cached clone of https://github.com/coder/coder.git.
3. Create an isolated worktree for the selected ref.
4. Apply local path-based overrides into that worktree.
5. Install the exact Go toolchain declared by Coder's go.mod.
6. Run the same upstream Make targets used by Coder for Docker images.
7. Validate the resulting image platform and embedded Coder version.

For Apple Silicon Macs, use scripts/build-coder-in-docker.py so this script
runs inside a linux/amd64 builder container.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

REPO_URL = "https://github.com/coder/coder.git"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = ROOT / ".cache"
RELEASE_TAG_RE = re.compile(r"^v[0-9]+(\.[0-9]+){1,2}([.-].*)?$")
WINDOWS_PLATFORM_MESSAGE = (
    "Docker images are Linux-only in upstream Coder. Windows support would "
    "mean building binaries or archives, not Docker images."
)


class BuildError(RuntimeError):
    """Raised for expected build failures with actionable messages."""


def log(message: str) -> None:
    """Write a build log line to stderr."""

    print(message, file=sys.stderr, flush=True)


def fail(message: str) -> None:
    """Abort with a consistent error message."""

    raise BuildError(message)


def shlex_join(values: Iterable[object]) -> str:
    """Quote a command for logs."""

    return shlex.join(str(value) for value in values)


@dataclass(frozen=True)
class DockerPlatform:
    """Supported Docker image platform."""

    arch: str
    docker_platform: str

    @property
    def os_arch(self) -> str:
        """Return the upstream Make OS/arch selector for this Docker image."""

        return f"linux_{self.arch}"


LINUX_AMD64 = DockerPlatform(arch="amd64", docker_platform="linux/amd64")
LINUX_ARM64 = DockerPlatform(arch="arm64", docker_platform="linux/arm64")
PLATFORM_ALIASES = {
    "linux": LINUX_AMD64,
    "amd64": LINUX_AMD64,
    "linux/amd64": LINUX_AMD64,
    "arm": LINUX_ARM64,
    "arm64": LINUX_ARM64,
    "linux/arm64": LINUX_ARM64,
}


@dataclass(frozen=True)
class Paths:
    """Filesystem layout used by the build."""

    root: Path
    cache: Path
    source: Path
    worktrees: Path
    toolchains: Path
    go_build: Path
    go_mod_cache: Path
    pnpm_store: Path
    corepack: Path
    npm_cache: Path
    xdg_cache: Path

    @classmethod
    def from_env(cls) -> Paths:
        """Create the build path layout from CODER_CACHE_DIR or the default cache."""

        cache = Path(os.environ.get("CODER_CACHE_DIR", DEFAULT_CACHE_DIR)).resolve()
        return cls(
            root=ROOT,
            cache=cache,
            source=cache / "coder-src",
            worktrees=cache / "worktrees",
            toolchains=cache / "toolchains",
            go_build=cache / "go-build",
            go_mod_cache=cache / "go" / "pkg" / "mod",
            pnpm_store=cache / "pnpm-store",
            corepack=cache / "corepack",
            npm_cache=cache / "npm",
            xdg_cache=cache / "xdg-cache",
        )


@dataclass(frozen=True)
class BuildOptions:
    """User-facing build configuration."""

    ref: str
    image: str
    tag: str | None
    platforms: tuple[DockerPlatform, ...]
    push: bool
    build_base: bool
    overrides_dir: Path
    embedded_os_arches: str
    dry_run: bool


class Runner:
    """Small subprocess wrapper that logs every command before running it."""

    def __init__(self, dry_run: bool = False) -> None:
        """Configure whether commands should be executed or only logged."""

        self.dry_run = dry_run

    def run(
        self,
        command: list[str | Path],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command with optional capture and dry-run behavior."""

        log("+ " + shlex_join(command))
        if self.dry_run:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env else None,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=check,
        )

    def succeeds(self, command: list[str | Path], *, cwd: Path | None = None) -> bool:
        """Return whether a command exits successfully without printing output."""

        if self.dry_run:
            return True
        return (
            subprocess.run(
                [str(part) for part in command],
                cwd=str(cwd) if cwd else None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )


def parse_platforms(value: str) -> tuple[DockerPlatform, ...]:
    """Normalize a user platform selector into Docker image platforms."""

    normalized = value.strip().lower()
    if normalized.startswith("windows") or normalized in {"win", "win32", "win64"}:
        fail(WINDOWS_PLATFORM_MESSAGE)
    if normalized == "all":
        return (LINUX_AMD64, LINUX_ARM64)
    platform_value = PLATFORM_ALIASES.get(normalized)
    if platform_value:
        return (platform_value,)
    supported = ", ".join(["linux", "amd64", "linux/amd64", "arm", "arm64", "linux/arm64", "all"])
    fail(f"Unsupported --platform {value!r}. Supported values: {supported}.")


def parse_args(argv: list[str]) -> BuildOptions:
    """Parse CLI arguments into validated build options."""

    parser = argparse.ArgumentParser(
        description="Build custom Linux Docker images from coder/coder.",
    )
    parser.add_argument(
        "--ref",
        default="latest-release",
        help="Git ref to build. Use latest-release for the newest stable Coder release.",
    )
    parser.add_argument(
        "--image",
        default="coder-custom",
        help="Docker repository without a tag. Example: ghcr.io/acme/coder",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Optional alias. A bare value becomes <image>:<value>.",
    )
    parser.add_argument(
        "--platform",
        default="linux",
        help=(
            "Docker image platform to build: linux/amd64, linux, amd64, "
            "linux/arm64, arm, arm64, or all. arm means linux/arm64, not Darwin/macOS. "
            "Defaults to linux/amd64."
        ),
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push the primary versioned tag and optional alias after validation.",
    )
    parser.add_argument(
        "--build-base",
        action="store_true",
        help="Build Coder's Dockerfile.base locally instead of pulling coder-base.",
    )
    parser.add_argument(
        "--embedded-os-arches",
        default="target",
        help=(
            "Upstream OS_ARCHES value for slim binaries embedded in the server binary. "
            "Use target for only the Docker image platform, all for upstream defaults, "
            "or a comma/space-separated OS_ARCHES list. Defaults to target."
        ),
    )
    parser.add_argument(
        "--overrides-dir",
        default=ROOT / "overrides",
        type=Path,
        help="Directory whose contents mirror paths in the Coder repository.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved build plan without cloning, building, or pushing.",
    )

    args = parser.parse_args(argv)
    image_tail = args.image.rsplit("/", 1)[-1]
    if ":" in image_tail:
        fail("--image expects a repository without a tag. Use --tag for aliases.")

    return BuildOptions(
        ref=args.ref,
        image=args.image,
        tag=args.tag,
        platforms=parse_platforms(args.platform),
        push=args.push,
        build_base=args.build_base,
        overrides_dir=args.overrides_dir,
        embedded_os_arches=args.embedded_os_arches,
        dry_run=args.dry_run,
    )


def require_command(name: str) -> None:
    """Fail if an external command is not available on PATH."""

    if shutil.which(name) is None:
        fail(f"Missing dependency: {name}")


def check_local_dependencies() -> None:
    """Validate that the host has all tools needed for a local Coder build."""

    for command in (
        "git",
        "make",
        "zstd",
        "node",
        "corepack",
        "docker",
        "protoc",
        "tar",
    ):
        require_command(command)

    make_version = subprocess.run(
        ["make", "--version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    ).stdout
    if "GNU Make" not in make_version:
        fail("Coder requires GNU Make as 'make'. Use scripts/build-coder-in-docker.py on macOS.")

    node_version = subprocess.run(
        ["node", "--version"],
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    match = re.match(r"^v([0-9]+)", node_version)
    if not match or not (22 <= int(match.group(1)) < 25):
        fail(f"Unsupported Node.js version: {node_version}. Coder expects >=22 and <25.")

    if subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        fail("Docker is installed but the daemon is not reachable.")

    include_candidates = (
        Path("/usr/include/google/protobuf/timestamp.proto"),
        Path("/usr/local/include/google/protobuf/timestamp.proto"),
        Path("/opt/homebrew/include/google/protobuf/timestamp.proto"),
    )
    if not any(path.exists() for path in include_candidates):
        fail("Missing standard protobuf includes for google/protobuf/timestamp.proto.")


def github_api_json(url: str) -> object:
    """Fetch a GitHub API endpoint and decode its JSON response."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "custom-coder-builder",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def resolve_latest_release() -> str:
    """Return the newest non-draft, non-prerelease Coder release tag."""

    data = github_api_json("https://api.github.com/repos/coder/coder/releases?per_page=30")
    if not isinstance(data, list):
        fail("Unexpected GitHub release API response.")
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("draft") or item.get("prerelease"):
            continue
        tag = item.get("tag_name")
        if isinstance(tag, str) and tag:
            return tag
    fail("Could not resolve the latest stable Coder release.")


def resolve_ref(ref: str) -> str:
    """Resolve symbolic build references such as latest-release."""

    return resolve_latest_release() if ref == "latest-release" else ref


def is_release_tag(ref: str) -> bool:
    """Return whether a ref looks like a Coder release tag."""

    return bool(RELEASE_TAG_RE.match(ref))


def version_from_tag(ref: str) -> str:
    """Convert a release tag such as v1.2.3 into a version string."""

    return ref.removeprefix("v")


def docker_tag_version(version: str) -> str:
    """Convert a version into a Docker-tag-safe version component."""

    return version.replace("+", "-")


def alias_target(image: str, tag: str | None) -> str | None:
    """Return the full Docker alias target for an optional user tag."""

    if not tag:
        return None
    if "/" in tag or ":" in tag:
        return tag
    return f"{image}:{tag}"


def safe_slug(value: str) -> str:
    """Convert a ref into a bounded filesystem-safe slug."""

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.replace("/", "-"))
    slug = slug.strip("-")
    return slug[:120] or "ref"


def clone_or_update_repo(runner: Runner, paths: Paths) -> None:
    """Ensure the cached Coder repository exists and has fresh refs."""

    paths.cache.mkdir(parents=True, exist_ok=True)
    if (paths.source / ".git").is_dir():
        log(f"Updating cached Coder repository at {paths.source}")
        runner.run(["git", "-C", paths.source, "remote", "set-url", "origin", REPO_URL])
    else:
        if paths.source.exists():
            fail(f"{paths.source} exists but is not a Git repository.")
        log(f"Cloning Coder into {paths.source}")
        runner.run(["git", "clone", REPO_URL, paths.source])

    runner.run(
        [
            "git",
            "-C",
            paths.source,
            "fetch",
            "--tags",
            "--force",
            "--prune",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
        ],
    )
    runner.run(["git", "-C", paths.source, "worktree", "prune"])


def checkout_ref_for_worktree(runner: Runner, paths: Paths, ref: str) -> str:
    """Return the Git checkout target for a branch, tag, or commit ref."""

    if runner.succeeds(
        ["git", "-C", paths.source, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{ref}^{{commit}}"],
    ):
        return f"origin/{ref}"
    if runner.succeeds(
        ["git", "-C", paths.source, "rev-parse", "--verify", "--quiet", f"refs/tags/{ref}^{{commit}}"],
    ):
        return f"refs/tags/{ref}"
    if runner.succeeds(["git", "-C", paths.source, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]):
        return ref
    fail(f"Ref {ref!r} was not found in {REPO_URL}.")


def remove_worktree(runner: Runner, paths: Paths, worktree: Path) -> None:
    """Remove a managed worktree after checking it is inside the cache."""

    allowed_root = paths.worktrees.resolve()
    try:
        worktree.resolve().relative_to(allowed_root)
    except ValueError:
        fail(f"Refusing to remove unexpected worktree path: {worktree}")

    if worktree.exists():
        result = runner.run(
            ["git", "-C", paths.source, "worktree", "remove", "--force", worktree],
            check=False,
        )
        if result.returncode != 0 and not runner.dry_run:
            shutil.rmtree(worktree)


def create_worktree(runner: Runner, paths: Paths, ref: str) -> Path:
    """Create a fresh detached worktree for the resolved Coder ref."""

    checkout = checkout_ref_for_worktree(runner, paths, ref)
    worktree = paths.worktrees / f"coder-{safe_slug(ref)}"
    paths.worktrees.mkdir(parents=True, exist_ok=True)
    remove_worktree(runner, paths, worktree)
    log(f"Creating isolated Coder worktree at {worktree}")
    runner.run(["git", "-C", paths.source, "worktree", "add", "--detach", worktree, checkout])
    runner.run(["git", "-C", worktree, "fetch", "--tags", "--force", "origin"])
    return worktree


def safe_relative_override_path(root: Path, path: Path) -> Path:
    """Return an override path relative to its root after safety checks."""

    relative = path.relative_to(root)
    if relative.is_absolute() or ".." in relative.parts:
        fail(f"Unsafe override path: {relative}")
    return relative


def path_is_within(path: Path, root: Path) -> bool:
    """Return whether path resolves inside root."""

    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def iter_override_files(overrides_dir: Path) -> list[tuple[Path, Path]]:
    """List override files paired with their target-relative paths."""

    if not overrides_dir.exists():
        return []
    if not overrides_dir.is_dir():
        fail(f"Overrides path is not a directory: {overrides_dir}")

    root = overrides_dir.resolve()
    files: list[tuple[Path, Path]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.name in {".gitkeep", ".DS_Store"}:
            continue
        if path.is_symlink() and not path_is_within(path, root):
            fail(f"Override symlink points outside overrides directory: {path}")
        relative = safe_relative_override_path(root, path)
        files.append((path, relative))
    return files


def apply_overrides(options: BuildOptions, worktree: Path) -> None:
    """Copy configured override files into the isolated Coder worktree."""

    files = iter_override_files(options.overrides_dir)
    if not files:
        log(f"No overrides found in {options.overrides_dir}")
        return

    worktree_root = worktree.resolve()
    for source, relative in files:
        target = worktree / relative
        target_parent = target.parent
        target_parent.mkdir(parents=True, exist_ok=True)
        if not path_is_within(target_parent, worktree_root):
            fail(f"Override target escapes Coder worktree: {relative}")
        log(f"Override {relative}")
        shutil.copy2(source, target, follow_symlinks=True)


def go_version_from_mod(worktree: Path) -> str:
    """Read the required Go version from the Coder go.mod file."""

    for line in (worktree / "go.mod").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "go":
            return parts[1]
    fail(f"Could not read Go version from {worktree / 'go.mod'}.")


def go_archive_platform() -> tuple[str, str]:
    """Return Go download OS and architecture names for the current host."""

    system = platform.system().lower()
    machine = platform.machine().lower()
    goos_map = {"linux": "linux", "darwin": "darwin"}
    goarch_map = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    goos = goos_map.get(system)
    goarch = goarch_map.get(machine)
    if not goos or not goarch:
        fail(f"Unsupported host platform for automatic Go install: {system}/{machine}")
    return goos, goarch


def download_file(url: str, destination: Path) -> None:
    """Download a URL into a destination file."""

    log(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)


def install_go_toolchain(paths: Paths, worktree: Path) -> Path:
    """Install or reuse the Go toolchain declared by the Coder worktree."""

    version = go_version_from_mod(worktree)
    goos, goarch = go_archive_platform()
    install_dir = paths.toolchains / f"go{version}.{goos}-{goarch}"
    go_binary = install_dir / "bin" / "go"
    if go_binary.exists():
        log(f"Using cached Go {version} from {install_dir}")
        return install_dir

    paths.toolchains.mkdir(parents=True, exist_ok=True)
    archive_name = f"go{version}.{goos}-{goarch}.tar.gz"
    archive_url = f"https://go.dev/dl/{archive_name}"
    with tempfile.TemporaryDirectory(dir=paths.toolchains) as tmp_name:
        tmp = Path(tmp_name)
        archive = tmp / archive_name
        download_file(archive_url, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(tmp)
        extracted = tmp / "go"
        if not extracted.is_dir():
            fail(f"Go archive did not contain a go directory: {archive_name}")
        if install_dir.exists():
            shutil.rmtree(install_dir)
        shutil.move(str(extracted), str(install_dir))
    return install_dir


def ensure_build_cache(paths: Paths) -> None:
    """Create cache directories used by Go, Node, Corepack, npm, and pnpm."""

    for directory in (
        paths.go_build,
        paths.go_mod_cache,
        paths.pnpm_store,
        paths.corepack,
        paths.npm_cache,
        paths.xdg_cache,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def tool_env(paths: Paths, go_dir: Path) -> dict[str, str]:
    """Build an environment that points tooling at cached dependencies."""

    env = os.environ.copy()
    ensure_build_cache(paths)
    env["GOCACHE"] = str(paths.go_build)
    env["GOMODCACHE"] = str(paths.go_mod_cache)
    env["PNPM_HOME"] = str(paths.pnpm_store)
    env["COREPACK_HOME"] = str(paths.corepack)
    env["NPM_CONFIG_CACHE"] = str(paths.npm_cache)
    env["NPM_CONFIG_STORE_DIR"] = str(paths.pnpm_store)
    env["npm_config_store_dir"] = str(paths.pnpm_store)
    env["XDG_CACHE_HOME"] = str(paths.xdg_cache)
    env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] = "0"
    env["CI"] = "true"
    goflags = env.get("GOFLAGS", "").split()
    if not any(flag.startswith("-buildvcs=") for flag in goflags):
        goflags.append("-buildvcs=false")
    env["GOFLAGS"] = " ".join(goflags)
    env["PATH"] = (
        f"{paths.pnpm_store}{os.pathsep}{go_dir / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    )
    return env


def build_env(paths: Paths, go_dir: Path, version: str, resolved_ref: str) -> dict[str, str]:
    """Build the full environment used by Make to produce the Docker image."""

    env = tool_env(paths, go_dir)
    env["CODER_FORCE_VERSION"] = version
    if is_release_tag(resolved_ref):
        env["CODER_RELEASE"] = "true"
    return env


def compute_version(runner: Runner, worktree: Path, resolved_ref: str, env: Mapping[str, str]) -> str:
    """Run Coder's version script and return the computed version."""

    command = ["./scripts/version.sh"]
    version_env = dict(env)
    if is_release_tag(resolved_ref):
        version_env["CODER_RELEASE"] = "true"
    result = runner.run(command, cwd=worktree, env=version_env, capture=True)
    return result.stdout.strip()


def validate_image(runner: Runner, image_ref: str, platform_value: DockerPlatform, expected_version: str) -> None:
    """Check that a Docker image has the expected platform and Coder version."""

    inspect = runner.run(
        ["docker", "image", "inspect", image_ref, "--format", "{{.Os}}/{{.Architecture}}"],
        capture=True,
    ).stdout.strip()
    if inspect != platform_value.docker_platform:
        fail(f"Built image platform is {inspect!r}, expected {platform_value.docker_platform!r}.")

    version_output = runner.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            platform_value.docker_platform,
            "--entrypoint",
            "/opt/coder",
            image_ref,
            "version",
        ],
        capture=True,
    ).stdout
    print(version_output, file=sys.stderr, end="")
    if expected_version not in version_output:
        fail(f"Image version output did not contain expected version {expected_version!r}.")


def primary_image_ref(image: str, version: str, platform_value: DockerPlatform) -> str:
    """Return the primary versioned Docker image reference for a build."""

    return f"{image}:v{docker_tag_version(version)}-{platform_value.arch}"


def multiarch_image_ref(image: str, version: str) -> str:
    """Return the primary versioned multi-arch Docker image reference."""

    return f"{image}:v{docker_tag_version(version)}"


def target_file(version: str, platform_value: DockerPlatform) -> str:
    """Return the upstream Coder Make target file for an image platform."""

    return f"build/coder_{version}_linux_{platform_value.arch}.tag"


def platform_summary(platforms: tuple[DockerPlatform, ...]) -> str:
    """Return a readable platform list for logs."""

    return ", ".join(platform_value.docker_platform for platform_value in platforms)


def embedded_os_arches(value: str, platform_value: DockerPlatform) -> str | None:
    """Return an upstream OS_ARCHES override, or None for upstream defaults."""

    normalized = value.strip().lower()
    if normalized == "all":
        return None
    if normalized == "target":
        return platform_value.os_arch

    parts = [part for part in re.split(r"[,\s]+", normalized) if part]
    if not parts:
        fail("--embedded-os-arches must be target, all, or a non-empty OS_ARCHES list.")
    for part in parts:
        if not re.match(r"^[a-z0-9_.-]+$", part):
            fail(f"Invalid --embedded-os-arches value: {part!r}.")
    return " ".join(parts)


def print_dry_run(options: BuildOptions, paths: Paths, resolved_ref: str) -> None:
    """Print the build steps that would run without changing local state."""

    version = version_from_tag(resolved_ref) if is_release_tag(resolved_ref) else "<computed>"
    alias = alias_target(options.image, options.tag)
    worktree = paths.worktrees / f"coder-{safe_slug(resolved_ref)}"
    overrides = iter_override_files(options.overrides_dir)
    multi_platform = len(options.platforms) > 1

    log(f"DRY RUN: build Coder ref {resolved_ref!r} for {platform_summary(options.platforms)}.")
    log(f"+ git clone/fetch {REPO_URL} into {paths.source}")
    log(f"+ git worktree add --detach {worktree} {resolved_ref}")
    if overrides:
        for _, relative in overrides:
            log(f"+ override {relative}")
    else:
        log(f"+ no overrides from {options.overrides_dir}")
    log("+ install Go version from go.mod into " + str(paths.toolchains))
    log("+ use Go, Corepack, npm, and pnpm caches under " + str(paths.cache))
    log("+ pnpm --version")
    log("+ ./.github/scripts/retry.sh -- go mod download")
    log("+ make gen/mark-fresh")
    for platform_value in options.platforms:
        current_target = target_file(version, platform_value)
        primary = primary_image_ref(options.image, version, platform_value)
        if options.build_base:
            log(
                "+ CODER_IMAGE_BUILD_BASE_TAG="
                f"{options.image}:base-{docker_tag_version(version)}-{platform_value.arch}"
            )
        os_arches = embedded_os_arches(options.embedded_os_arches, platform_value)
        make_parts = [
            "DOCKER_IMAGE_NO_PREREQUISITES=true",
            f"CODER_IMAGE_BASE={options.image}",
        ]
        if os_arches:
            make_parts.append(f"OS_ARCHES={shlex.quote(os_arches)}")
        log("+ " + " ".join(make_parts) + f" make {current_target}")
        log(f"+ docker image inspect {primary}")
        log(
            "+ docker run --rm "
            f"--platform {platform_value.docker_platform} --entrypoint /opt/coder {primary} version"
        )
        if not multi_platform and alias:
            log(f"+ docker tag {primary} {alias}")
            log(f"+ docker image inspect {alias}")
        if options.push:
            log(f"+ docker push {primary}")

    if multi_platform:
        if options.push:
            primary_images = " ".join(
                f"--amend {primary_image_ref(options.image, version, platform_value)}"
                for platform_value in options.platforms
            )
            manifest = multiarch_image_ref(options.image, version)
            log(f"+ docker manifest create {manifest} {primary_images}")
            log(f"+ docker manifest push {manifest}")
            if alias:
                log(f"+ docker manifest create {alias} {primary_images}")
                log(f"+ docker manifest push {alias}")
        else:
            log("Multi-arch manifest creation requires --push because Docker manifests reference pushed images.")
    elif options.push and alias:
        log(f"+ docker push {alias}")


def create_multiarch_manifest(runner: Runner, target: str, source_images: list[str]) -> None:
    """Create a Docker manifest list from already-pushed source images."""

    command = ["docker", "manifest", "create", target]
    for source_image in source_images:
        command.extend(["--amend", source_image])
    runner.run(command)


def run_build(options: BuildOptions) -> None:
    """Execute the end-to-end Coder Docker image build workflow."""

    paths = Paths.from_env()
    resolved_ref = resolve_ref(options.ref)

    if options.dry_run:
        print_dry_run(options, paths, resolved_ref)
        return

    check_local_dependencies()
    runner = Runner()

    clone_or_update_repo(runner, paths)
    worktree = create_worktree(runner, paths, resolved_ref)
    apply_overrides(options, worktree)

    go_dir = install_go_toolchain(paths, worktree)
    pre_version_env = tool_env(paths, go_dir)
    version = compute_version(runner, worktree, resolved_ref, pre_version_env)
    env = build_env(paths, go_dir, version, resolved_ref)

    log(f"Resolved Coder ref: {resolved_ref}")
    log(f"Resolved Coder version: {version}")
    log(f"Building platforms: {platform_summary(options.platforms)}")

    runner.run(["go", "version"], cwd=worktree, env=env)
    runner.run(["pnpm", "--version"], cwd=worktree, env=env)
    runner.run(["./.github/scripts/retry.sh", "--", "go", "mod", "download"], cwd=worktree, env=env)
    runner.run(["make", "gen/mark-fresh"], cwd=worktree, env=env)

    make_env = dict(env)
    make_env["DOCKER_IMAGE_NO_PREREQUISITES"] = "true"
    make_env["CODER_IMAGE_BASE"] = options.image

    built_images: list[tuple[DockerPlatform, str]] = []
    for platform_value in options.platforms:
        platform_env = dict(make_env)
        if options.build_base:
            platform_env["CODER_IMAGE_BUILD_BASE_TAG"] = (
                f"{options.image}:base-{docker_tag_version(version)}-{platform_value.arch}"
            )
        os_arches = embedded_os_arches(options.embedded_os_arches, platform_value)
        if os_arches:
            platform_env["OS_ARCHES"] = os_arches
        else:
            platform_env.pop("OS_ARCHES", None)

        current_target = target_file(version, platform_value)
        log(f"Building primary image: {primary_image_ref(options.image, version, platform_value)}")
        runner.run(["make", current_target], cwd=worktree, env=platform_env)

        built_image = (worktree / current_target).read_text(encoding="utf-8").strip()
        validate_image(runner, built_image, platform_value, version)
        built_images.append((platform_value, built_image))

    multi_platform = len(built_images) > 1
    alias = alias_target(options.image, options.tag)
    if alias and not multi_platform:
        platform_value, built_image = built_images[0]
        runner.run(["docker", "tag", built_image, alias])
        validate_image(runner, alias, platform_value, version)

    if options.push:
        for _, built_image in built_images:
            runner.run(["docker", "push", built_image])
        if multi_platform:
            manifest = multiarch_image_ref(options.image, version)
            create_multiarch_manifest(runner, manifest, [image for _, image in built_images])
            runner.run(["docker", "manifest", "push", manifest])
            if alias:
                create_multiarch_manifest(runner, alias, [image for _, image in built_images])
                runner.run(["docker", "manifest", "push", alias])
        elif alias:
            runner.run(["docker", "push", alias])
    elif multi_platform:
        log("Multi-arch manifest creation skipped: Docker manifests require pushed source images.")

    log("Done.")
    for platform_value, built_image in built_images:
        log(f"Built {platform_value.docker_platform} image: {built_image}")
    if alias and not multi_platform:
        log(f"Local alias: {alias}")
    if alias and multi_platform and options.push:
        log(f"Pushed multi-arch alias: {alias}")


def main(argv: list[str]) -> int:
    """CLI entry point that maps known failures to process exit codes."""

    try:
        run_build(parse_args(argv))
        return 0
    except BuildError as exc:
        log(f"ERROR: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        log(f"ERROR: command failed with exit code {exc.returncode}: {shlex_join(exc.cmd)}")
        if exc.stdout:
            log(exc.stdout)
        if exc.stderr:
            log(exc.stderr)
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
