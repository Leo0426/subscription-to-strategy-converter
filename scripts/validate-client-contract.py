#!/usr/bin/env python3
"""Synthetic Profile → pinned Mihomo contract; no credentials or controller needed."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "tests/fixtures/client_contract"
RELEASE = r"v\d+\.\d+\.\d+"
# Do not inherit CLASH_POST_UP, controller overrides, proxy settings, or credentials.
KERNEL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C"}


def generate_profile(port_a: int, port_b: int, mixed_port: int) -> tuple[str, dict]:
    from fastapi.testclient import TestClient
    from app.core.renderer import render_yaml
    from app.main import app

    fixture = json.loads((FIXTURES / "profile.json").read_text())
    source = fixture["source"]
    source["proxies"][0]["port"], source["proxies"][1]["port"] = port_a, port_b
    source["mixed-port"] = mixed_port

    async def synthetic_fetch(url: str) -> str:
        if url != "https://source.fixture.test/subscription":
            raise RuntimeError("contract attempted a non-fixture source")
        return render_yaml(source)

    # Patch only the fetch boundary. Public SSRF validation and compilation stay intact.
    with tempfile.TemporaryDirectory(prefix="subflow-profile-contract-") as directory:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("SUBFLOW_")}
        environment["SUBFLOW_DB_PATH"] = str(Path(directory) / "profile.db")
        with patch.dict(os.environ, environment, clear=True), patch(
            "app.core.subscription.fetch_subscription", synthetic_fetch,
        ), TestClient(app) as client:
            created = client.post("/profiles", json={
                "subscription_url": "https://source.fixture.test/subscription",
                "target": "mihomo", "publication_targets": ["mihomo"],
                "selected_policy": fixture["selected_policy"],
            })
            if created.status_code != 201:
                raise RuntimeError(f"synthetic Profile creation failed: {created.text}")
            artifact = client.get(created.json()["subscribe_url"])
            if artifact.status_code != 200:
                raise RuntimeError(f"synthetic Profile subscription failed: {artifact.text}")
            return artifact.text, {
                "publication_revision": artifact.headers.get("x-subflow-revision"),
                "artifact_sha256": hashlib.sha256(artifact.content).hexdigest(),
            }


@contextmanager
def local_endpoint(route: str):
    """Terminate synthetic HTTP tunnels locally; never resolve or forward traffic."""
    allowed = set(json.loads((FIXTURES / "profile.json").read_text())["routes"])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_CONNECT(self):
            if self.path not in {f"{host}:80" for host in allowed}:
                self.send_error(403)
                return
            self.connection.settimeout(1)
            self.send_response(200)
            self.end_headers()
            self.handle_one_request()

        def do_GET(self):
            host = urlsplit(self.path).hostname or self.headers.get("Host", "").split(":")[0]
            if host not in allowed:
                self.send_error(403)
                return
            evidence = {"route": route, "authority": f"{host}:80"}
            self.server.events.append(evidence)
            payload = json.dumps(evidence).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.events = []
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("total kernel deadline exceeded")
    return value


@contextmanager
def kernel_process(arguments: list[str], *, stdout, cwd: Path | None = None):
    """Own the whole process group, including children of an already-exited kernel."""
    process = subprocess.Popen(arguments, env=KERNEL_ENV, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=stdout, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        yield process
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            pass
        # A child can ignore TERM or outlive its parent; poll() alone is insufficient.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=1)


def kernel_command(arguments: list[str], deadline: float, *, cwd: Path | None = None):
    # A file avoids communicate() waiting forever on a descendant's inherited pipe.
    with tempfile.TemporaryFile(mode="w+") as log:
        with kernel_process(arguments, stdout=log, cwd=cwd) as process:
            returncode = process.wait(timeout=remaining(deadline))
        log.seek(0)
        return subprocess.CompletedProcess(arguments, returncode, log.read(), "")


def probe_routes(port: int, expected: dict, deadline: float) -> list[dict]:
    evidence = []
    for host, route in expected.items():
        item = {"host": host, "expected_route": route, "status": "fail"}
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=min(1, remaining(deadline)))
        watchdog = None
        response = None
        try:
            connection.connect()
            active_socket = connection.sock

            def expire():
                # Socket timeouts alone reset after each byte. Shutdown also
                # interrupts buffered status/header and response-body reads.
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            watchdog = threading.Timer(remaining(deadline), expire)
            watchdog.daemon = True
            watchdog.start()
            connection.request("GET", f"http://{host}/contract", headers={"Connection": "close"})
            response = connection.getresponse()
            item["http_status"] = response.status
            item["observed"] = json.loads(response.read(4096))
            remaining(deadline)
            if response.status == 200 and item["observed"] == {"route": route, "authority": f"{host}:80"}:
                item["status"] = "pass"
        except (OSError, ValueError, http.client.HTTPException) as exc:
            item["reason"] = "total kernel deadline exceeded" if time.monotonic() >= deadline else str(exc)
        finally:
            if watchdog is not None:
                watchdog.cancel()
                watchdog.join(timeout=0.1)
            if response is not None:
                response.close()
            connection.close()
        evidence.append(item)
    return evidence


def kernel_case(binary: Path, artifact: str, case: str, output: Path, deadline: float) -> dict:
    from ruamel.yaml import YAML
    from app.core.renderer import render_yaml

    result = {"parse": {"status": "skip"}, "behavior": {"status": "skip"}}
    config = YAML(typ="safe").load(artifact)
    if case == "wrong_order":
        config["rules"][0], config["rules"][1] = config["rules"][1], config["rules"][0]
    provider = (FIXTURES / "contract-domains.yaml").read_text()
    if case == "broken_provider":
        provider = "payload: [\n"
    with tempfile.TemporaryDirectory(prefix=f"subflow-{case}-") as directory:
        work = Path(directory)
        config_path = work / "config.yaml"
        config_path.write_text(render_yaml(config))
        (work / "contract-domains.yaml").write_text(provider)
        arguments = [str(binary.resolve()), "-d", str(work), "-f", str(config_path)]
        try:
            parsed = kernel_command(arguments + ["-t"], deadline, cwd=work)
            (output / f"{case}-parse.log").write_text(parsed.stdout + parsed.stderr)
            result["parse"] = {"status": "pass" if parsed.returncode == 0 else "fail", "returncode": parsed.returncode}
            if parsed.returncode:
                return result
        except (OSError, subprocess.SubprocessError) as exc:
            result["parse"] = {"status": "fail", "reason": str(exc)}
            return result
        started = time.monotonic()
        with (output / f"{case}-runtime.log").open("w") as log, kernel_process(
            arguments, stdout=log, cwd=work,
        ) as process:
            try:
                while True:
                    remaining(deadline)
                    if process.poll() is not None:
                        raise RuntimeError(f"kernel exited before readiness: {process.returncode}")
                    try:
                        with socket.create_connection(("127.0.0.1", config["mixed-port"]), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(min(0.05, remaining(deadline)))
                routes = json.loads((FIXTURES / "profile.json").read_text())["routes"]
                samples = probe_routes(config["mixed-port"], routes, deadline)
                result["behavior"] = {"status": "pass" if all(r["status"] == "pass" for r in samples) else "fail",
                                      "routes": samples, "cold_start_seconds": round(time.monotonic() - started, 4)}
            except (OSError, RuntimeError) as exc:
                result["behavior"] = {"status": "fail", "reason": str(exc)}
    return result


def run_contract(binary: Path | None, expected_version: str | None, output: Path, timeout: float) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "unavailable", "kernel": {"expected_version": expected_version},
        "parse": {"status": "unavailable"}, "behavior": {"status": "skip"},
        "fixture_sha256": hashlib.sha256(b"".join(
            path.name.encode() + b"\0" + path.read_bytes() for path in sorted(FIXTURES.iterdir())
        )).hexdigest(),
        "not_tested": ["ServiceRoute modes", "Surge CLI", "Surge iOS", "Shadowrocket", "real services", "resource usage"],
    }
    try:
        artifact, metadata = generate_profile(18080, 18081, 18082)
        (output / "profile.yaml").write_text(artifact)
        report["profile"] = metadata
        if binary is None or not binary.is_file():
            report["reason"] = "Mihomo binary unavailable; pass --mihomo-binary and --expected-version"
        else:
            deadline = time.monotonic() + timeout
            report["kernel"]["sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
            result = kernel_command([str(binary.resolve()), "-v"], deadline)
            version = re.search(r"Mihomo Meta (\S+)", result.stdout)
            report["kernel"].update(actual_version=version[1] if version else None, version_output=result.stdout.strip())
            report["parse"]["status"] = "skip"
            report["status"] = "fail"
            report["reason"] = "kernel version does not match expected release"
            if result.returncode == 0 and version and version[1] == expected_version:
                report.pop("reason")
                with local_endpoint("A") as endpoint_a, local_endpoint("B") as endpoint_b:
                    with socket.socket() as reservation:
                        reservation.bind(("127.0.0.1", 0))
                        port = reservation.getsockname()[1]
                    artifact, report["profile"] = generate_profile(endpoint_a.server_port, endpoint_b.server_port, port)
                    (output / "profile.yaml").write_text(artifact)
                    report.update(kernel_case(binary, artifact, "baseline", output, deadline))
                    report["negative_controls"] = {}
                    if report["behavior"]["status"] == "pass":
                        for case, host in (("wrong_order", "exact.contract.test"), ("broken_provider", "provider.fixture.test")):
                            control = kernel_case(binary, artifact, case, output, deadline)
                            control["detected"] = any(
                                sample["host"] == host and sample["status"] == "fail" and "observed" in sample
                                for sample in control["behavior"].get("routes", [])
                            )
                            report["negative_controls"][case] = control
                        if all(control["detected"] for control in report["negative_controls"].values()):
                            report["status"] = "pass"
                    report["endpoint_evidence"] = endpoint_a.events + endpoint_b.events
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(status="fail", reason=str(exc))
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mihomo-binary", type=Path)
    parser.add_argument("--expected-version", help="exact release, e.g. v1.19.27; no latest")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30, help="total kernel deadline in seconds (1–120)")
    args = parser.parse_args()
    if args.mihomo_binary and not args.expected_version:
        parser.error("--mihomo-binary requires --expected-version")
    if args.expected_version and not re.fullmatch(RELEASE, args.expected_version):
        parser.error("--expected-version must name an exact release, e.g. v1.19.27")
    if not 1 <= args.timeout <= 120:
        parser.error("--timeout must be between 1 and 120 seconds")
    report = run_contract(args.mihomo_binary, args.expected_version, args.output_dir, args.timeout)
    print(json.dumps(report, indent=2))
    return {"pass": 0, "fail": 1, "unavailable": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
