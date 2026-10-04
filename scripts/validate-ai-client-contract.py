#!/usr/bin/env python3
"""Full Leo Profile → pinned Mihomo AI routing with local, non-forwarding exits."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("client_contract_helpers", ROOT / "scripts/validate-client-contract.py")
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)
FIXTURE = ROOT / "tests/fixtures/client_contract/ai-services.json"
SERVICES = json.loads(FIXTURE.read_text())["services"]
NODE_NAMES = ["HK Trap", *[service[key] for service in SERVICES for key in ("primary", "backup")]]
HEALTH_URL = "http://health.fixture.test/generate_204"
CANARY_HOST = "www.google.com"  # Broad Google route, independent of all AI mutations.
HOSTS = {host for service in SERVICES for host in service["hosts"]}


def generate_profiles(ports: dict[str, int], mixed_port: int, controller_port: int) -> dict:
    """Exercise /profiles and the same authenticated subscription links users get."""
    from fastapi.testclient import TestClient
    from app.core.renderer import render_yaml
    from app.main import app

    source = {
        "mixed-port": mixed_port, "allow-lan": False, "bind-address": "127.0.0.1",
        "external-controller": f"127.0.0.1:{controller_port}", "mode": "rule", "ipv6": False,
        "dns": {"enable": False}, "tun": {"enable": False}, "sniffer": {"enable": False},
        "profile": {"store-selected": False, "store-fake-ip": False},
        "proxies": [{"name": name, "type": "http", "server": "127.0.0.1", "port": ports[name]}
                    for name in NODE_NAMES],
        "rules": ["MATCH,REJECT"],
    }

    async def synthetic_fetch(url: str, **_kwargs) -> str:
        if url != "https://source.fixture.test/subscription":
            raise RuntimeError("contract attempted a non-fixture subscription")
        return render_yaml(source)

    profiles = {}
    with tempfile.TemporaryDirectory(prefix="subflow-ai-profile-") as directory:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("SUBFLOW_")}
        environment["SUBFLOW_DB_PATH"] = str(Path(directory) / "profile.db")
        with patch.dict(os.environ, environment, clear=True), patch(
            "app.core.subscription.fetch_subscription", synthetic_fetch,
        ), TestClient(app) as client:
            for mode in ("fixed", "manual", "fallback"):
                routes = [{"service": service["id"], "mode": mode,
                           "egress": service["manual"] if mode == "manual" else service["primary"],
                           **({"fallback": service["backup"]} if mode == "fallback" else {})}
                          for service in SERVICES]
                response = client.post("/profiles", json={
                    "subscription_url": "https://source.fixture.test/subscription",
                    "target": "mihomo", "publication_targets": ["mihomo", "surge"],
                    "service_routes": routes,
                })
                if response.status_code != 201:
                    raise RuntimeError(f"{mode} Profile creation failed: {response.text}")
                entry = {"metadata": {}}
                for target, alias in (("mihomo", "clash"), ("surge", "surge")):
                    artifact = client.get(response.json()["subscribe_urls"][alias])
                    if artifact.status_code != 200:
                        raise RuntimeError(f"{mode} {target} subscription failed: {artifact.text}")
                    entry[target] = artifact.text
                    entry["metadata"][target] = {
                        "publication_revision": artifact.headers.get("x-subflow-revision"),
                        "sha256": hashlib.sha256(artifact.content).hexdigest(),
                    }
                profiles[mode] = entry
    return profiles


def dependency_file(url: str, cache: Path, *, download: bool) -> Path:
    """Freeze public pinned inputs on first explicit download, verify every reuse."""
    cache.mkdir(parents=True, exist_ok=True)
    manifest_path = cache / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    filename = hashlib.sha256(url.encode()).hexdigest()
    path = cache / filename
    if url in manifest:
        record = manifest[url]
        if record["file"] != filename or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"dependency digest mismatch: {url}")
        return path
    if not download:
        raise FileNotFoundError(f"dependency unavailable; explicitly use --download-dependencies: {url}")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"raw.githubusercontent.com", "cdn.jsdelivr.net"}
            or not re.search(r"[/@][0-9a-f]{40}/", parsed.path) or parsed.query or parsed.fragment):
        raise ValueError(f"dependency must use a pinned official repository URL: {url}")
    import httpx
    with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
        response = client.get(url)
        response.raise_for_status()
    content = response.content
    if not content or len(content) > 32 * 1024 * 1024:
        raise ValueError(f"dependency size outside contract limit: {url}")
    path.write_bytes(content)
    manifest[url] = {"file": filename, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


def localize_config(artifact: str, work: Path, cache: Path, *, download: bool) -> tuple[dict, list[dict]]:
    """Keep every rule/member intact; localize downloads and generic health probes."""
    from ruamel.yaml import YAML
    config = YAML(typ="safe").load(artifact)
    dependencies = []
    for index, provider in enumerate(config["rule-providers"].values()):
        url = provider["url"]
        source = dependency_file(url, cache, download=download)
        destination = f"provider-{index}.{provider['format']}"
        shutil.copyfile(source, work / destination)
        dependencies.append({"url": url, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
        provider.update(type="file", path=f"./{destination}")
        for key in ("url", "proxy", "interval"):
            provider.pop(key, None)
    for kind, filename in (("geosite", "GeoSite.dat"), ("mmdb", "country.mmdb"), ("asn", "ASN.mmdb")):
        url = config["geox-url"][kind]
        source = dependency_file(url, cache, download=download)
        shutil.copyfile(source, work / filename)
        dependencies.append({"url": url, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    # A missing/corrupt local database must not trigger an Internet download.
    config["geox-url"] = {kind: "http://127.0.0.1:1/unavailable" for kind in config["geox-url"]}
    config["geo-auto-update"] = False
    for group in config["proxy-groups"]:
        if group.get("url"):
            if group["url"] != "https://cp.cloudflare.com/generate_204" or group.get("expected-status") != 204:
                raise ValueError(f"unexpected health-check contract for {group['name']}")
            group["url"] = HEALTH_URL
    return config, dependencies


@contextmanager
def local_exit(name: str):
    """A proxy-shaped sink: no DNS lookup, forwarding socket, or remote service."""
    allowed = HOSTS | {"health.fixture.test", CANARY_HOST}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_CONNECT(self):
            if self.path not in {f"{host}:80" for host in allowed}:
                self.server.events.append({"kind": "denied", "authority": self.path})
                self.send_error(403)
                return
            if not self.server.healthy:
                self.send_error(503)
                return
            self.connection.settimeout(1)
            self.send_response(200)
            self.end_headers()
            try:
                self.handle_one_request()
            except OSError:
                pass  # Kernel teardown can cancel an in-flight health check.

        def do_HEAD(self):
            host = urlsplit(self.path).hostname or self.headers.get("Host", "").split(":")[0]
            status = 204 if self.server.healthy else 503
            if host != "health.fixture.test":
                status = 403
            self.server.events.append({"kind": "health", "route": name, "status": status})
            self.send_response(status)
            self.send_header("Connection", "close")
            self.end_headers()

        def do_GET(self):
            host = urlsplit(self.path).hostname or self.headers.get("Host", "").split(":")[0]
            if host not in allowed:
                self.server.events.append({"kind": "denied", "authority": host})
                self.send_error(403)
                return
            evidence = {"route": name, "authority": f"{host}:80"}
            self.server.events.append({"kind": "request", **evidence})
            payload = json.dumps(evidence).encode()
            self.send_response(200 if self.server.healthy else 503)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.healthy, server.events = True, []
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def controller_request(port: int, method: str, path: str, deadline: float, body: dict | None = None) -> dict:
    """Only the contract's newly allocated loopback controller is addressable."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=min(1, helpers.remaining(deadline)))
    watchdog = None
    response = None
    try:
        connection.connect()
        active_socket = connection.sock

        def expire():
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        watchdog = threading.Timer(helpers.remaining(deadline), expire)
        watchdog.daemon = True
        watchdog.start()
        connection.request(method, path, None if body is None else json.dumps(body), {"Content-Type": "application/json"})
        response = connection.getresponse()
        payload = response.read()
        helpers.remaining(deadline)
        if response.status not in {200, 204}:
            raise RuntimeError(f"private controller returned {response.status}: {path}")
        return json.loads(payload) if payload else {}
    except (OSError, http.client.HTTPException):
        helpers.remaining(deadline)
        raise
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join(timeout=0.1)
        if response is not None:
            response.close()
        connection.close()


