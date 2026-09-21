import json
import shlex
import time
from typing import Optional

from dstack._internal.core.backends.base.compute import (
    get_dstack_runner_download_url,
    normalize_arch,
)
from dstack._internal.core.consts import DSTACK_RUNNER_HTTP_PORT, DSTACK_RUNNER_SSH_PORT
from dstack._internal.core.models.backends.base import BackendType
from dstack._internal.core.models.runs import JobProvisioningData
from dstack._internal.server.services.ssh_fleets.provisioning import get_paramiko_connection
from dstack._internal.utils.ssh import pkey_from_str

EXTERNAL_RUNNER_KIND = "vastai-import"


def build_external_runner_backend_data(provider_instance_id: str) -> str:
    return json.dumps(
        {
            "dstack_external_runner": EXTERNAL_RUNNER_KIND,
            "provider_instance_id": provider_instance_id,
        }
    )


def get_external_runner_provider_instance_id(
    jpd: Optional[JobProvisioningData],
) -> Optional[str]:
    if jpd is None or jpd.backend_data is None:
        return None
    try:
        data = json.loads(jpd.backend_data)
    except (TypeError, ValueError):
        return None
    if data.get("dstack_external_runner") != EXTERNAL_RUNNER_KIND:
        return None
    provider_instance_id = data.get("provider_instance_id")
    return str(provider_instance_id) if provider_instance_id is not None else None


def is_external_runner(jpd: Optional[JobProvisioningData]) -> bool:
    if jpd is None or jpd.backend_data is None:
        return False
    if jpd.backend != BackendType.REMOTE or jpd.base_backend != BackendType.VASTAI:
        return False
    try:
        data = json.loads(jpd.backend_data)
    except (TypeError, ValueError):
        return False
    return data.get("dstack_external_runner") == EXTERNAL_RUNNER_KIND


def ensure_external_runner_started(
    ssh_private_key: str,
    jpd: JobProvisioningData,
    project_ssh_public_key: str,
    job_id: str,
) -> None:
    if not is_external_runner(jpd):
        return
    if jpd.hostname is None or jpd.ssh_port is None:
        raise ValueError("Imported Vast.ai instance has no SSH endpoint")

    pkey = pkey_from_str(ssh_private_key)
    with get_paramiko_connection(
        jpd.username,
        jpd.hostname,
        jpd.ssh_port,
        [pkey],
    ) as client:
        runner_dir = "/tmp/dstack-external-runner"
        runner_path = f"{runner_dir}/dstack-runner"
        job_id_path = f"{runner_dir}/job-id"
        pid_path = f"{runner_dir}/runner.pid"
        if _remote_runner_healthy(client) and _remote_file_equals(client, job_id_path, job_id):
            return

        _stop_previous_runner(client, pid_path, runner_path)

        _, stdout, stderr = client.exec_command("uname -m", timeout=10)
        arch_raw = stdout.read().decode().strip()
        arch_err = stderr.read().decode().strip()
        if arch_err:
            raise RuntimeError(f"Failed to detect imported Vast.ai architecture: {arch_err}")
        arch = normalize_arch(arch_raw).value
        runner_url = get_dstack_runner_download_url(arch)
        state_dir = f"{runner_dir}/state"
        log_path = f"{runner_dir}/runner.log"
        public_key = shlex.quote(project_ssh_public_key.strip())
        url = shlex.quote(runner_url)

        script = f"""
set -eu
mkdir -p {runner_dir}
tmp={runner_path}.tmp
if command -v curl >/dev/null 2>&1; then
  curl -fL --connect-timeout 30 --max-time 240 -o "$tmp" {url}
elif command -v wget >/dev/null 2>&1; then
  wget -q -O "$tmp" {url}
else
  echo "curl or wget is required to install dstack-runner" >&2
  exit 127
fi
chmod +x "$tmp"
mv "$tmp" {runner_path}
rm -rf {state_dir}
mkdir -p {state_dir}
printf %s {shlex.quote(job_id)} > {job_id_path}
nohup {runner_path} --log-level 6 start \
  --temp-dir {state_dir} \
  --http-port {DSTACK_RUNNER_HTTP_PORT} \
  --ssh-port {DSTACK_RUNNER_SSH_PORT} \
  --ssh-authorized-key {public_key} \
  > {log_path} 2>&1 </dev/null &
echo $! > {pid_path}
"""
        _, stdout, stderr = client.exec_command(script, timeout=300)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            raise RuntimeError(
                f"Failed to start dstack-runner on imported Vast.ai instance: {err or out}"
            )

        for _ in range(20):
            if _remote_runner_healthy(client):
                return
            time.sleep(0.5)
        raise RuntimeError(
            "dstack-runner did not become healthy on the imported Vast.ai instance"
        )


def _remote_file_equals(client, path: str, expected: str) -> bool:
    try:
        _, stdout, _ = client.exec_command(f"cat {shlex.quote(path)} 2>/dev/null", timeout=5)
        value = stdout.read().decode().strip()
        return stdout.channel.recv_exit_status() == 0 and value == expected
    except Exception:
        return False


def _stop_previous_runner(client, pid_path: str, runner_path: str) -> None:
    quoted = shlex.quote(pid_path)
    runner_quoted = shlex.quote(runner_path)
    script = (
        f'if test -s {quoted}; then '
        f'pid="$(cat {quoted})"; '
        'case "$pid" in (*[!0-9]*|"") exit 0;; esac; '
        'if kill -0 "$pid" 2>/dev/null; then '
        f'exe="$(readlink /proc/$pid/exe 2>/dev/null || true)"; '
        f'test "$exe" = {runner_quoted} || exit 0; '
        'kill "$pid" 2>/dev/null || true; '
        'i=0; while kill -0 "$pid" 2>/dev/null && test "$i" -lt 20; do '
        'sleep 0.1; i=$((i+1)); done; '
        'kill -9 "$pid" 2>/dev/null || true; '
        'fi; fi'
    )
    _, stdout, stderr = client.exec_command(script, timeout=10)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if stdout.channel.recv_exit_status() != 0:
        raise RuntimeError(f"Failed to stop previous external dstack-runner: {err or out}")


def _remote_runner_healthy(client) -> bool:
    cmd = (
        "if command -v curl >/dev/null 2>&1; then "
        f"curl -fsS --max-time 2 http://127.0.0.1:{DSTACK_RUNNER_HTTP_PORT}/api/healthcheck "
        ">/dev/null 2>&1; "
        "elif command -v wget >/dev/null 2>&1; then "
        f"wget -q -T 2 -O /dev/null http://127.0.0.1:{DSTACK_RUNNER_HTTP_PORT}/api/healthcheck; "
        "else exit 1; fi"
    )
    try:
        _, stdout, _ = client.exec_command(cmd, timeout=5)
        return stdout.channel.recv_exit_status() == 0
    except Exception:
        return False
