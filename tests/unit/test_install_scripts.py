"""Behavior of ``scripts/install.sh`` and ``scripts/uninstall.sh`` against fake system tools.

The scripts run in a throwaway HOME with a PATH that holds only fake ``uv``, ``systemctl``,
``gnome-extensions`` and ``gdbus`` plus symlinks to a whitelist of coreutils, so the real tools can
never run. Every fake appends its argv to a log file, which is also the proof the fakes were used.
"""

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from tests.unit.install_sandbox import (
    ACTIVATION,
    COREUTILS,
    DESKTOP_ENTRY,
    EXTENSION,
    INSTALL,
    ROOT,
    SCRIPTS,
    TRAY_BUS_NAME,
    TRAY_ICONS,
    UNINSTALL,
    UNIT,
    Env,
    is_desktop_query,
    sandbox_fixture,  # noqa: F401  # the `sandbox` fixture
)


def _is_restart(line: str) -> bool:
    return line.startswith("systemctl") and "restart" in line


def _index(calls: list[str], prefix: str) -> int:
    return next(i for i, line in enumerate(calls) if line.startswith(prefix))


TOOLS = ["systemctl", "gnome-extensions", "uv", "gdbus"]


def test_scripts_only_reach_the_fakes_never_a_decoy_on_a_normal_path(sandbox: Env) -> None:
    decoys = sandbox.home / ".local" / "bin"
    decoys.mkdir(parents=True)
    marker = sandbox.log.parent / "REAL-CALLED"
    for tool in TOOLS:
        decoy = decoys / tool
        decoy.write_text(f'#!/usr/bin/env bash\necho "{tool}" >> "{marker}"\n')
        decoy.chmod(0o755)
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle")
    sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle")
    called = {line.split()[0] for line in sandbox.calls()}
    assert set(TOOLS) <= called  # the fakes ran
    assert not marker.exists()  # the decoys never did


def test_sandbox_path_holds_real_files_for_tools_and_only_whitelisted_links(
    sandbox: Env,
) -> None:
    fakes = Path(sandbox.env["PATH"])
    assert fakes.is_dir()
    assert ":" not in sandbox.env["PATH"]  # a single entry, nothing inherited
    assert fakes == sandbox.home.parent / "fakes"  # exactly the fakes directory
    for tool in TOOLS:
        assert not (fakes / tool).is_symlink()
    links = {p.name for p in fakes.iterdir() if p.is_symlink()}
    assert links <= set(COREUTILS)


@pytest.mark.parametrize("script", sorted(SCRIPTS.glob("*.sh")))
@pytest.mark.parametrize("tool", TOOLS)
def test_scripts_never_hardcode_an_absolute_path_to_a_tool(script: Path, tool: str) -> None:
    pattern = rf"/(?:usr|bin|sbin|opt|nix)[\w./-]*/{re.escape(tool)}\b"
    assert not re.search(pattern, script.read_text())


def test_install_uses_a_python_312_uv_tool_install_of_the_repo(sandbox: Env) -> None:
    result = sandbox.run(INSTALL)
    assert result.returncode == 0, result.stderr
    expected = f"uv tool install --force --reinstall --python 3.12 meeting-scribe[ml] @ {ROOT}"
    assert expected in sandbox.calls()  # the ml extra: faster-whisper, torch, pyannote


def _prefetches(sandbox: Env) -> list[str]:
    return [line for line in sandbox.calls() if line.startswith("meeting-scribe prefetch-models")]


