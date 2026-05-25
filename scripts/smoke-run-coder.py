#!/usr/bin/env python3
"""Start a built Coder Docker image and verify that /healthz responds."""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass


POSTGRES_IMAGE = "postgres:16-alpine"
CODER_PORT = 3000


class SmokeError(RuntimeError):
    """Raised for expected smoke-test failures."""


@dataclass(frozen=True)
class SmokeOptions:
    """User-facing smoke-test configuration."""

    image_ref: str
    platform: str
    port: int | None
    timeout: int
    keep_running: bool


@dataclass(frozen=True)
class DockerResources:
    """Names for the temporary Docker resources used by one smoke test."""

    suffix: str
    network: str
    postgres: str
    coder: str


def log(message: str) -> None:
    """Write a smoke-test log line to stderr."""

    print(message, file=sys.stderr, flush=True)


def shlex_join(values: list[str]) -> str:
    """Quote a command for readable logs."""

    import shlex

    return shlex.join(values)


def run(
    command: list[str],
    *,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command with consistent logging."""

    log("+ " + shlex_join(command))
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=check,
    )


def parse_args(argv: list[str]) -> SmokeOptions:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Start a Coder Docker image and wait for /healthz to return OK.",
    )
    parser.add_argument(
        "--image-ref",
        required=True,
        help="Full Docker image reference to test, including tag.",
    )
    parser.add_argument(
        "--platform",
        default="linux/amd64",
        help="Docker platform for the Coder container. Defaults to linux/amd64.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Host port to bind. Defaults to a free local port.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Seconds to wait for Postgres and Coder startup. Defaults to 180.",
    )
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Leave the Coder and Postgres containers running after a successful check.",
    )
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        raise SmokeError("--timeout must be greater than zero.")
    if args.port is not None and not 1 <= args.port <= 65535:
        raise SmokeError("--port must be between 1 and 65535.")

    return SmokeOptions(
        image_ref=args.image_ref,
        platform=args.platform,
        port=args.port,
        timeout=args.timeout,
        keep_running=args.keep_running,
    )


def require_docker() -> None:
    """Fail if Docker is missing or its daemon is unreachable."""

    try:
        run(["docker", "info"], capture=True)
    except FileNotFoundError as exc:
        raise SmokeError("Missing dependency: docker") from exc
    except subprocess.CalledProcessError as exc:
        raise SmokeError("Docker is installed but the daemon is not reachable.") from exc


def find_free_port() -> int:
    """Ask the OS for a currently free loopback TCP port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def resources() -> DockerResources:
    """Create unique Docker resource names for this smoke test."""

    suffix = uuid.uuid4().hex[:12]
    return DockerResources(
        suffix=suffix,
        network=f"coder-smoke-{suffix}",
        postgres=f"coder-smoke-postgres-{suffix}",
        coder=f"coder-smoke-coder-{suffix}",
    )


def cleanup(current: DockerResources) -> None:
    """Remove temporary Docker containers and network."""

    run(["docker", "rm", "-f", current.coder], check=False)
    run(["docker", "rm", "-f", current.postgres], check=False)
    run(["docker", "network", "rm", current.network], check=False)


def print_logs(current: DockerResources) -> None:
    """Print Coder and Postgres logs if the smoke test fails."""

    for name in (current.coder, current.postgres):
        log("")
        log(f"Logs for {name}:")
        result = run(["docker", "logs", name], capture=True, check=False)
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")


def start_postgres(current: DockerResources) -> None:
    """Start an ephemeral Postgres container."""

    run(["docker", "network", "create", current.network])
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            current.postgres,
            "--network",
            current.network,
            "--network-alias",
            "postgres",
            "-e",
            "POSTGRES_USER=coder",
            "-e",
            "POSTGRES_PASSWORD=coder",
            "-e",
            "POSTGRES_DB=coder",
            POSTGRES_IMAGE,
        ],
    )


