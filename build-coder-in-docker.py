#!/usr/bin/env python3
"""Run build-coder.py inside a linux/amd64 Docker builder container."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLATFORM = "linux/amd64"
DEFAULT_BUILDER_IMAGE = os.environ.get("CODER_BUILDER_IMAGE", "coder-coder-builder:amd64")


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def shlex_join(values: list[str | Path]) -> str:
    return shlex.join(str(value) for value in values)


def run(command: list[str | Path]) -> None:
    log("+ " + shlex_join(command))
    subprocess.run([str(part) for part in command], check=True)


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Build Coder inside a linux/amd64 Docker builder.",
        epilog="All unknown options are passed through to build-coder.py.",
    )
    parser.add_argument(
        "--builder-image",
        default=DEFAULT_BUILDER_IMAGE,
        help="Local Docker tag for the reusable builder image.",
    )
    parser.add_argument(
        "--rebuild-builder",
        action="store_true",
        help="Rebuild the builder image before running build-coder.py.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Docker wrapper command and the delegated build dry-run.",
    )
    return parser.parse_known_args(argv)


def require_docker() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("ERROR: Missing dependency: docker")
    if subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise SystemExit("ERROR: Docker is installed but the daemon is not reachable.")


def builder_exists(builder_image: str) -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", builder_image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def build_builder_image(builder_image: str) -> list[str | Path]:
    return [
        "docker",
        "build",
        "--platform",
        PLATFORM,
        "-f",
        ROOT / "Dockerfile.build-coder",
        "-t",
        builder_image,
        ROOT,
    ]


def docker_run_command(builder_image: str, build_args: list[str]) -> list[str | Path]:
    docker_args: list[str | Path] = [
        "docker",
        "run",
        "--rm",
        "--platform",
        PLATFORM,
        "-v",
        f"{ROOT}:/workspace",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-w",
        "/workspace",
        "-e",
        "CODER_CACHE_DIR=/workspace/.cache",
        "-e",
        "DOCKER_HOST=unix:///var/run/docker.sock",
    ]

    docker_config = Path.home() / ".docker"
    if docker_config.is_dir():
        docker_args.extend(
            [
                "-v",
                f"{docker_config}:/host-docker-config:ro",
                "-e",
                "DOCKER_CONFIG=/host-docker-config",
            ],
        )

    docker_args.extend([builder_image, "python3", "build-coder.py", *build_args])
    return docker_args


def delegated_dry_run(build_args: list[str]) -> None:
    command = [sys.executable, str(ROOT / "build-coder.py"), *build_args]
    log("")
    log("Delegated build dry-run:")
    run(command)


def main(argv: list[str]) -> int:
    args, build_args = parse_args(argv)
    if args.dry_run and "--dry-run" not in build_args:
        build_args = [*build_args, "--dry-run"]

    builder_command = build_builder_image(args.builder_image)
    run_command = docker_run_command(args.builder_image, build_args)

    if args.dry_run:
        log("DRY RUN: wrapper commands")
        log("+ " + shlex_join(builder_command))
        log("+ " + shlex_join(run_command))
        delegated_dry_run(build_args)
        return 0

    require_docker()
    if args.rebuild_builder or not builder_exists(args.builder_image):
        run(builder_command)
    else:
        log(f"Using existing builder image: {args.builder_image}")

    run(run_command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