def _summary(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout.split("meeting-scribe installed")[1]


MODELS_PENDING = (
    "  models:    PENDING: add your Hugging Face token in Preferences and press Download models"
)


def test_install_reports_models_ready_when_they_are_cached(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    assert "  models:    ready (cached)" in _summary(result)
    (version,) = sandbox.versions
    assert _prefetches(sandbox) == [
        f"meeting-scribe prefetch-models --check (from {sandbox.root}/current/bin/meeting-scribe)"
    ]
    assert version.is_dir()


def test_install_reports_models_pending_and_still_succeeds(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_MODELS_CACHED="0", **IDLE)
    assert result.returncode == 0, result.stderr  # pending is not a failure
    summary = _summary(result)
    assert MODELS_PENDING in summary
    assert "recording works meanwhile" in summary
    # Download models in Preferences is the path now: no command is suggested as a stopgap
    assert "until then" not in summary
    assert "prefetch-models" not in summary
    # the service, the unit, the activation file, the review app and the extension are installed
    assert sandbox.exe.exists()
    assert sandbox.unit_path.exists()
    assert sandbox.activation_path.exists()
    assert sandbox.desktop_entry.exists()
    assert "extension: enabled" in summary


def test_the_installer_never_downloads_the_models(sandbox: Env) -> None:
    sandbox.run(INSTALL, FAKE_MODELS_CACHED="0", **IDLE)
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    assert _prefetches(sandbox)
    assert all(line.split()[2] == "--check" for line in _prefetches(sandbox))
    assert not Path(f"{sandbox.log}.download").exists()  # the fake records any other call there


def test_the_install_no_longer_has_a_download_step_or_its_timeout_knob() -> None:
    for script in SCRIPTS.glob("*.sh"):
        text = script.read_text()
        assert "PREFETCH_TIMEOUT" not in text, script
        assert "Downloading the speech models" not in text, script
        assert "hf auth login" not in text, script


def test_a_hung_models_check_is_cut_off_and_reported_as_pending(sandbox: Env) -> None:
    result = sandbox.run(
        INSTALL, FAKE_MODELS_CHECK_SLEEP="30", MEETING_SCRIBE_MODELS_CHECK_TIMEOUT="1", **IDLE
    )
    assert result.returncode == 0, result.stderr
    assert MODELS_PENDING in _summary(result)


def test_the_models_line_is_still_reported_after_a_rollback(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    summary = _summary(result)
    assert "rolled back" in summary
    assert "  models:    ready (cached)" in summary  # the version in charge is checked


def test_no_models_check_when_the_install_is_refused_or_uv_fails(sandbox: Env) -> None:
    sandbox.run(INSTALL, FAKE_GDBUS_MODE="recording")
    sandbox.run(INSTALL, FAKE_UV_FAIL="1", **IDLE)
    assert _prefetches(sandbox) == []


def test_install_renders_the_unit_with_the_absolute_exec_start(sandbox: Env) -> None:
    sandbox.run(INSTALL)
    text = sandbox.unit_path.read_text()
    assert f"ExecStart={sandbox.exe}\n" in text
    assert "@EXEC_START@" not in text


def test_install_copies_the_activation_file(sandbox: Env) -> None:
    sandbox.run(INSTALL)
    source = ROOT / "packaging" / "dbus" / ACTIVATION
    assert sandbox.activation_path.read_text() == source.read_text()


def test_first_install_reloads_then_enables_and_starts(sandbox: Env) -> None:
    sandbox.run(INSTALL)
    calls = sandbox.calls()
    reload_at = _index(calls, "systemctl --user daemon-reload")
    enable_at = _index(calls, f"systemctl --user enable --now {UNIT}")
    assert reload_at < enable_at
    assert not any("restart" in line for line in calls)


def test_reinstall_restarts_the_running_service_when_idle(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle")
    calls = sandbox.calls()
    assert result.returncode == 0, result.stderr
    assert f"systemctl --user restart {UNIT}" in calls
    assert _index(calls, "systemctl --user daemon-reload") < _index(
        calls, "systemctl --user restart"
    )


def test_install_refuses_while_recording_before_touching_anything(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording")
    assert result.returncode != 0
    message = (result.stdout + result.stderr).lower()
    assert "a recording is in progress" in message
    assert "top-bar menu" in message
    calls = sandbox.calls()
    assert not any(line.startswith(("uv tool install", "gnome-extensions")) for line in calls)
    assert not any(line.startswith("systemctl") and "is-active" not in line for line in calls)
    assert not sandbox.unit_path.exists()
    assert not sandbox.activation_path.exists()


GDBUS_FAILURES = [
    "owner-fail",
    "owner-garbage",
    "get-fail",
    "get-garbage",
    "get-empty",
    "weird",
]


@pytest.mark.parametrize("mode", GDBUS_FAILURES)
def test_install_fails_closed_when_the_state_cannot_be_read(sandbox: Env, mode: str) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE=mode)
    assert result.returncode != 0
    assert "could not be read" in (result.stdout + result.stderr).lower()
    assert not any(line.startswith("uv tool install") for line in sandbox.calls())
    assert not any(_is_restart(line) for line in sandbox.calls())
    assert not sandbox.unit_path.exists()


@pytest.mark.parametrize("mode", GDBUS_FAILURES)
def test_uninstall_fails_closed_when_the_state_cannot_be_read(sandbox: Env, mode: str) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE=mode)
    assert result.returncode != 0
    assert "could not be read" in (result.stdout + result.stderr).lower()
    assert sandbox.unit_path.exists()
    assert not any("disable" in line or "uninstall" in line for line in sandbox.calls())


def test_uninstall_proceeds_with_force_when_the_state_cannot_be_read(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="get-fail", FORCE="1")
    assert result.returncode == 0, result.stderr
    assert not sandbox.unit_path.exists()


@pytest.mark.parametrize("mode", ["get-fail", "weird"])
def test_install_proceeds_with_force_when_the_state_is_unknown(sandbox: Env, mode: str) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="0", FAKE_GDBUS_MODE=mode, FORCE="1")
    assert result.returncode == 0, result.stderr
    assert any(line.startswith("uv tool install") for line in sandbox.calls())


@pytest.mark.parametrize("mode", ["get-fail", "weird"])
def test_forced_install_restarts_an_active_service_whose_state_stays_unknown(
    sandbox: Env, mode: str
) -> None:
    # FORCE=1 is the documented remedy for a wedged service: it must actually replace it.
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE=mode, FORCE="1")
    assert result.returncode == 0, result.stderr
    assert f"systemctl --user restart {UNIT}" in sandbox.calls()


def test_forced_install_still_never_restarts_a_recording(sandbox: Env) -> None:
    result = sandbox.run(
        INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-recording", FORCE="1"
    )
    assert result.returncode == 0, result.stderr
    assert not any(_is_restart(line) for line in sandbox.calls())
    assert "restart:   skipped" in result.stdout


def test_install_unknown_message_names_the_remedies(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_GDBUS_MODE="weird")
    message = result.stdout + result.stderr
    assert f"systemctl --user stop {UNIT}" in message
    assert "FORCE=1" in message


def test_install_never_proceeds_while_recording_even_with_force(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording", FORCE="1")
    assert result.returncode != 0
    assert not any(line.startswith("uv tool install") for line in sandbox.calls())


def test_install_treats_a_stopped_service_as_safe(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_GDBUS_MODE="stopped")
    assert result.returncode == 0, result.stderr


def test_install_skips_the_restart_when_a_recording_starts_during_the_install(
    sandbox: Env,
) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-recording")
    assert result.returncode == 0, result.stderr
    assert not any(_is_restart(line) for line in sandbox.calls())
    assert "warning" in result.stderr.lower()
    assert "recording" in result.stderr.lower()
    assert "restart:   skipped" in result.stdout
    assert any(line.startswith("gnome-extensions install") for line in sandbox.calls())


def test_daemon_reload_failure_has_its_own_cause(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_GDBUS_MODE="idle", FAKE_SYSTEMCTL_FAIL="daemon-reload")
    assert result.returncode != 0
    assert "daemon-reload failed" in result.stdout
    assert "make run" not in result.stdout


def test_gdbus_calls_are_bounded_by_a_timeout(sandbox: Env) -> None:
    sandbox.run(INSTALL, FAKE_GDBUS_MODE="idle")
    gdbus_calls = [line for line in sandbox.calls() if line.startswith("gdbus ")]
    assert gdbus_calls
    assert all("--timeout" in line for line in gdbus_calls)


@pytest.mark.parametrize(
    ("active", "failing"), [("0", "enable"), ("1", "restart")], ids=["first-install", "reinstall"]
)
def test_start_failure_still_installs_the_extension_and_explains(
    sandbox: Env, active: str, failing: str
) -> None:
    result = sandbox.run(
        INSTALL, FAKE_UNIT_ACTIVE=active, FAKE_GDBUS_MODE="idle", FAKE_SYSTEMCTL_FAIL=failing
    )
    assert result.returncode != 0
    assert any(line.startswith("gnome-extensions install --force") for line in sandbox.calls())
    assert "NOT active" in result.stdout
    assert "make run" in result.stdout
    assert "Ctrl+C" in result.stdout
    assert f"systemctl --user restart {UNIT}" in result.stdout


def test_install_packs_installs_and_enables_the_extension(sandbox: Env) -> None:
    result = sandbox.run(INSTALL)
    calls = sandbox.calls()
    assert result.returncode == 0, result.stderr
    pack = _index(calls, "gnome-extensions pack")
    install = _index(calls, "gnome-extensions install --force")
    enable = _index(calls, f"gnome-extensions enable {EXTENSION}")
    assert pack < install < enable
    assert f"{EXTENSION}.shell-extension.zip" in calls[install]


def test_wayland_log_out_hint_when_enable_fails(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_EXT_ENABLE_FAIL="1", FAKE_EXT_KNOWN="0")
    assert result.returncode == 0, result.stderr
    assert "log out and back in" in result.stdout
    assert any(line.startswith("systemctl --user enable --now") for line in sandbox.calls())


def test_install_prints_a_summary_with_the_config_location(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle")
    settings = sandbox.home / ".config" / "meeting-scribe" / "settings.json"
    assert "Preferences" in result.stdout
    assert str(settings) in result.stdout
    assert "developer override" in result.stdout
    assert "service" in result.stdout.lower()
    assert "extension" in result.stdout.lower()


def _installed(sandbox: Env) -> None:
    sandbox.run(INSTALL)
    sandbox.log.write_text("")


def test_uninstall_removes_files_and_disables_everything(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle")
    calls = sandbox.calls()
    assert result.returncode == 0, result.stderr
    assert not sandbox.unit_path.exists()
    assert not sandbox.activation_path.exists()
    assert f"systemctl --user disable --now {UNIT}" in calls
    assert f"gnome-extensions disable {EXTENSION}" in calls
    assert f"gnome-extensions uninstall {EXTENSION}" in calls
    assert "uv tool uninstall meeting-scribe" in calls
    assert _index(calls, "systemctl --user disable") < _index(
        calls, "systemctl --user daemon-reload"
    )


def test_uninstall_refuses_while_recording(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording")
    assert result.returncode != 0
    assert "a recording is in progress" in (result.stdout + result.stderr).lower()
    assert sandbox.unit_path.exists()
    assert not any("disable" in line or "uninstall" in line for line in sandbox.calls())


def test_uninstall_proceeds_while_recording_with_force(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording", FORCE="1")
    assert result.returncode == 0, result.stderr
    assert not sandbox.unit_path.exists()


def test_uninstall_is_idempotent_on_a_clean_home(sandbox: Env) -> None:
    result = sandbox.run(UNINSTALL)
    assert result.returncode == 0, result.stderr


def test_uninstall_keeps_the_user_config(sandbox: Env) -> None:
    config = sandbox.home / ".config" / "meeting-scribe" / ".env"
    config.parent.mkdir(parents=True)
    config.write_text("LOG_LEVEL=DEBUG\n")
    _installed(sandbox)
    sandbox.run(UNINSTALL)
    assert config.read_text() == "LOG_LEVEL=DEBUG\n"


@pytest.mark.parametrize("script", sorted(SCRIPTS.glob("*.sh")))
def test_scripts_never_reference_the_meetings_directory(script: Path) -> None:
    assert "Documents/meetings" not in script.read_text()


# --- F2.7: versioned installs, truthful summary, one FORCE contract, escaped rendering ---

IDLE = {"FAKE_GDBUS_MODE": "idle"}


def test_install_gives_each_version_its_own_uv_tool_and_bin_dirs(sandbox: Env) -> None:
    result = sandbox.run(INSTALL)
    assert result.returncode == 0, result.stderr
    (version,) = sandbox.versions
    env_line = next(line for line in sandbox.calls() if line.startswith("uv-env"))
    assert f"UV_TOOL_DIR={version / 'tools'}" in env_line
    assert f"UV_TOOL_BIN_DIR={version / 'bin'}" in env_line
    assert (sandbox.root / "current").is_symlink()
    assert (sandbox.root / "current").resolve() == version.resolve()
    assert sandbox.exe.is_file()


def test_exec_start_path_is_stable_across_versions(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    first = sandbox.unit_exec_start
    sandbox.run(INSTALL, **IDLE)
    assert sandbox.unit_exec_start == first == f"ExecStart={sandbox.exe}"


def test_second_install_keeps_the_running_version_while_the_restart_is_skipped(
    sandbox: Env,
) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-recording")
    assert result.returncode == 0, result.stderr
    assert "restart:   skipped" in result.stdout
    assert first in sandbox.versions  # the recording may still run from it
    assert len(sandbox.versions) == 2
    current = (sandbox.root / "current").resolve()
    assert current != first.resolve()
    assert current in [v.resolve() for v in sandbox.versions]


def test_old_versions_are_pruned_after_a_successful_restart(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-recording")
    assert len(sandbox.versions) == 2
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    assert result.returncode == 0, result.stderr
    assert f"systemctl --user restart {UNIT}" in sandbox.calls()
    (only,) = sandbox.versions
    assert (sandbox.root / "current").resolve() == only.resolve()


def test_nothing_is_pruned_when_the_restart_failed(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert result.returncode != 0
    assert len(sandbox.versions) == 2


def test_a_failed_uv_install_leaves_current_and_the_unit_untouched(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    unit_before = sandbox.unit_path.read_text()
    result = sandbox.run(INSTALL, FAKE_UV_FAIL="1", **IDLE)
    assert result.returncode != 0
    assert sandbox.versions == [first]
    assert (sandbox.root / "current").resolve() == first.resolve()
    assert sandbox.unit_path.read_text() == unit_before


def _legacy_unit(sandbox: Env) -> None:
    sandbox.unit_path.parent.mkdir(parents=True)
    sandbox.unit_path.write_text("[Service]\nExecStart=/legacy/.local/bin/meeting-scribe\n")


def test_migration_removes_the_old_uv_tool_only_after_the_new_unit_is_active(
    sandbox: Env,
) -> None:
    _legacy_unit(sandbox)
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    calls = sandbox.calls()
    assert result.returncode == 0, result.stderr
    assert sandbox.unit_exec_start == f"ExecStart={sandbox.exe}"
    assert _index(calls, f"systemctl --user restart {UNIT}") < _index(
        calls, "uv tool uninstall meeting-scribe"
    )


def test_migration_keeps_the_old_uv_tool_while_the_old_service_may_still_run(
    sandbox: Env,
) -> None:
    _legacy_unit(sandbox)
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-recording")
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("uv tool uninstall") for line in sandbox.calls())
    assert "uv tool uninstall meeting-scribe" in result.stdout  # tells the user how


def test_a_regular_install_never_uninstalls_a_uv_tool(sandbox: Env) -> None:
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    assert not any(line.startswith("uv tool uninstall") for line in sandbox.calls())


def test_uninstall_removes_the_versions_tree(sandbox: Env) -> None:
    _installed(sandbox)
    assert sandbox.root.exists()
    sandbox.run(UNINSTALL, **IDLE)
    assert not sandbox.root.exists()


def test_uninstall_keeps_other_data_next_to_the_versions(sandbox: Env) -> None:
    # Only what the installer created goes away; e.g. a future model cache must survive.
    _installed(sandbox)
    other = sandbox.root / "models" / "keep.bin"
    other.parent.mkdir(parents=True)
    other.write_text("x")
    sandbox.run(UNINSTALL, **IDLE)
    assert other.exists()
    assert not (sandbox.root / "versions").exists()
    assert not (sandbox.root / "current").is_symlink()


@pytest.mark.parametrize("failing", ["pack", "install"])
def test_extension_step_failure_still_prints_the_summary_and_exits_non_zero(
    sandbox: Env, failing: str
) -> None:
    result = sandbox.run(INSTALL, FAKE_EXT_FAIL=failing, **IDLE)
    assert result.returncode != 0
    assert "meeting-scribe installed" in result.stdout
    assert "service:" in result.stdout
    assert f"extension: FAILED ({failing}" in result.stdout
    assert f"ext {failing} failed" in result.stderr


def test_extension_failure_keeps_the_service_failure_cause(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_EXT_FAIL="pack", FAKE_SYSTEMCTL_FAIL="enable", **IDLE)
    assert result.returncode != 0
    assert "NOT active" in result.stdout
    assert "extension: FAILED" in result.stdout


def test_log_out_hint_does_not_claim_a_running_service_when_it_is_not_active(
    sandbox: Env,
) -> None:
    result = sandbox.run(
        INSTALL, FAKE_EXT_KNOWN="0", FAKE_EXT_ENABLE_FAIL="1", FAKE_SYSTEMCTL_FAIL="enable", **IDLE
    )
    assert "log out and back in" in result.stdout
    assert "already running" not in result.stdout
    assert "NOT active" in result.stdout


def test_log_out_hint_mentions_the_service_when_it_is_active(sandbox: Env) -> None:
    result = sandbox.run(
        INSTALL, FAKE_EXT_KNOWN="0", FAKE_EXT_ENABLE_FAIL="1", FAKE_UNIT_ACTIVE="1", **IDLE
    )
    assert "log out and back in" in result.stdout
    assert "service is already running" in result.stdout


def test_start_failure_cause_lists_the_bus_conflict_as_one_possible_cause(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_SYSTEMCTL_FAIL="enable", **IDLE)
    assert "possible cause" in result.stdout.lower()
    assert "make run" in result.stdout
    assert f"journalctl --user -u {UNIT}" in result.stdout


def test_uninstall_uses_the_shared_state_check_even_with_force(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording", FORCE="1")
    assert result.returncode == 0, result.stderr
    assert any(line.startswith("gdbus ") for line in sandbox.calls())
    assert "FORCE=1" in result.stderr
    assert "recording" in result.stderr.lower()


def test_install_force_never_overrides_a_recording_detected_at_the_first_check(
    sandbox: Env,
) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording", FORCE="1")
    assert result.returncode != 0
    assert "a recording is in progress" in result.stderr.lower()
    assert not any(line.startswith("uv tool install") for line in sandbox.calls())
    assert not sandbox.unit_path.exists()


def test_uninstall_force_proceeds_over_a_recording_by_intent(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording", FORCE="1")
    assert result.returncode == 0, result.stderr
    assert f"systemctl --user disable --now {UNIT}" in sandbox.calls()
    assert not sandbox.unit_path.exists()


def test_unit_is_rendered_verbatim_for_a_path_with_sed_special_characters(
    sandbox: Env, tmp_path: Path
) -> None:
    tricky = tmp_path / "a&b|c"
    result = sandbox.run(INSTALL, XDG_DATA_HOME=str(tricky), **IDLE)
    assert result.returncode == 0, result.stderr
    expected = f"ExecStart={tricky}/meeting-scribe/current/bin/meeting-scribe"
    lines = sandbox.unit_path.read_text().splitlines()
    assert expected in lines
    assert "@EXEC_START@" not in sandbox.unit_path.read_text()


def test_reinstall_with_a_failing_enable_does_not_restart_and_explains(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="enable", **IDLE)
    assert result.returncode != 0
    assert not any(_is_restart(line) for line in sandbox.calls())
    assert "NOT active" in result.stdout
    assert f"journalctl --user -u {UNIT}" in result.stdout


def test_reinstall_restarts_when_the_service_exited_during_the_install(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-none")
    assert result.returncode == 0, result.stderr
    assert f"systemctl --user restart {UNIT}" in sandbox.calls()
    assert "restart:   skipped" not in result.stdout


# --- F2.7b: verify before switching, roll back on a failed start, explicit legacy detection ---


def _restarts(sandbox: Env) -> list[str]:
    return [line for line in sandbox.calls() if _is_restart(line)]


def test_a_failed_restart_on_upgrade_rolls_back_to_the_previous_version(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert result.returncode != 0
    assert (sandbox.root / "current").resolve() == first.resolve()
    assert len(_restarts(sandbox)) == 2  # the new version, then the previous one
    assert len(sandbox.versions) == 2  # the new one is kept for inspection
    assert f"rolled back to {first.name}" in result.stdout
    assert "kept in versions/" in result.stdout
    assert f"journalctl --user -u {UNIT}" in result.stdout
    assert sandbox.unit_exec_start == f"ExecStart={sandbox.exe}"


def test_a_failed_restart_on_migration_restores_the_legacy_unit_and_keeps_the_uv_tool(
    sandbox: Env,
) -> None:
    _legacy_unit(sandbox)
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert result.returncode != 0
    assert sandbox.unit_exec_start == "ExecStart=/legacy/.local/bin/meeting-scribe"
    assert not any(line.startswith("uv tool uninstall") for line in sandbox.calls())
    assert not (sandbox.root / "current").is_symlink()
    assert len(sandbox.versions) == 1  # kept for inspection
    assert "legacy" in result.stdout
    assert len(_restarts(sandbox)) == 2


def test_a_missing_executable_never_switches_and_removes_the_orphan(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    unit_before = sandbox.unit_path.read_text()
    result = sandbox.run(INSTALL, FAKE_UV_NO_EXE="1", **IDLE)
    assert result.returncode != 0
    assert "was not created" in result.stderr
    assert sandbox.versions == [first]
    assert (sandbox.root / "current").resolve() == first.resolve()
    assert sandbox.unit_path.read_text() == unit_before


def test_a_leftover_temp_link_from_an_interrupted_run_does_not_break_the_install(
    sandbox: Env,
) -> None:
    sandbox.run(INSTALL, **IDLE)
    # `exec` keeps the pid, so the leftover has exactly the name the old script would reuse.
    prelude = f'mkdir -p "{sandbox.root}"; ln -s x "{sandbox.root}/current.new.$$"; exec "$0" "$1"'
    result = subprocess.run(  # noqa: S603  # fixed argv, sandboxed PATH
        [sandbox.env["BASH"], "-c", prelude, sandbox.env["BASH"], str(INSTALL)],
        env={**sandbox.env, **IDLE},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert (sandbox.root / "current").resolve() in [v.resolve() for v in sandbox.versions]


def test_a_hand_edited_unit_is_not_mistaken_for_the_legacy_install(sandbox: Env) -> None:
    sandbox.unit_path.parent.mkdir(parents=True)
    sandbox.unit_path.write_text("[Service]\nExecStart=/opt/custom/meeting-scribe\n")
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("uv tool uninstall") for line in sandbox.calls())


def test_the_legacy_install_is_the_unit_pointing_at_the_default_uv_tool_bin_dir(
    sandbox: Env,
) -> None:
    sandbox.unit_path.parent.mkdir(parents=True)
    sandbox.unit_path.write_text("[Service]\nExecStart=/elsewhere/bin/meeting-scribe\n")
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_UV_TOOL_BIN="/elsewhere/bin", **IDLE)
    calls = sandbox.calls()
    assert "uv tool uninstall meeting-scribe" in calls
    assert "uv tool dir --bin" in calls
    query_env = next(line for line in calls if line.startswith("uv-env-dir"))
    assert query_env == "uv-env-dir UV_TOOL_DIR= UV_TOOL_BIN_DIR="


# -- the review app (F4.6) -------------------------------------------------------------------


def _version_dir(sandbox: Env) -> Path:
    (version,) = sandbox.versions
    return version


def test_install_copies_the_review_app_into_the_version_without_bytecode(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    package = _version_dir(sandbox) / "app" / "meeting_scribe_review"
    assert (package / "__main__.py").is_file()
    assert (package / "ui" / "window.py").is_file()
    assert not list((_version_dir(sandbox) / "app").rglob("__pycache__"))


def test_the_launcher_runs_the_app_with_system_python_from_its_own_version(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    launcher = _version_dir(sandbox) / "bin" / "meeting-scribe-review"
    assert launcher.stat().st_mode & 0o111
    sandbox.log.write_text("")
    done = sandbox.run(launcher, "--meeting", "m1")
    assert done.returncode == 0, done.stderr
    (call,) = [line for line in sandbox.calls() if line.startswith("python3-run")]
    assert f"PYTHONPATH={_version_dir(sandbox)}/app" in call
    assert "PYTHONDONTWRITEBYTECODE=1" in call
    assert "args=-m meeting_scribe_review --meeting m1" in call


def test_the_launcher_interpreter_is_the_system_one_by_default() -> None:
    text = (SCRIPTS / "_common.sh").read_text()
    assert 'REVIEW_APP_PYTHON="${REVIEW_APP_PYTHON:-/usr/bin/python3}"' in text


def _exec_argv(entry: Path, *, action: str = "") -> list[str]:
    """Parse an Exec line the way the Desktop Entry specification says a launcher must.

    The entry's own line by default, or the one inside ``[Desktop Action <action>]``.
    """
    text = entry.read_text()
    if action:
        text = text.split(f"[Desktop Action {action}]", 1)[1]
    line = next(x for x in text.splitlines() if x.startswith("Exec="))
    value = line.removeprefix("Exec=").replace("\\\\", "\x00")  # string level: \\ -> \
    value = value.replace("\x00", "\\")
    argv: list[str] = []
    current, quoted, started, i = "", False, False, 0
    while i < len(value):
        ch = value[i]
        if quoted and ch == "\\":
            current += value[i + 1]
            i += 2
            continue
        if ch == '"':
            quoted, started = not quoted, True
        elif ch == " " and not quoted:
            if started:
                argv.append(current)
            current, started = "", False
        else:
            current, started = current + ch, True
        i += 1
    if started:
        argv.append(current)
    return argv


def test_install_renders_the_desktop_entry_with_the_stable_current_path(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    lines = sandbox.desktop_entry.read_text().splitlines()
    launcher = sandbox.root / "current" / "bin" / "meeting-scribe-review"
    assert _exec_argv(sandbox.desktop_entry) == [str(launcher)]
    for expected in (
        "Type=Application",
        "Icon=audio-input-microphone",
        "Categories=GNOME;GTK;Utility;",
        "StartupNotify=true",
        "StartupWMClass=dev.meetingscribe.Review",
        "DBusActivatable=false",
    ):
        assert expected in lines
    assert not any("@" in line for line in lines)  # every placeholder was rendered


def test_install_renders_the_preferences_action_with_the_same_launcher(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    lines = sandbox.desktop_entry.read_text().splitlines()
    launcher = sandbox.root / "current" / "bin" / "meeting-scribe-review"
    assert "Actions=preferences;" in lines
    assert "[Desktop Action preferences]" in lines
    assert _exec_argv(sandbox.desktop_entry, action="preferences") == [
        str(launcher),
        "--preferences",
    ]


def test_the_exec_line_is_spec_valid_for_spaces_and_reserved_characters(
    sandbox: Env, tmp_path: Path
) -> None:
    data = tmp_path / 'a b&c|d$e`f"g\\h%i' / "share"
    result = sandbox.run(INSTALL, XDG_DATA_HOME=str(data), **IDLE)
    assert result.returncode == 0, result.stderr
    entry = data / "applications" / DESKTOP_ENTRY
    expected = f"{data}/meeting-scribe/current/bin/meeting-scribe-review"
    assert _exec_argv(entry) == [expected.replace("%", "%%")]
    line = next(x for x in entry.read_text().splitlines() if x.startswith("Exec="))
    assert line.startswith('Exec="') and line.endswith('"')  # one quoted argument
    assert _exec_argv(entry, action="preferences") == [expected.replace("%", "%%"), "--preferences"]


def test_a_rollback_to_a_version_without_the_launcher_removes_the_entry(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    (first / "bin" / "meeting-scribe-review").unlink()  # stands in for a pre-F4 install
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert "rolled back" in result.stdout
    assert not sandbox.desktop_entry.exists()
    assert "review app: " in result.stdout  # the summary still reports the new, kept version


def test_a_rollback_to_a_version_with_the_launcher_keeps_the_entry(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert sandbox.desktop_entry.exists()


def test_the_entry_is_written_only_after_the_switch_succeeded(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UV_FAIL="1", **IDLE)
    assert result.returncode != 0
    assert not sandbox.desktop_entry.exists()


def test_a_failed_first_start_leaves_an_entry_for_the_kept_version(sandbox: Env) -> None:
    # No previous version to roll back to: the new one stays in charge, so its entry stays.
    sandbox.run(INSTALL, FAKE_SYSTEMCTL_FAIL="enable", **IDLE)
    assert sandbox.desktop_entry.exists()


def test_install_refreshes_the_desktop_database_when_the_tool_exists(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    applications = sandbox.desktop_entry.parent
    assert f"update-desktop-database {applications}" in sandbox.calls()


def test_install_works_without_update_desktop_database(sandbox: Env) -> None:
    (Path(sandbox.env["PATH"]) / "update-desktop-database").unlink()
    result = sandbox.run(INSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    assert sandbox.desktop_entry.exists()


def test_the_summary_says_the_review_app_is_ready(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, **IDLE)
    summary = result.stdout.split("meeting-scribe installed")[1]
    assert "review app: ready" in summary


def _remove_tool(sandbox: Env, name: str) -> None:
    (Path(sandbox.env["PATH"]) / name).unlink()


@pytest.mark.parametrize(
    ("setup", "code", "tag"),
    [
        ({"remove": "ffmpeg"}, 10, "[ FIX ] tool.ffmpeg"),
        ({"env": {"FAKE_GI_MISSING": "Adw"}}, 10, "[ FIX ] gtk.libadwaita"),
        ({"env": {"XDG_SESSION_TYPE": "x11"}}, 11, "[HUMAN] session.wayland"),
        ({"remove": "uv"}, 10, "[ FIX ] tool.uv"),
        ({"env": {"FAKE_GDBUS_MODE": "recording"}, "remove": "pactl"}, 10, "[ FIX ] tool.pactl"),
        ({"env": {"FORCE": "1"}, "remove": "curl"}, 10, "[ FIX ] tool.curl"),
        ({"remove": "nvidia-smi"}, 11, "[HUMAN] gpu.driver"),
        ({"env": {"FAKE_NVIDIA_ROWS": "Small GPU, 6144 MiB, 1.0"}}, 11, "[HUMAN] gpu.vram"),
        ({"env": {"FAKE_DF": "/|/dev/small|1048576|/"}}, 10, "[ FIX ] disk.space"),
        ({"remove": "ollama"}, 10, "[ FIX ] ollama.installed"),
        ({"env": {"FAKE_OLLAMA_MODE": "down"}}, 10, "[ FIX ] ollama.reachable"),
        ({"env": {"FAKE_OLLAMA_MODE": "missing-model"}}, 10, "[ FIX ] ollama.model"),
    ],
)
def test_install_stops_with_zero_changes_when_a_prerequisite_is_missing(
    sandbox: Env, setup: dict, code: int, tag: str
) -> None:
    if "remove" in setup:
        _remove_tool(sandbox, setup["remove"])
    result = sandbox.run(INSTALL, **setup.get("env", {}))
    assert result.returncode == code, result.stderr
    assert tag in result.stdout  # the preflight report is printed
    assert "fix:" in result.stdout or "steps:" in result.stdout
    assert sandbox.tree() == []  # the sandbox HOME is unchanged
    # the read-only desktop detection asks the bus daemon, never the service
    calls = [line for line in sandbox.calls() if not is_desktop_query(line)]
    for prefix in ("uv", "gnome-extensions", "gdbus", "sudo", "meeting-scribe", "ollama"):
        assert not any(line.startswith(prefix) for line in calls), (prefix, calls)
    # the only systemctl call a preflight makes reads whether Ollama is a system service
    assert all("cat ollama.service" in x for x in calls if x.startswith("systemctl")), calls
    assert "meeting-scribe installed" not in result.stdout


def test_a_package_gap_no_longer_installs_with_a_warning(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_GI_MISSING="gi", **IDLE)
    assert result.returncode == 10
    assert "sudo dnf install python3-gobject" in result.stdout
    assert not sandbox.desktop_entry.exists()


def test_the_review_app_never_runs_a_window_during_install(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    runs = [line for line in sandbox.calls() if line.startswith("python3-run")]
    assert all("args=-c " in line for line in runs)  # only the import probes, never the app
    assert not any("meeting_scribe_review" in line for line in runs)


def test_uninstall_removes_the_desktop_entry_and_the_app_but_not_other_entries(
    sandbox: Env,
) -> None:
    _installed(sandbox)
    other = sandbox.desktop_entry.parent / "other.desktop"
    other.write_text("[Desktop Entry]\n")
    result = sandbox.run(UNINSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    assert not sandbox.desktop_entry.exists()
    assert other.exists()
    assert not sandbox.root.exists()  # the launcher and the app went with versions/
    assert f"update-desktop-database {other.parent}" in sandbox.calls()


def test_uninstall_without_an_entry_is_still_clean(sandbox: Env) -> None:
    result = sandbox.run(UNINSTALL, **IDLE)
    assert result.returncode == 0, result.stderr


def test_a_rolled_back_install_keeps_the_previous_launcher_reachable(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert (sandbox.root / "current").resolve() == first.resolve()
    assert (sandbox.root / "current" / "bin" / "meeting-scribe-review").is_file()


def test_make_run_review_app_uses_system_python_and_the_working_tree() -> None:
    dry = subprocess.run(
        ["make", "-n", "run-review-app"],  # noqa: S607  # dry run: prints, never runs
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry.returncode == 0, dry.stderr
    assert "PYTHONPATH=review-app/src" in dry.stdout
    assert "/usr/bin/python3 -m meeting_scribe_review" in dry.stdout
    override = subprocess.run(
        ["make", "-n", "run-review-app", "REVIEW_APP_PYTHON=/opt/py/python3"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "/opt/py/python3 -m meeting_scribe_review" in override.stdout


def test_the_desktop_template_has_one_exec_placeholder_per_exec_line() -> None:
    template = (ROOT / "packaging" / "applications" / f"{DESKTOP_ENTRY}.in").read_text()
    execs = [line for line in template.splitlines() if line.startswith("Exec=")]
    assert execs == ["Exec=@EXEC@", "Exec=@EXEC@ --preferences"]  # the entry and its action
    assert template.count("@EXEC@") == len(execs)  # none in a comment: the render replaces all


# -- Ollama and the local summary model (F5.8, F6.3): checked by the preflight, never run ----------


def _ollama_calls(sandbox: Env) -> list[str]:
    return [line for line in sandbox.calls() if line.startswith("ollama")]


def test_install_reports_the_reachable_ollama_with_the_configured_model(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    assert "  ollama:    reachable at preflight; model qwen3:4b-instruct-2507-q4_K_M present" in (
        _summary(result)
    )
    assert _ollama_calls(sandbox) == []  # never run: no list, pull, serve or create


def test_the_summary_names_the_model_from_the_configuration(sandbox: Env) -> None:
    result = sandbox.run(
        INSTALL, SUMMARIES_LOCAL_MODEL="mistral:7b", FAKE_OLLAMA_MODEL="mistral:7b", **IDLE
    )
    assert "model mistral:7b present" in _summary(result)


def test_uninstall_never_touches_ollama(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    sandbox.log.write_text("")
    sandbox.run(UNINSTALL, **IDLE)
    assert _ollama_calls(sandbox) == []
    assert not any(line.startswith("curl") for line in sandbox.calls())


# --- F6.8: update and uninstall (AC-21, AC-22) ---

USER_FILES = {
    "Documents/meetings/2026-01-01-demo/meeting.json": "synthetic meeting\n",
    ".config/meeting-scribe/settings.json": '{"logging.level": "DEBUG"}\n',
    ".config/meeting-scribe/.env": "LOG_LEVEL=DEBUG\n",
    ".local/share/keyrings/login.keyring": "fake keyring marker\n",
    ".cache/huggingface/hub/models--fake--model/snapshots/abc/config.json": "{}\n",
}
ACTIVE = {"FAKE_UNIT_ACTIVE": "1", "FAKE_GDBUS_MODE": "idle"}


def _seed_user_files(sandbox: Env) -> dict[Path, str]:
    seeded = {sandbox.home / name: text for name, text in USER_FILES.items()}
    for path, text in seeded.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return seeded


def _user_tree(sandbox: Env) -> dict[str, str]:
    """Every file outside the install root, the unit, the activation file and the desktop entry."""
    owned = {sandbox.unit_path, sandbox.activation_path, sandbox.desktop_entry}
    return {
        str(path.relative_to(sandbox.home)): path.read_text()
        for path in sorted(sandbox.home.rglob("*"))
        if path.is_file()
        and path not in owned
        and sandbox.root not in path.parents
        and path.suffix != ".desktop"
    }


def _extension_calls(sandbox: Env) -> list[str]:
    """The extension calls without the temporary pack directory, which differs per run."""
    return [
        re.sub(r"/\S*\.zip", "<zip>", re.sub(r"-o \S+", "-o <dir>", line))
        for line in sandbox.calls()
        if line.startswith("gnome-extensions")
    ]


def test_update_keeps_user_data_and_is_idempotent(sandbox: Env) -> None:
    seeded = _seed_user_files(sandbox)
    snapshots = []
    extension_calls = []
    for _ in range(3):
        sandbox.log.write_text("")
        result = sandbox.run(INSTALL, **ACTIVE)
        assert result.returncode == 0, result.stdout + result.stderr
        snapshots.append(
            (
                sandbox.unit_path.read_bytes(),
                sandbox.activation_path.read_bytes(),
                sandbox.desktop_entry.read_bytes(),
                _user_tree(sandbox),
                [v.name for v in sandbox.versions],
            )
        )
        extension_calls.append(_extension_calls(sandbox))
    second, third = snapshots[1], snapshots[2]
    assert second[:4] == third[:4] == snapshots[0][:4]  # unit, activation, entry, user files
    assert _user_tree(sandbox) == {str(p.relative_to(sandbox.home)): t for p, t in seeded.items()}
    assert extension_calls[0] == extension_calls[1] == extension_calls[2] != []
    for _, _, _, _, versions in snapshots[1:]:
        assert len(versions) == 1  # the previous version is pruned once the new one runs
    assert (sandbox.root / "current").resolve() == sandbox.versions[0].resolve()
    assert f"systemctl --user restart {UNIT}" in sandbox.calls()


def test_uninstall_lists_what_it_keeps(sandbox: Env) -> None:
    seeded = _seed_user_files(sandbox)
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, **ACTIVE)
    assert result.returncode == 0, result.stderr
    assert _user_tree(sandbox) == {str(p.relative_to(sandbox.home)): t for p, t in seeded.items()}
    kept = result.stdout.split("Kept (not removed):", 1)[1]
    for fragment in (
        "meetings",
        "settings.json",
        ".env",
        "cloud API key",
        "Hugging Face token",
        "Seahorse",
        "~/.cache/huggingface",
        "shared with other tools",
    ):
        assert fragment in kept, fragment


def test_the_runbook_keep_list_is_the_text_uninstall_prints(sandbox: Env) -> None:
    printed = sandbox.run(UNINSTALL).stdout.split("Kept (not removed):", 1)[1]
    printed = printed.replace(str(sandbox.home / ".config"), "~/.config")
    runbook = (ROOT / "docs" / "runbooks" / "install.md").read_text()
    documented = runbook.split("Kept (not removed):", 1)[1].split("\n\n## ", 1)[0]

    def bullets(text: str) -> list[str]:
        return [line.strip("- ").replace("`", "") for line in text.splitlines() if "- " in line]

    assert bullets(printed) == bullets(documented) != []


# --- F6.5: the tray outside GNOME Shell (docs/specs/desktop-tray.md section 7, AC-10, AC-11) ---

# A Plasma session: org.gnome.Shell owns no name on the bus, so the tray path applies. The fake
# gnome-extensions stays on PATH, so any call to it would be logged.
TRAY = {"FAKE_GNOME_SHELL_OWNER": "0", "XDG_CURRENT_DESKTOP": "KDE"}
TRAY_LAUNCHER = "meeting-scribe-tray"
SHARED_LIB = Path("gnome-extension") / EXTENSION / "lib"
# The read-only question for the running tray's PID; the timeout is any number of seconds, so a
# different GDBUS_TIMEOUT does not change what counts as this question.
PID_QUERY = re.compile(
    r"gdbus call --session --timeout [1-9][0-9]* --dest org\.freedesktop\.DBus "
    r"--object-path /org/freedesktop/DBus --method "
    rf"org\.freedesktop\.DBus\.GetConnectionUnixProcessID {re.escape(TRAY_BUS_NAME)}"
)


def _pid_queries(calls: list[str]) -> list[int]:
    """The positions of the logged PID questions about the tray."""
    return [i for i, line in enumerate(calls) if PID_QUERY.fullmatch(line)]


def _files(folder: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def _tray_starts(sandbox: Env) -> list[str]:
    return [line for line in sandbox.calls() if line.startswith("gjs ")]


def _tray_bus_calls(sandbox: Env) -> list[str]:
    return [line for line in sandbox.calls() if TRAY_BUS_NAME in line]


def test_tray_path_install_copies_the_tray_launcher_entry_and_icons(sandbox: Env) -> None:  # AC-10
    result = sandbox.run(INSTALL, **TRAY, **ACTIVE)
    assert result.returncode == 0, result.stdout + result.stderr
    version = _version_dir(sandbox)
    current = sandbox.root / "current"
    # desktop-tray/ without its tests, and the shared lib/ at the path the tray imports it from
    tray = {k: v for k, v in _files(ROOT / "desktop-tray").items() if not k.startswith("tests/")}
    assert _files(version / "desktop-tray") == tray
    assert not (version / "desktop-tray" / "tests").exists()
    assert _files(version / SHARED_LIB) == _files(ROOT / SHARED_LIB)
    assert [p.name for p in (version / "gnome-extension").iterdir()] == [EXTENSION]
    assert [p.name for p in (version / "gnome-extension" / EXTENSION).iterdir()] == ["lib"]
    # the launcher runs the copied entry point through the stable current path
    main = version / "desktop-tray" / "main.js"
    assert main.read_bytes() == (ROOT / "desktop-tray" / "main.js").read_bytes()
    launcher = version / "bin" / TRAY_LAUNCHER
    assert launcher.stat().st_mode & 0o111
    assert f"exec gjs -m {current}/desktop-tray/main.js" in launcher.read_text()
    assert (current / "desktop-tray" / "main.js").resolve() == main.resolve()
    # the autostart entry starts that launcher at every login
    entry = sandbox.autostart_entry.read_text().splitlines()
    assert _exec_argv(sandbox.autostart_entry) == [str(current / "bin" / TRAY_LAUNCHER)]
    for expected in ("Type=Application", "Name=Meeting Scribe tray", "NoDisplay=true"):
        assert expected in entry
    assert not any(line.startswith("NotShowIn=") for line in entry)
    assert not any("@" in line for line in entry)  # every placeholder was rendered
    # the five status icons
    icons = {p.name: p.read_bytes() for p in sorted(TRAY_ICONS.glob("*.svg"))}
    assert len(icons) == 5
    assert _files(sandbox.tray_icons) == icons
    # no GNOME Shell step, and the tray now runs in this session
    assert not any(line.startswith("gnome-extensions") for line in sandbox.calls())
    assert _tray_starts(sandbox) == [f"gjs -m {current}/desktop-tray/main.js"]
    assert sandbox.is_sandbox_tray(sandbox.tray_pid())
    summary = _summary(result)
    assert "  tray: running" in summary.splitlines()
    assert "extension:" not in summary


def test_the_tray_launcher_passes_its_arguments_to_gjs(sandbox: Env) -> None:
    sandbox.run(INSTALL, **TRAY, **IDLE)
    sandbox.log.write_text("")
    launcher = sandbox.root / "current" / "bin" / TRAY_LAUNCHER
    done = sandbox.run(launcher, "--flag", "a b", FAKE_TRAY_LIFETIME="0")
    assert done.returncode == 0, done.stderr
    main = sandbox.root / "current" / "desktop-tray" / "main.js"
    assert _tray_starts(sandbox) == [f"gjs -m {main} --flag a b"]


def test_tray_path_update_is_idempotent_and_replaces_the_tray_by_pid(sandbox: Env) -> None:  # AC-10
    seeded = _seed_user_files(sandbox)
    first = sandbox.run(INSTALL, **TRAY, **ACTIVE)
    assert first.returncode == 0, first.stdout + first.stderr
    old_pid = sandbox.tray_pid()
    entry, icons = sandbox.autostart_entry.read_bytes(), _files(sandbox.tray_icons)
    sandbox.log.write_text("")
    second = sandbox.run(INSTALL, **TRAY, **ACTIVE)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "  tray: running" in _summary(second).splitlines()
    calls = sandbox.calls()
    # the running tray is found by its bus name and stopped by its PID before the new one starts
    (pid_query,) = _pid_queries(calls)
    assert [line for line in calls if "GetConnectionUnixProcessID" in line] == [calls[pid_query]]
    assert pid_query < _index(calls, "gjs ")
    assert not sandbox.is_sandbox_tray(old_pid)
    new_pid = sandbox.tray_pid()
    assert new_pid != old_pid
    assert sandbox.is_sandbox_tray(new_pid)
    assert len(_tray_starts(sandbox)) == 1
    # the same files, one version, no GNOME Shell step, the user's data untouched
    assert sandbox.autostart_entry.read_bytes() == entry
    assert _files(sandbox.tray_icons) == icons
    (version,) = sandbox.versions
    assert (version / "desktop-tray").is_dir()
    assert not any(line.startswith("gnome-extensions") for line in calls)
    user_files = {k: v for k, v in _user_tree(sandbox).items() if "/icons/" not in k}
    assert user_files == {str(p.relative_to(sandbox.home)): t for p, t in seeded.items()}


def test_a_tray_that_does_not_start_is_reported_and_fails_the_install(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_TRAY_START_FAIL="1", **TRAY, **IDLE)
    assert result.returncode == 1
    summary = _summary(result).splitlines()
    (tray,) = [line for line in summary if line.startswith("  tray:")]
    assert tray.startswith("  tray: FAILED (")
    assert TRAY_BUS_NAME in tray
    assert any(line.startswith("  service:") for line in summary)  # the summary is complete


def test_a_tray_path_rollback_runs_the_tray_of_the_restored_version(sandbox: Env) -> None:
    sandbox.run(INSTALL, **TRAY, **IDLE)
    (first,) = sandbox.versions
    result = sandbox.run(INSTALL, FAKE_SYSTEMCTL_FAIL="restart", **TRAY, **ACTIVE)
    assert result.returncode == 1
    assert f"rolled back to {first.name}" in result.stdout
    assert (sandbox.root / "current").resolve() == first.resolve()
    launcher = sandbox.root / "current" / "bin" / TRAY_LAUNCHER
    assert _exec_argv(sandbox.autostart_entry) == [str(launcher)]
    assert "  tray: running" in _summary(result).splitlines()
    assert sandbox.is_sandbox_tray(sandbox.tray_pid())


def test_a_rollback_to_a_version_without_the_tray_removes_its_autostart_entry(
    sandbox: Env,
) -> None:
    sandbox.run(INSTALL, **IDLE)  # installed on GNOME Shell: that version has no tray
    sandbox.autostart_entry.parent.mkdir(parents=True)
    sandbox.autostart_entry.write_text("[Desktop Entry]\n")  # left from an earlier tray install
    result = sandbox.run(INSTALL, FAKE_SYSTEMCTL_FAIL="restart", **TRAY, **ACTIVE)
    assert result.returncode == 1
    assert "rolled back" in result.stdout
    assert not sandbox.autostart_entry.exists()
    assert "  tray: FAILED (the version in charge has no tray launcher)" in _summary(result)
    assert _tray_starts(sandbox) == []


def test_tray_path_uninstall_stops_the_tray_and_removes_its_files(sandbox: Env) -> None:  # AC-10
    seeded = _seed_user_files(sandbox)
    sandbox.run(INSTALL, **TRAY, **ACTIVE)
    pid = sandbox.tray_pid()
    assert sandbox.is_sandbox_tray(pid)
    sandbox.log.write_text("")
    result = sandbox.run(UNINSTALL, **TRAY, **ACTIVE)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _pid_queries(sandbox.calls())
    assert not sandbox.is_sandbox_tray(pid)  # stopped by its PID
    assert not sandbox.autostart_entry.exists()
    assert not list(sandbox.tray_icons.glob("*.svg"))
    assert not sandbox.root.exists()
    assert not any(line.startswith("gnome-extensions") for line in sandbox.calls())
    assert _user_tree(sandbox) == {str(p.relative_to(sandbox.home)): t for p, t in seeded.items()}
    assert "Kept (not removed):" in result.stdout


def test_gnome_path_install_installs_no_tray(sandbox: Env) -> None:  # AC-11
    result = sandbox.run(INSTALL, **ACTIVE)
    assert result.returncode == 0, result.stdout + result.stderr
    version = _version_dir(sandbox)
    assert not (version / "desktop-tray").exists()
    assert not (version / "gnome-extension").exists()
    assert not (version / "bin" / TRAY_LAUNCHER).exists()
    assert not sandbox.autostart_entry.exists()
    assert not (sandbox.home / ".local" / "share" / "icons").exists()
    assert _tray_starts(sandbox) == []
    assert _tray_bus_calls(sandbox) == []
    summary = _summary(result)
    assert f"  extension: enabled ({EXTENSION})" in summary.splitlines()
    assert "tray:" not in summary


def test_gnome_path_uninstall_removes_a_leftover_tray_entry_and_icons(sandbox: Env) -> None:
    _installed(sandbox)
    sandbox.autostart_entry.parent.mkdir(parents=True)
    sandbox.autostart_entry.write_text("[Desktop Entry]\n")
    other_entry = sandbox.autostart_entry.parent / "other.desktop"
    other_entry.write_text("[Desktop Entry]\n")
    sandbox.tray_icons.mkdir(parents=True)
    for icon in TRAY_ICONS.glob("*.svg"):
        (sandbox.tray_icons / icon.name).write_text("<svg/>")
    (sandbox.tray_icons / "other-app.svg").write_text("<svg/>")
    result = sandbox.run(UNINSTALL, **ACTIVE)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not sandbox.autostart_entry.exists()
    assert [p.name for p in sandbox.tray_icons.iterdir()] == ["other-app.svg"]
    assert other_entry.exists()
    assert f"gnome-extensions uninstall {EXTENSION}" in sandbox.calls()
    # on GNOME Shell nothing else is stopped: not even a question about the tray's bus name
    assert _tray_bus_calls(sandbox) == []


def test_the_installed_autostart_entry_carries_no_placeholder_and_no_template_note(
    sandbox: Env,
) -> None:
    result = sandbox.run(INSTALL, **TRAY, **ACTIVE)
    assert result.returncode == 0, result.stdout + result.stderr
    text = sandbox.autostart_entry.read_text()
    assert "@EXEC@" not in text
    assert "Template" not in text
    assert [line for line in text.splitlines() if line.startswith("#")] == []
    assert text.startswith("[Desktop Entry]\n")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "5"), ("", "5"), ("2", "2"), ("abc", "5"), ("0", "5"), ("-1", "5"), ("1.5", "5")],
)
def test_the_tray_wait_comes_from_the_environment_with_a_5_second_default(
    sandbox: Env, value: str | None, expected: str
) -> None:
    extra = {} if value is None else {"MEETING_SCRIBE_TRAY_WAIT_SECONDS": value}
    result = sandbox.snippet('printf %s "$TRAY_WAIT_SECONDS"', **extra)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


def test_the_tray_wait_is_bounded_by_elapsed_time_even_on_a_slow_bus(sandbox: Env) -> None:
    # Each question about the tray name takes 1 s: the wait still ends after about
    # TRAY_WAIT_SECONDS of elapsed time, not after a fixed number of questions (5 x 1.2 s).
    slow = "bus_name_owner() { sleep 1; echo yes; }; TRAY_WAIT_SECONDS=1; tray_wait_owner no"
    started = time.monotonic()
    result = sandbox.snippet(slow)
    elapsed = time.monotonic() - started
    assert result.returncode == 1
    assert elapsed < 4
    # A fast bus that never gives the awaited answer: the wait lasts at least TRAY_WAIT_SECONDS.
    fast = "bus_name_owner() { echo yes; }; TRAY_WAIT_SECONDS=1; tray_wait_owner no"
    started = time.monotonic()
    result = sandbox.snippet(fast)
    elapsed = time.monotonic() - started
    assert result.returncode == 1
    assert 1 <= elapsed < 3


# Every way the stop of the running tray can fail (docs/specs/install.md, decision log 2026-10-17):
# the environment of the install that started the old tray, the environment of the failing run,
# the reason (with {pid} for the old tray's PID) and the SIGTERMs the old tray must have received.
NOT_OWNED = f"the session bus did not say whether {TRAY_BUS_NAME} is running"
NO_PID = f"the PID of the running {TRAY_BUS_NAME} could not be read"
STOP_FAILURES = [
    pytest.param({}, {"FAKE_TRAY_OWNER": "fail"}, NOT_OWNED, [], id="bus-does-not-answer"),
    pytest.param({}, {"FAKE_TRAY_PID_ANSWER": "fail"}, NO_PID, [], id="pid-query-fails"),
    pytest.param({}, {"FAKE_TRAY_PID_ANSWER": "what"}, NO_PID, [], id="pid-unreadable"),
    # PID 1 is refused like an unreadable answer. Never PID 0 here: if the guard broke, `kill 0`
    # would signal the test run's own process group.
    pytest.param(
        {},
        {"FAKE_TRAY_PID_ANSWER": "(uint32 1,)"},
        NO_PID,
        [],
        id="pid-is-init",
        marks=pytest.mark.skipif(os.geteuid() == 0, reason="never risk signalling init as root"),
    ),
    pytest.param(
        {"FAKE_TRAY_IGNORE_TERM": "1"},
        {"MEETING_SCRIBE_TRAY_WAIT_SECONDS": "1"},
        f"the running {TRAY_BUS_NAME} (pid {{pid}}) did not stop within 1 s",
        ["TERM"],
        id="survives-sigterm",
    ),
]


@pytest.mark.parametrize(("before", "failing", "reason", "signals"), STOP_FAILURES)
def test_a_tray_that_cannot_be_stopped_fails_the_install_and_is_left_running(
    sandbox: Env, before: dict[str, str], failing: dict[str, str], reason: str, signals: list[str]
) -> None:
    first = sandbox.run(INSTALL, **before, **TRAY, **ACTIVE)
    assert first.returncode == 0, first.stdout + first.stderr
    old_pid = sandbox.tray_pid()
    sandbox.log.write_text("")
    result = sandbox.run(INSTALL, **failing, **TRAY, **ACTIVE)
    assert result.returncode == 1
    summary = _summary(result).splitlines()
    assert f"  tray: FAILED ({reason.format(pid=old_pid)})" in summary
    assert any(line.startswith("  service:") for line in summary)  # the summary is complete
    # no second tray while the old one may still run, and the old one got SIGTERM at most: it
    # still runs, so no SIGKILL was sent
    assert _tray_starts(sandbox) == []
    assert sandbox.is_sandbox_tray(old_pid)
    assert sandbox.tray_signals() == signals


@pytest.mark.parametrize(("before", "failing", "reason", "signals"), STOP_FAILURES)
def test_a_tray_that_cannot_be_stopped_is_a_warning_of_the_uninstall(
    sandbox: Env, before: dict[str, str], failing: dict[str, str], reason: str, signals: list[str]
) -> None:
    first = sandbox.run(INSTALL, **before, **TRAY, **ACTIVE)
    assert first.returncode == 0, first.stdout + first.stderr
    old_pid = sandbox.tray_pid()
    result = sandbox.run(UNINSTALL, **failing, **TRAY, **ACTIVE)
    assert result.returncode == 0, result.stdout + result.stderr
    warning = f"warning: {reason.format(pid=old_pid)}; it stops at the next log out."
    assert warning in result.stderr.splitlines()
    assert sandbox.is_sandbox_tray(old_pid)  # never SIGKILL
    assert sandbox.tray_signals() == signals
    # everything else is removed anyway; the autostart entry no longer starts it at a login
    assert not sandbox.autostart_entry.exists()
    assert not list(sandbox.tray_icons.glob("*.svg"))
    assert not sandbox.root.exists()
    assert "meeting-scribe uninstalled." in result.stdout