def wait_for_postgres(current: DockerResources, deadline: float) -> None:
    """Wait until Postgres accepts local connections."""

    while time.monotonic() < deadline:
        result = run(
            [
                "docker",
                "exec",
                current.postgres,
                "pg_isready",
                "-U",
                "coder",
                "-d",
                "coder",
            ],
            capture=True,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(2)

    raise SmokeError("Postgres did not become ready before the timeout.")


def start_coder(options: SmokeOptions, current: DockerResources, host_port: int) -> None:
    """Start the Coder container from the image under test."""

    access_url = f"http://127.0.0.1:{host_port}"
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            current.coder,
            "--network",
            current.network,
            "--platform",
            options.platform,
            "-p",
            f"127.0.0.1:{host_port}:{CODER_PORT}",
            "-e",
            f"CODER_ACCESS_URL={access_url}",
            "-e",
            f"CODER_HTTP_ADDRESS=0.0.0.0:{CODER_PORT}",
            "-e",
            "CODER_PG_CONNECTION_URL=postgres://coder:coder@postgres:5432/coder?sslmode=disable",
            "-e",
            "CODER_TELEMETRY_ENABLE=false",
            "-e",
            "CODER_UPDATE_CHECK=false",
            "-e",
            "CODER_PROVISIONER_DAEMONS=0",
            "--entrypoint",
            "/opt/coder",
            options.image_ref,
            "server",
        ],
    )


def container_is_running(name: str) -> bool:
    """Return whether a Docker container is currently running."""

    result = run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def fetch_healthz(url: str) -> tuple[int | None, str]:
    """Fetch /healthz and return status plus body, or an error summary."""

    try:
        with urllib.request.urlopen(f"{url}/healthz", timeout=3) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body
    except (OSError, urllib.error.URLError) as exc:
        return None, str(exc)


def wait_for_healthz(current: DockerResources, access_url: str, deadline: float) -> None:
    """Wait until Coder responds with 200 OK from /healthz."""

    last_status: int | None = None
    last_body = ""
    while time.monotonic() < deadline:
        if not container_is_running(current.coder):
            raise SmokeError("Coder container exited before /healthz became ready.")

        status, body = fetch_healthz(access_url)
        last_status = status
        last_body = body
        if status == 200 and body.strip() == "OK":
            log(f"Coder responded OK at {access_url}/healthz")
            return
        time.sleep(2)

    status_text = "unreachable" if last_status is None else str(last_status)
    raise SmokeError(
        f"Coder /healthz did not return 200 OK before the timeout "
        f"(last status: {status_text}, body: {last_body.strip()!r})."
    )


def smoke_run(options: SmokeOptions) -> None:
    """Run the end-to-end Coder startup smoke test."""

    require_docker()
    current = resources()
    host_port = options.port if options.port is not None else find_free_port()
    access_url = f"http://127.0.0.1:{host_port}"
    deadline = time.monotonic() + options.timeout
    succeeded = False

    try:
        start_postgres(current)
        wait_for_postgres(current, deadline)
        start_coder(options, current, host_port)
        wait_for_healthz(current, access_url, deadline)
        succeeded = True
        if options.keep_running:
            log("")
            log(f"Coder is running at {access_url}")
            log("Cleanup command:")
            log(f"docker rm -f {current.coder} {current.postgres} && docker network rm {current.network}")
    except Exception:
        print_logs(current)
        raise
    finally:
        if not (succeeded and options.keep_running):
            cleanup(current)


def main(argv: list[str]) -> int:
    """CLI entry point that maps known failures to process exit codes."""

    try:
        smoke_run(parse_args(argv))
        return 0
    except SmokeError as exc:
        log(f"ERROR: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        log(f"ERROR: command failed with exit code {exc.returncode}: {shlex_join(exc.cmd)}")
        if exc.stdout:
            log(exc.stdout)
        if exc.stderr:
            log(exc.stderr)
        return exc.returncode
    except KeyboardInterrupt:
        log("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
