"""The AI contract uses published Leo artifacts and reports only observed coverage."""
import importlib.util
import json
import hashlib
import http.client
import time
import subprocess
import sys
import socket
import threading
from pathlib import Path

import pytest

from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[1]


def harness():
    spec = importlib.util.spec_from_file_location("ai_client_contract", ROOT / "scripts/validate-ai-client-contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ai_modes_use_public_profiles_and_full_leo_without_touching_existing_database(tmp_path, monkeypatch):
    contract = harness()
    database = tmp_path / "untouched.db"
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(database))
    monkeypatch.setenv("SUBFLOW_PUBLIC_BASE_URL", "https://do-not-contact.example")
    ports = {name: 18080 + index for index, name in enumerate(contract.NODE_NAMES)}

    profiles = contract.generate_profiles(ports, 18090, 18091)

    assert not database.exists()
    assert set(profiles) == {"fixed", "manual", "fallback"}
    for mode, artifact in profiles.items():
        config = YAML(typ="safe").load(artifact["mihomo"])
        groups = {group["name"]: group for group in config["proxy-groups"]}
        assert len(config["rule-providers"]) == 9
        assert any(rule.startswith("GEOSITE,") for rule in config["rules"])
        for service in contract.SERVICES:
            group = groups[service["group"]]
            expected = ([service["primary"], service["backup"]] if mode == "fallback"
                        else [service["manual"] if mode == "manual" else service["primary"]])
            assert group["proxies"] == expected
            assert group["type"] == ("fallback" if mode == "fallback" else "select")
            assert f"{service['group']} = " in artifact["surge"]
        assert config["dns"] == {"enable": False}
        assert config["tun"] == {"enable": False}
        assert config["external-controller"] == "127.0.0.1:18091"
        assert "token" not in json.dumps(artifact["metadata"]).lower()


def test_dependency_cache_requires_recorded_digest_and_rejects_tampering(tmp_path):
    contract = harness()
    url = "https://raw.githubusercontent.com/example/rules/" + "a" * 40 + "/rules.yaml"
    filename = hashlib.sha256(url.encode()).hexdigest()
    content = b"payload: [example.com]\n"
    (tmp_path / filename).write_bytes(content)
    (tmp_path / "manifest.json").write_text(json.dumps({url: {
        "file": filename, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
    }}))

    assert contract.dependency_file(url, tmp_path, download=False).read_bytes() == content
    (tmp_path / filename).write_bytes(b"modified")
    with pytest.raises(ValueError, match="digest"):
        contract.dependency_file(url, tmp_path, download=False)
    with pytest.raises(FileNotFoundError):
        contract.dependency_file(url + "?other", tmp_path, download=False)


def test_local_ai_exit_records_authority_and_controls_health_without_forwarding():
    contract = harness()
    with contract.local_exit("US Primary") as endpoint:
        samples = contract.helpers.probe_routes(endpoint.server_port, {
            "api.openai.com": "US Primary", "claude.ai": "SG Primary",
            contract.CANARY_HOST: "US Primary",
        }, time.monotonic() + 2)
        assert [sample["status"] for sample in samples] == ["pass", "fail", "pass"]
        for healthy, status in ((True, 204), (False, 503)):
            endpoint.healthy = healthy
            connection = http.client.HTTPConnection("127.0.0.1", endpoint.server_port, timeout=1)
            connection.request("HEAD", contract.HEALTH_URL)
            response = connection.getresponse()
            assert response.status == status
            response.read()
            connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", endpoint.server_port, timeout=1)
        connection.request("GET", "http://unexpected.example/")
        assert connection.getresponse().status == 403
        connection.close()
        assert any(event["kind"] == "denied" for event in endpoint.events)


def test_ai_cli_without_kernel_reports_unavailable_and_saves_profiles(tmp_path):
    output = tmp_path / "report"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/validate-ai-client-contract.py"),
                             "--output-dir", str(output), "--dependency-cache", str(tmp_path / "cache")],
                            text=True, capture_output=True, cwd=ROOT)
    assert result.returncode == 2, result.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "unavailable"
    assert report["parse"]["status"] == "unavailable"
    assert report["behavior"]["status"] == "skip"
    assert "Surge iOS runtime" in report["not_tested"]
    assert len(list(output.glob("*-mihomo.yaml"))) == 3
    assert len(list(output.glob("*-surge.conf"))) == 3
    assert not (tmp_path / "cache").exists()


def test_contract_rejects_an_extra_generated_fallback_member_before_runtime():
    contract = harness()
    config = {"proxy-groups": [{"name": service["group"], "type": "fallback",
                               "proxies": [service["primary"], service["backup"]]}
                              for service in contract.SERVICES]}
    contract.validate_service_groups(config, "fallback")
    config["proxy-groups"][0]["proxies"].append("HK Trap")
    with pytest.raises(ValueError, match="OpenAI"):
        contract.validate_service_groups(config, "fallback")


@pytest.mark.parametrize("slow_part", ["headers", "body"])
def test_private_controller_total_deadline_stops_trickling_response(slow_part):
    contract = harness()
    payload = b'{"ok":true}'
    headers = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n"
    stop = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)

        def serve():
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(2)
                    connection.recv(4096)
                    if slow_part == "body":
                        connection.sendall(headers)
                    for byte in headers + payload if slow_part == "headers" else payload:
                        if stop.wait(0.05):
                            break
                        connection.sendall(bytes([byte]))
            except OSError:
                pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            started = time.monotonic()
            with pytest.raises(TimeoutError, match="deadline"):
                contract.controller_request(listener.getsockname()[1], "GET", "/proxies", started + 0.12)
            assert time.monotonic() - started < 0.65
        finally:
            stop.set()
            thread.join(timeout=2)