def expected_routes(member: str) -> dict[str, str]:
    return {host: service[member] for service in SERVICES for host in service["hosts"]}


def validate_service_groups(config: dict, mode: str) -> None:
    groups = {group["name"]: group for group in config["proxy-groups"]}
    for service in SERVICES:
        group = groups.get(service["group"], {})
        members = ([service["primary"], service["backup"]] if mode == "fallback"
                   else [service["manual"] if mode == "manual" else service["primary"]])
        if group.get("proxies") != members or group.get("type") != ("fallback" if mode == "fallback" else "select"):
            raise ValueError(f"unexpected {mode} members/type for {service['group']}")


def kernel_case(binary: Path, original: dict, case: str, endpoints: dict, work: Path,
                output: Path, deadline: float) -> dict:
    from app.core.renderer import render_yaml
    config = deepcopy(original)
    down = set()
    if case in {"fallback_backup", "fallback_exhausted", "third_exit", "fixed_unavailable"}:
        down = {service["primary"] for service in SERVICES}
    if case in {"fallback_exhausted", "third_exit"}:
        down |= {service["backup"] for service in SERVICES}
    for name, endpoint in endpoints.items():
        endpoint.healthy = name not in down
        endpoint.events.clear()
    if case == "wrong_service":
        rule = "DOMAIN-SUFFIX,openai.com,OpenAI"
        config["rules"][config["rules"].index(rule)] = "DOMAIN-SUFFIX,openai.com,Claude"
    if case == "wide_first":
        broad = "GEOSITE,google,Google"
        config["rules"].remove(broad)
        config["rules"].insert(0, broad)
    if case == "third_exit":
        for group in config["proxy-groups"]:
            if group["name"] in {service["group"] for service in SERVICES}:
                group["proxies"].append("HK Trap")
    config_path = work / f"{case}.yaml"
    config_path.write_text(render_yaml(config))
    arguments = [str(binary.resolve()), "-d", str(work), "-f", str(config_path)]
    result = {"parse": {"status": "skip"}, "behavior": {"status": "skip"}}
    parsed = helpers.kernel_command(arguments + ["-t"], deadline, cwd=work)
    (output / f"{case}-parse.log").write_text(parsed.stdout)
    result["parse"] = {"status": "pass" if parsed.returncode == 0 else "fail", "returncode": parsed.returncode}
    if parsed.returncode:
        return result
    controller_port = int(config["external-controller"].rsplit(":", 1)[1])
    with (output / f"{case}-runtime.log").open("w") as log, helpers.kernel_process(
        arguments, stdout=log, cwd=work,
    ) as process:
        ready_deadline = min(deadline, time.monotonic() + 20)
        while True:
            helpers.remaining(ready_deadline)
            if process.poll() is not None:
                raise RuntimeError(f"{case}: kernel exited before readiness: {process.returncode}")
            try:
                proxies = controller_request(controller_port, "GET", "/proxies", ready_deadline)["proxies"]
                # Controllers/health checks become available before Mihomo leaves
                # its Inner startup state. Require an unrelated data-plane canary
                # as well, especially before expecting intentionally blocked AI.
                if all(proxies.get(name, {}).get("extra", {}).get(HEALTH_URL, {}).get("history") for name in NODE_NAMES):
                    canary = helpers.probe_routes(config["mixed-port"], {CANARY_HOST: "HK Trap"}, ready_deadline)
                    if canary[0]["status"] == "pass":
                        result["readiness"] = canary[0]
                        break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(min(0.05, helpers.remaining(ready_deadline)))
        member = "backup" if case in {"manual_backup", "fallback_backup"} else "primary"
        if case.startswith("manual_"):
            for service in SERVICES:
                controller_request(controller_port, "PUT", "/proxies/" + quote(service["manual"], safe=""),
                                   deadline, {"name": service[member]})
        samples = helpers.probe_routes(config["mixed-port"], expected_routes(member), deadline)
        blocked = case in {"fallback_exhausted", "fixed_unavailable"}
        passed = all(sample.get("http_status") != 200 for sample in samples) if blocked else all(
            sample["status"] == "pass" for sample in samples)
        result["behavior"] = {"status": "pass" if passed else "fail", "expectation": "blocked" if blocked else member,
                              "routes": samples}
        result["selected"] = {service["group"]: controller_request(
            controller_port, "GET", "/proxies/" + quote(service["group"], safe=""), deadline,
        ).get("now") for service in SERVICES}
    result["endpoint_evidence"] = [event for endpoint in endpoints.values() for event in endpoint.events]
    if any(event["kind"] == "denied" for event in result["endpoint_evidence"]):
        result["behavior"]["status"] = "fail"
        result["behavior"]["reason"] = "unexpected destination requested from a local exit"
    return result


