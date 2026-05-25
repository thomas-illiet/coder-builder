#!/usr/bin/env python3
"""Check local dependencies for the Coder Builder workflows."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DOCS_PATH = "docs/dependencies.md"


@dataclass(frozen=True)
class Check:
    """A single dependency check result."""

    name: str
    ok: bool
    detail: str
    fix: str = ""


def run_capture(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command and capture text output without raising."""

    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def command_exists(name: str) -> bool:
    """Return whether a command is available on PATH."""

    return shutil.which(name) is not None


def python_check() -> Check:
    """Check that the current Python can run the project scripts."""

    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        return Check("Python 3.10+", True, version)
    return Check(
        "Python 3.10+",
        False,
        version,
        "Install Python 3.10 or newer, then run the scripts with that interpreter.",
    )


def command_check(command: str, install_hint: str) -> Check:
    """Check that a command exists."""

    if command_exists(command):
        return Check(command, True, "found")
    return Check(command, False, "not found", install_hint)


def docker_daemon_check() -> Check:
    """Check that Docker can talk to a running daemon."""

    if not command_exists("docker"):
        return Check("Docker daemon", False, "docker command is missing", "Install Docker first.")
    result = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if result.returncode == 0:
        return Check("Docker daemon", True, "reachable")
    return Check(
        "Docker daemon",
        False,
        "not reachable",
        "Start Docker Desktop on macOS or Docker Engine on Linux.",
    )


def gnu_make_check() -> Check:
    """Check that make is GNU Make."""

    if not command_exists("make"):
        return Check("GNU Make", False, "make not found", "Install GNU Make.")
    result = run_capture(["make", "--version"])
    first_line = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else "unknown"
    if "GNU Make" in result.stdout:
        return Check("GNU Make", True, first_line)
    return Check(
        "GNU Make",
        False,
        first_line,
        "Use the Docker wrapper on macOS, or install GNU Make as the default make on Linux.",
    )


def node_check() -> Check:
    """Check the Node.js major version expected by upstream Coder."""

    if not command_exists("node"):
        return Check("Node.js 22-24", False, "node not found", "Install Node.js 22, 23, or 24.")
    result = run_capture(["node", "--version"])
    version = result.stdout.strip()
    match = re.match(r"^v([0-9]+)", version)
    if match and 22 <= int(match.group(1)) < 25:
        return Check("Node.js 22-24", True, version)
    return Check("Node.js 22-24", False, version or "unknown", "Install Node.js 22, 23, or 24.")


def protobuf_include_check() -> Check:
    """Check for standard protobuf include files used by protoc."""

    candidates = (
        Path("/usr/include/google/protobuf/timestamp.proto"),
        Path("/usr/local/include/google/protobuf/timestamp.proto"),
        Path("/opt/homebrew/include/google/protobuf/timestamp.proto"),
    )
    for candidate in candidates:
        if candidate.exists():
            return Check("protobuf includes", True, str(candidate))
    return Check(
        "protobuf includes",
        False,
        "google/protobuf/timestamp.proto not found",
        "Install protobuf development headers, such as libprotobuf-dev on Debian/Ubuntu.",
    )


def wrapper_checks() -> list[Check]:
    """Return checks required for the Docker wrapper workflow."""

    return [
        python_check(),
        command_check("docker", "Install Docker Desktop on macOS or Docker Engine on Linux."),
        docker_daemon_check(),
    ]


def direct_checks() -> list[Check]:
    """Return checks required for direct host builds."""

    checks = [
        python_check(),
        command_check("git", "Install Git."),
        gnu_make_check(),
        command_check("zstd", "Install zstd."),
        node_check(),
        command_check("corepack", "Install Node.js with Corepack, then run corepack enable."),
        command_check("docker", "Install Docker Engine."),
        docker_daemon_check(),
        command_check("protoc", "Install protobuf-compiler."),
        protobuf_include_check(),
        command_check("tar", "Install tar."),
    ]
    return checks


def print_checks(mode: str, checks: list[Check]) -> int:
    """Print checks and return the intended process exit code."""

    print(f"Coder Builder dependency check: {mode}")
    print(f"See {DOCS_PATH} for install commands and platform notes.")
    print()

    failed = False
    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"{status:4} {check.name}: {check.detail}")
        if not check.ok:
            failed = True
            if check.fix:
                print(f"     fix: {check.fix}")

    print()
    if failed:
        print("One or more checks failed.")
        return 1

    if mode == "wrapper":
        print("Wrapper mode is ready. Host Node, GNU Make, zstd, and protoc are provided by Dockerfile.")
    else:
        print("Direct mode is ready for Linux amd64 hosts with native tooling.")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(description="Check Coder Builder dependencies.")
    parser.add_argument(
        "--mode",
        choices=("wrapper", "direct"),
        default="wrapper",
        help="Dependency set to check. Defaults to wrapper.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """CLI entry point."""

    args = parse_args(argv)
    checks = wrapper_checks() if args.mode == "wrapper" else direct_checks()
    return print_checks(args.mode, checks)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
