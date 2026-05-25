#!/usr/bin/env python3
"""Build a custom linux/amd64 Coder Docker image from the  repo.

This script owns the full build flow:

1. Resolve a Coder Git ref, including the latest non-prerelease GitHub release.
2. Maintain a cached clone of https://github.com/coder/coder.git.
3. Create an isolated worktree for the selected ref.
4. Apply local path-based overrides into that worktree.
5. Install the exact Go toolchain declared by Coder's go.mod.
6. Run the same upstream Make targets used by Coder for Docker images.
7. Validate the resulting image platform and embedded Coder version.

For Apple Silicon Macs, use build-coder-in-docker.py so this script runs inside
a linux/amd64 builder container.
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
ARCH = "amd64"
PLATFORM = "linux/amd64"
ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = ROOT / ".cache"
RELEASE_TAG_RE = re.compile(r"^v[0-9]+(\.[0-9]+){1,2}([.-].*)?$")


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
    push: bool
    build_base: bool
    overrides_dir: Path
    dry_run: bool


class Runner:
    """Small subprocess wrapper that logs every command before running it."""

    def __init__(self, dry_run: bool = False) -> None:
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


def parse_args(argv: list[str]) -> BuildOptions:
    parser = argparse.ArgumentParser(
        description="Build a custom linux/amd64 Docker image from coder/coder.",
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
        "--overrides-dir",
        default=ROOT / "overrides",
        type=Path,
        help="Directory whose contents mirror paths in the  Coder repository.",
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
        push=args.push,
        build_base=args.build_base,
        overrides_dir=args.overrides_dir,
        dry_run=args.dry_run,
    )


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        fail(f"Missing dependency: {name}")


def check_local_dependencies() -> None:
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
        fail("Coder requires GNU Make as 'make'. Use build-coder-in-docker.py on macOS.")

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
    return resolve_latest_release() if ref == "latest-release" else ref


def is_release_tag(ref: str) -> bool:
    return bool(RELEASE_TAG_RE.match(ref))


def version_from_tag(ref: str) -> str:
    return ref.removeprefix("v")


def docker_tag_version(version: str) -> str:
    return version.replace("+", "-")


def alias_target(image: str, tag: str | None) -> str | None:
    if not tag:
        return None
    if "/" in tag or ":" in tag:
        return tag
    return f"{image}:{tag}"


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.replace("/", "-"))
    slug = slug.strip("-")
    return slug[:120] or "ref"


def clone_or_update_repo(runner: Runner, paths: Paths) -> None:
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
    checkout = checkout_ref_for_worktree(runner, paths, ref)
    worktree = paths.worktrees / f"coder-{safe_slug(ref)}"
    paths.worktrees.mkdir(parents=True, exist_ok=True)
    remove_worktree(runner, paths, worktree)
    log(f"Creating isolated Coder worktree at {worktree}")
    runner.run(["git", "-C", paths.source, "worktree", "add", "--detach", worktree, checkout])
    runner.run(["git", "-C", worktree, "fetch", "--tags", "--force", "origin"])
    return worktree


def safe_relative_override_path(root: Path, path: Path) -> Path:
    relative = path.relative_to(root)
    if relative.is_absolute() or ".." in relative.parts:
        fail(f"Unsafe override path: {relative}")
    return relative


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def iter_override_files(overrides_dir: Path) -> list[tuple[Path, Path]]:
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
    for line in (worktree / "go.mod").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "go":
            return parts[1]
    fail(f"Could not read Go version from {worktree / 'go.mod'}.")


def go_archive_platform() -> tuple[str, str]:
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
    log(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)


def install_go_toolchain(paths: Paths, worktree: Path) -> Path:
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
    env["PATH"] = (
        f"{paths.pnpm_store}{os.pathsep}{go_dir / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    )
    return env


def build_env(paths: Paths, go_dir: Path, version: str, resolved_ref: str) -> dict[str, str]:
    env = tool_env(paths, go_dir)
    env["CODER_FORCE_VERSION"] = version
    if is_release_tag(resolved_ref):
        env["CODER_RELEASE"] = "true"
    return env


def compute_version(runner: Runner, worktree: Path, resolved_ref: str, env: Mapping[str, str]) -> str:
    command = ["./scripts/version.sh"]
    version_env = dict(env)
    if is_release_tag(resolved_ref):
        version_env["CODER_RELEASE"] = "true"
    result = runner.run(command, cwd=worktree, env=version_env, capture=True)
    return result.stdout.strip()


def validate_image(runner: Runner, image_ref: str, expected_version: str) -> None:
    inspect = runner.run(
        ["docker", "image", "inspect", image_ref, "--format", "{{.Os}}/{{.Architecture}}"],
        capture=True,
    ).stdout.strip()
    if inspect != PLATFORM:
        fail(f"Built image platform is {inspect!r}, expected {PLATFORM!r}.")

    version_output = runner.run(
        ["docker", "run", "--rm", "--platform", PLATFORM, "--entrypoint", "/opt/coder", image_ref, "version"],
        capture=True,
    ).stdout
    print(version_output, file=sys.stderr, end="")
    if expected_version not in version_output:
        fail(f"Image version output did not contain expected version {expected_version!r}.")


def primary_image_ref(image: str, version: str) -> str:
    return f"{image}:v{docker_tag_version(version)}-{ARCH}"


def print_dry_run(options: BuildOptions, paths: Paths, resolved_ref: str) -> None:
    version = version_from_tag(resolved_ref) if is_release_tag(resolved_ref) else "<computed>"
    target_file = f"build/coder_{version}_linux_{ARCH}.tag"
    primary = primary_image_ref(options.image, version)
    alias = alias_target(options.image, options.tag)
    worktree = paths.worktrees / f"coder-{safe_slug(resolved_ref)}"
    overrides = iter_override_files(options.overrides_dir)

    log(f"DRY RUN: build Coder ref {resolved_ref!r} for {PLATFORM}.")
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
    if options.build_base:
        log(f"+ CODER_IMAGE_BUILD_BASE_TAG={options.image}:base-{docker_tag_version(version)}-{ARCH}")
    log(f"+ DOCKER_IMAGE_NO_PREREQUISITES=true CODER_IMAGE_BASE={options.image} make {target_file}")
    log(f"+ docker image inspect {primary}")
    log(f"+ docker run --rm --platform {PLATFORM} --entrypoint /opt/coder {primary} version")
    if alias:
        log(f"+ docker tag {primary} {alias}")
        log(f"+ docker image inspect {alias}")
    if options.push:
        log(f"+ docker push {primary}")
        if alias:
            log(f"+ docker push {alias}")


def run_build(options: BuildOptions) -> None:
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
    log(f"Building primary image: {primary_image_ref(options.image, version)}")

    runner.run(["go", "version"], cwd=worktree, env=env)
    runner.run(["pnpm", "--version"], cwd=worktree, env=env)
    runner.run(["./.github/scripts/retry.sh", "--", "go", "mod", "download"], cwd=worktree, env=env)
    runner.run(["make", "gen/mark-fresh"], cwd=worktree, env=env)

    make_env = dict(env)
    make_env["DOCKER_IMAGE_NO_PREREQUISITES"] = "true"
    make_env["CODER_IMAGE_BASE"] = options.image
    if options.build_base:
        make_env["CODER_IMAGE_BUILD_BASE_TAG"] = (
            f"{options.image}:base-{docker_tag_version(version)}-{ARCH}"
        )

    target_file = f"build/coder_{version}_linux_{ARCH}.tag"
    runner.run(["make", target_file], cwd=worktree, env=make_env)

    built_image = (worktree / target_file).read_text(encoding="utf-8").strip()
    validate_image(runner, built_image, version)

    alias = alias_target(options.image, options.tag)
    if alias:
        runner.run(["docker", "tag", built_image, alias])
        validate_image(runner, alias, version)

    if options.push:
        runner.run(["docker", "push", built_image])
        if alias:
            runner.run(["docker", "push", alias])

    log("Done.")
    log(f"Built image: {built_image}")
    if alias:
        log(f"Local alias: {alias}")


def main(argv: list[str]) -> int:
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