def unused_port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def run_contract(binary: Path | None, expected_version: str | None, output: Path, cache: Path,
                 *, download: bool = False, timeout: float = 120) -> dict:
    import subprocess
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "unavailable", "kernel": {"expected_version": expected_version},
        "parse": {"status": "unavailable"}, "behavior": {"status": "skip"},
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "runtime_adaptations": ["Pinned providers become local files; exact bytes are SHA-256 checked",
                                "Geo databases are preloaded; update URLs point to closed loopback port",
                                "Generic health URL uses a local HTTP HEAD 204/503 sink; group members/order remain intact"],
        "not_tested": ["Surge iOS runtime", "OpenClash device integration", "real services or account acceptance",
                       "TLS/QUIC/WebSocket/SSE sessions", "real provider download egress", "resource usage"],
    }
    try:
        with ExitStack() as stack:
            endpoints = {name: stack.enter_context(local_exit(name)) for name in NODE_NAMES}
            profiles = generate_profiles({name: endpoint.server_port for name, endpoint in endpoints.items()},
                                         unused_port(), unused_port())
            report["profiles"] = {mode: entry["metadata"] for mode, entry in profiles.items()}
            for mode, entry in profiles.items():
                (output / f"{mode}-mihomo.yaml").write_text(entry["mihomo"])
                (output / f"{mode}-surge.conf").write_text(entry["surge"])
            if binary is None or not binary.is_file():
                report["reason"] = "Mihomo binary unavailable; provide an exact verified release"
            else:
                deadline = time.monotonic() + timeout
                version_result = helpers.kernel_command([str(binary.resolve()), "-v"], deadline)
                version = re.search(r"Mihomo Meta (\S+)", version_result.stdout)
                report["kernel"].update(actual_version=version[1] if version else None,
                                         sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
                report["status"] = "fail"
                if version_result.returncode or not version or version[1] != expected_version:
                    raise ValueError("kernel version does not match expected release")
                report["cases"], report["negative_controls"] = {}, {}
                for mode, cases in (("fixed", ("fixed", "fixed_unavailable", "wrong_service", "wide_first")),
                                    ("manual", ("manual_primary", "manual_backup")),
                                    ("fallback", ("fallback_primary", "fallback_backup", "fallback_exhausted", "third_exit"))):
                    with tempfile.TemporaryDirectory(prefix=f"subflow-ai-{mode}-") as directory:
                        work = Path(directory)
                        config, report["dependencies"] = localize_config(profiles[mode]["mihomo"], work, cache, download=download)
                        validate_service_groups(config, mode)
                        for case in cases:
                            result = kernel_case(binary, config, case, endpoints, work, output, deadline)
                            if case in {"wrong_service", "wide_first", "third_exit"}:
                                sentinel = {"wrong_service": "api.openai.com", "wide_first": "gemini.google.com",
                                            "third_exit": "gemini.google.com"}[case]
                                result["detected"] = any(sample["host"] == sentinel and sample["status"] == "fail"
                                                         and sample.get("http_status") == 200 and "observed" in sample
                                                         for sample in result["behavior"].get("routes", []))
                                report["negative_controls"][case] = result
                            else:
                                report["cases"][case] = result
                parse_ok = all(case["parse"]["status"] == "pass" for case in report["cases"].values())
                behavior_ok = all(case["behavior"]["status"] == "pass" for case in report["cases"].values())
                controls_ok = all(case["detected"] for case in report["negative_controls"].values())
                report["parse"] = {"status": "pass" if parse_ok else "fail"}
                report["behavior"] = {"status": "pass" if behavior_ok else "fail"}
                report["status"] = "pass" if parse_ok and behavior_ok and controls_ok else "fail"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(status="fail", reason=str(exc))
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mihomo-binary", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dependency-cache", type=Path, required=True)
    parser.add_argument("--download-dependencies", action="store_true", help="explicitly fetch missing pinned public inputs")
    parser.add_argument("--timeout", type=float, default=120, help="total kernel deadline, 1–120 seconds")
    args = parser.parse_args()
    if args.mihomo_binary and not args.expected_version:
        parser.error("--mihomo-binary requires --expected-version")
    if args.expected_version and not re.fullmatch(helpers.RELEASE, args.expected_version):
        parser.error("--expected-version must be an exact release")
    if not 1 <= args.timeout <= 120:
        parser.error("--timeout must be between 1 and 120 seconds")
    report = run_contract(args.mihomo_binary, args.expected_version, args.output_dir, args.dependency_cache,
                          download=args.download_dependencies, timeout=args.timeout)
    print(json.dumps({"status": report["status"], "report": str(args.output_dir / "report.json"),
                      **({"reason": report["reason"]} if "reason" in report else {})}))
    return {"pass": 0, "fail": 1, "unavailable": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
