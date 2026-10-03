"""The optional client contract harness must never silently claim kernel coverage."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import os
import signal
import socket
import threading
import time

import pytest
from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate-client-contract.py"


def harness():
    assert SCRIPT.is_file(), "the client contract CLI has not been implemented"
    spec = importlib.util.spec_from_file_location("client_contract", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_profile_contract_preserves_source_settings_and_dependency_names(tmp_path, monkeypatch):
    contract = harness()
    production_database = tmp_path / "do-not-touch.db"
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(production_database))
    monkeypatch.setenv("SUBFLOW_PUBLIC_BASE_URL", "https://do-not-contact.example")

    artifact, metadata = contract.generate_profile(18080, 18081, 18082)
    config = YAML(typ="safe").load(artifact)

    assert not production_database.exists()
    assert config["mixed-port"] == 18082
    assert config["dns"] == {"enable": False}
    assert config["tun"] == {"enable": False}
    assert config["hosts"] == {"retained.contract.test": "127.0.0.1"}
    groups = {group["name"]: group for group in config["proxy-groups"]}
    assert groups["Contract exact"]["proxies"] == ["Source leaf"]
    assert groups["Source leaf"]["proxies"] == ["DIRECT"]
    assert groups["Subflow Contract exact"]["proxies"] == ["Route A"]
    assert config["rules"][0] == "DOMAIN,exact.contract.test,Subflow Contract exact"
    assert "RULE-SET,contract-domains,Contract broad" in config["rules"]
    assert "IP-CIDR,198.18.0.0/15,Contract broad,no-resolve" in config["rules"]
    assert config["rule-providers"]["contract-domains"]["type"] == "file"
    assert metadata["publication_revision"]
    assert metadata["artifact_sha256"]
    assert "token" not in json.dumps(metadata).lower()


def test_no_kernel_is_unavailable_with_nonzero_exit_and_generated_profile(tmp_path):
    output = tmp_path / "report"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(output)],
        text=True, capture_output=True, cwd=ROOT,
    )
    assert (output / "report.json").exists(), result.stderr
    report = json.loads((output / "report.json").read_text())
    assert result.returncode == 2
    assert report["status"] == "unavailable"
    assert report["parse"]["status"] == "unavailable"
    assert report["behavior"]["status"] == "skip"
    assert (output / "profile.yaml").is_file()
    assert not list(output.rglob("*.db"))


def fake_kernel(tmp_path, version="v1.19.27", body=""):
    binary = tmp_path / "mihomo"
    binary.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"if '-v' in sys.argv:\n print('Mihomo Meta {version} test amd64')\n sys.exit(0)\n"
        + body
    )
    binary.chmod(0o700)
    return binary


def test_wrong_kernel_version_stops_before_config_validation(tmp_path):
    contract = harness()
    marker = tmp_path / "validation-started"
    binary = fake_kernel(tmp_path, body=f"open({str(marker)!r}, 'w').write('wrong')\n")
    report = contract.run_contract(binary, "v1.19.26", tmp_path / "report", 10)

    assert report["status"] == "fail"
    assert report["kernel"]["actual_version"] == "v1.19.27"
    assert len(report["kernel"]["sha256"]) == 64
    assert report["parse"]["status"] == "skip"
    assert report["behavior"]["status"] == "skip"
    assert not marker.exists()


def test_kernel_requires_explicit_pinned_version(tmp_path):
    binary = fake_kernel(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--mihomo-binary", str(binary),
         "--output-dir", str(tmp_path / "report")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 2
    assert "expected-version" in result.stderr


@pytest.mark.parametrize("version", ["latest", "Meta", "v1.19", "1.19.27"])
def test_kernel_version_must_be_an_exact_release(tmp_path, version):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--expected-version", version,
         "--output-dir", str(tmp_path / "report")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 2
    assert "release" in result.stderr


def test_parse_failure_cannot_be_reported_as_behavior_pass(tmp_path, monkeypatch):
    contract = harness()
    leaked_environment = tmp_path / "leaked-environment"
    monkeypatch.setenv("CLASH_POST_UP", "do not run")
    monkeypatch.setenv("CLASH_OVERRIDE_EXTERNAL_CONTROLLER", "127.0.0.1:9999")
    binary = fake_kernel(tmp_path, body=(
        "import os\n"
        f"open({str(leaked_environment)!r}, 'w').write(str([k for k in os.environ if k.startswith('CLASH_')]))\n"
        "print('fixture rejected')\nsys.exit(8)\n"
    ))
    report = contract.run_contract(binary, "v1.19.27", tmp_path / "report", 10)

    assert report["status"] == "fail"
    assert report["parse"]["status"] == "fail"
    assert report["parse"]["returncode"] == 8
    assert report["behavior"]["status"] == "skip"
    assert leaked_environment.read_text() == "[]"


def test_timed_out_kernel_is_reaped_and_reported(tmp_path):
    contract = harness()
    pid_file = tmp_path / "pid"
    binary = fake_kernel(tmp_path, body=(
        "import os, time\n"
        "if '-t' in sys.argv: sys.exit(0)\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    ))
    started = time.monotonic()
    report = contract.run_contract(binary, "v1.19.27", tmp_path / "report", 1)

    assert time.monotonic() - started < 4
    assert report["status"] == "fail"
    assert report["parse"]["status"] == "pass"
    assert report["behavior"]["status"] == "fail"
    assert pid_file.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_controlled_route_evidence_rejects_success_on_the_wrong_egress():
    contract = harness()
    with contract.local_endpoint("A") as endpoint:
        routes = contract.probe_routes(
            endpoint.server_port,
            {"exact.contract.test": "A", "provider.fixture.test": "B"},
            time.monotonic() + 3,
        )

    assert routes[0]["status"] == "pass"
    assert routes[0]["observed"] == {"route": "A", "authority": "exact.contract.test:80"}
    assert routes[1]["status"] == "fail"
    assert routes[1]["http_status"] == 200
    assert routes[1]["observed"]["route"] == "A"


@pytest.mark.parametrize("phase", ["version", "parse", "runtime"])
def test_failed_kernel_commands_reap_children_even_after_parent_exit(tmp_path, phase):
    contract = harness()
    child_pid = tmp_path / "child-pid"
    binary = tmp_path / "mihomo"
    binary.write_text(
        f"#!{sys.executable}\nimport subprocess, sys\n"
        "phase = 'version' if '-v' in sys.argv else 'parse' if '-t' in sys.argv else 'runtime'\n"
        f"if phase != {phase!r}:\n"
        " print('Mihomo Meta v1.19.27 test amd64')\n sys.exit(0)\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"open({str(child_pid)!r}, 'w').write(str(child.pid))\n"
        "sys.exit(8)\n"
    )
    binary.chmod(0o700)
    pid = None
    try:
        report = contract.run_contract(binary, "v1.19.27", tmp_path / "report", 2)
        assert report["status"] == "fail"
        assert child_pid.exists()
        pid = int(child_pid.read_text())
        for _ in range(30):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"{phase} command left its child process running")
    finally:
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("slow_part", ["headers", "body"])
def test_controlled_route_absolute_deadline_stops_trickling_response(slow_part):
    contract = harness()
    stop = threading.Event()
    payload = b'{"route": "A", "authority": "exact.contract.test:80"}'
    headers = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(payload)).encode() + b"\r\nConnection: close\r\n\r\n"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)

        def serve():
            connection = None
            try:
                connection, _ = listener.accept()
                connection.settimeout(2)
                connection.recv(4096)
                if slow_part == "body":
                    connection.sendall(headers)
                for byte in headers + payload if slow_part == "headers" else payload:
                    if stop.wait(0.02):
                        break
                    connection.sendall(bytes([byte]))
            except OSError:
                pass
            finally:
                if connection is not None:
                    connection.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            started = time.monotonic()
            evidence = contract.probe_routes(
                listener.getsockname()[1], {"exact.contract.test": "A"}, started + 0.15,
            )
            assert time.monotonic() - started < 0.65
            assert evidence[0]["status"] == "fail"
            assert "deadline" in evidence[0]["reason"]
        finally:
            stop.set()
            thread.join(timeout=2)
