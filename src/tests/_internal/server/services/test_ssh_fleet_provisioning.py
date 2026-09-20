import shlex
from unittest.mock import Mock

import pytest

from dstack._internal.core.errors import SSHProvisioningError
from dstack._internal.server.services.ssh_fleets import provisioning


class _Stream:
    def __init__(self, value: str = "", exit_status: int = 0):
        self.value = value.encode()
        self.channel = Mock()
        self.channel.recv_exit_status.return_value = exit_status

    def read(self):
        return self.value


def _exec_result(stdout: str = "", stderr: str = ""):
    return None, _Stream(stdout), _Stream(stderr)


def test_run_shim_uses_systemd_when_pid1_is_systemd(monkeypatch):
    client = Mock()
    monkeypatch.setattr(provisioning, "_systemd_is_init", Mock(return_value=True))
    systemd = Mock()
    background = Mock()
    monkeypatch.setattr(provisioning, "run_shim_as_systemd_service", systemd)
    monkeypatch.setattr(provisioning, "run_shim_as_background_process", background)
    shell_env = Mock()
    monkeypatch.setattr(provisioning, "upload_shell_envs", shell_env)

    provisioning.run_shim(client, "/opt/dstack-shim", "/var/lib/dstack", {"A": "b"}, dev=True)

    systemd.assert_called_once_with(
        client=client,
        binary_path="/opt/dstack-shim",
        working_dir="/var/lib/dstack",
        dev=True,
    )
    background.assert_not_called()
    shell_env.assert_not_called()


def test_run_shim_falls_back_when_pid1_is_not_systemd(monkeypatch):
    client = Mock()
    monkeypatch.setattr(provisioning, "_systemd_is_init", Mock(return_value=False))
    systemd = Mock()
    background = Mock()
    monkeypatch.setattr(provisioning, "run_shim_as_systemd_service", systemd)
    monkeypatch.setattr(provisioning, "run_shim_as_background_process", background)
    shell_env = Mock()
    monkeypatch.setattr(provisioning, "upload_shell_envs", shell_env)

    provisioning.run_shim(client, "/opt/dstack-shim", "/var/lib/dstack", {"A": "b"}, dev=False)

    background.assert_called_once_with(
        client=client,
        binary_path="/opt/dstack-shim",
        working_dir="/var/lib/dstack",
    )
    systemd.assert_not_called()
    shell_env.assert_called_once_with(client=client, working_dir="/var/lib/dstack", envs={"A": "b"})


def test_upload_shell_envs_writes_shell_safe_environment(monkeypatch):
    client = Mock()
    client.exec_command.return_value = _exec_result()
    uploads = {}

    def capture_upload(_client, path, body):
        uploads[path] = body

    monkeypatch.setattr(provisioning, "sftp_upload", capture_upload)
    special = "a'$(touch /tmp/should-not-run)"

    provisioning.upload_shell_envs(
        client,
        "/var/lib/dstack",
        {"NORMAL": "hello world", "SPECIAL": special},
    )

    shell_env = uploads["/tmp/shim.env.sh"]
    assert "export NORMAL='hello world'" in shell_env
    assert f"export SPECIAL={shlex.quote(special)}" in shell_env
    command = client.exec_command.call_args.args[0]
    assert "chmod 600 /var/lib/dstack/shim.env.sh" in command


def test_upload_shell_envs_rejects_invalid_shell_variable_name(monkeypatch):
    client = Mock()
    monkeypatch.setattr(provisioning, "sftp_upload", Mock())

    with pytest.raises(SSHProvisioningError, match="Invalid environment variable name"):
        provisioning.upload_shell_envs(client, "/var/lib/dstack", {"BAD-NAME": "value"})


def test_background_shim_uses_owned_pidfile_and_shell_env():
    client = Mock()
    client.exec_command.return_value = _exec_result()

    provisioning.run_shim_as_background_process(
        client, "/opt/dstack/dstack-shim", "/var/lib/dstack"
    )

    command = client.exec_command.call_args.args[0]
    assert "/var/lib/dstack/shim.pid" in command
    assert "/var/lib/dstack/shim.log" in command
    assert "/var/lib/dstack/shim.env.sh" in command
    assert "/proc/$old_pid/cmdline" in command
    assert "trap " in command
    assert "nohup sh -c" in command
    assert "\x00" not in command


def test_background_shim_rejects_failed_start():
    client = Mock()
    client.exec_command.return_value = (
        None,
        _Stream(exit_status=1),
        _Stream(),
    )

    with pytest.raises(SSHProvisioningError, match="exit_status: 1"):
        provisioning.run_shim_as_background_process(
            client, "/opt/dstack/dstack-shim", "/var/lib/dstack"
        )
