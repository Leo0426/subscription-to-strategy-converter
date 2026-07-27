from __future__ import annotations

import gzip
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _fake_docker(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "if [ \"$1\" = save ]; then printf 'docker-image'; fi\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["DOCKER_LOG"] = str(log_path)
    return env, log_path


def _run(script: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROOT / "scripts" / script), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("alias", "platform", "suffix"),
    [
        ("x86", "linux/amd64", "amd64"),
        ("amd64", "linux/amd64", "amd64"),
        ("arm", "linux/arm64", "arm64"),
        ("arm64", "linux/arm64", "arm64"),
    ],
)
def test_build_script_maps_architecture_aliases(
    tmp_path: Path, alias: str, platform: str, suffix: str
) -> None:
    env, log_path = _fake_docker(tmp_path)

    result = _run("docker-build.sh", alias, "1.2.3", env=env)

    assert result.returncode == 0, result.stderr
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        f"buildx build --platform {platform} --load --tag subflow:1.2.3-{suffix} ."
    ]


def test_build_all_creates_one_loadable_image_per_architecture(tmp_path: Path) -> None:
    env, log_path = _fake_docker(tmp_path)

    result = _run("docker-build.sh", "all", "latest", env=env)

    assert result.returncode == 0, result.stderr
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "buildx build --platform linux/amd64 --load --tag subflow:latest-amd64 .",
        "buildx build --platform linux/arm64 --load --tag subflow:latest-arm64 .",
    ]


def test_export_all_writes_compressed_archives(tmp_path: Path) -> None:
    env, log_path = _fake_docker(tmp_path)
    output_dir = tmp_path / "dist"

    result = _run("docker-export.sh", "all", "1.2.3", str(output_dir), env=env)

    assert result.returncode == 0, result.stderr
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "image inspect subflow:1.2.3-amd64",
        "save subflow:1.2.3-amd64",
        "image inspect subflow:1.2.3-arm64",
        "save subflow:1.2.3-arm64",
    ]
    for arch in ("amd64", "arm64"):
        archive = output_dir / f"subflow-1.2.3-linux-{arch}.tar.gz"
        with gzip.open(archive, "rb") as stream:
            assert stream.read() == b"docker-image"


@pytest.mark.parametrize("script", ["docker-build.sh", "docker-export.sh"])
def test_scripts_reject_unknown_architecture(tmp_path: Path, script: str) -> None:
    env, _ = _fake_docker(tmp_path)

    result = _run(script, "sparc", env=env)

    assert result.returncode == 2
    assert "不支持的架构" in result.stderr


def test_openclash_e2e_script_documents_full_runtime_validation() -> None:
    result = subprocess.run(
        [str(ROOT / "scripts" / "openclash-e2e.sh"), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "创建长期 Profile" in result.stdout
    assert "Mihomo" in result.stdout
    assert "Google generate_204" in result.stdout


def test_openclash_e2e_script_requires_subscription_url() -> None:
    env = os.environ.copy()
    env.pop("SUBFLOW_E2E_SUBSCRIPTION_URL", None)

    result = _run("openclash-e2e.sh", env=env)

    assert result.returncode == 2
    assert "SUBFLOW_E2E_SUBSCRIPTION_URL" in result.stderr
