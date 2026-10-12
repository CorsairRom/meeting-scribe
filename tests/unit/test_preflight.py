"""Behavior of the install preflight (`./install.sh --check`): report, exit codes and checks.

Every test runs against a sandbox HOME with fake tools on a PATH that holds nothing else (see
``install_sandbox``): never the real GPU, Ollama, systemctl, gnome-extensions or the network.
"""

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from tests.unit.install_sandbox import (
    INSTALL,
    ROOT,
    ROOT_INSTALL,
    ROOT_UNINSTALL,
    SCRIPTS,
    UNINSTALL,
    Env,
    sandbox_fixture,  # noqa: F401  # the `sandbox` fixture
)

IDLE = {"FAKE_GDBUS_MODE": "idle"}

# The documented check ids (docs/specs/install.md section 4). A document may only hold these.
CHECK_IDS = {
    "session.wayland",
    "session.gnome-shell",
    "session.bus",
    "tool.uv",
    "tool.curl",
    "tool.ffmpeg",
    "tool.pactl",
    "tool.systemctl",
    "tool.gdbus",
    "tool.gnome-extensions",
    "gtk.pygobject",
    "gtk.gtk4",
    "gtk.libadwaita",
    "gtk.libsecret",
    "gtk.gstreamer",
    "tool.secret-tool",
    "gpu.driver",
    "gpu.vram",
    "disk.space",
    "ollama.installed",
    "ollama.reachable",
    "ollama.model",
}
STATUSES = {"OK", "WARN", "FIX", "HUMAN", "SKIP"}
# Calls that change the machine. A preflight may make none of them.
WRITE_CALLS = (
    "systemctl --user enable",
    "systemctl --user start",
    "systemctl --user restart",
    "systemctl --user stop",
    "systemctl --user disable",
    "systemctl --user daemon-reload",
    "uv tool",
    "gnome-extensions pack",
    "gnome-extensions install",
    "gnome-extensions enable",
    "gnome-extensions disable",
    "gdbus",  # --check never calls the service
    "sudo",
)


def _json(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)  # strict: a control character or a stray byte fails here


def _add(status: str, ident: str, summary: str, why: str, fix: str = "", steps: str = "") -> str:
    """Bash that adds one synthetic item to the report, every value quoted for bash."""
    out = f"pf_add {status} {ident} {shlex.quote(summary)} {shlex.quote(why)}; "
    if fix:
        out += f"pf_fix {shlex.quote(fix)}; "
    if steps:
        out += f"pf_step {shlex.quote(steps)}; "
    return out


# -- one entry point (AC-13) -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wrapper", "target"), [(ROOT_INSTALL, INSTALL), (ROOT_UNINSTALL, UNINSTALL)]
)
def test_root_wrappers_and_make_aliases(sandbox: Env, wrapper: Path, target: Path) -> None:
    assert wrapper.is_file()
    assert wrapper.stat().st_mode & 0o111  # executable
    text = wrapper.read_text()
    assert target.relative_to(ROOT).as_posix() in text
    assert len(text.splitlines()) <= 10  # a thin wrapper: no logic of its own
    assert "exec " in text
    direct = sandbox.run(target, "--help")
    wrapped = sandbox.run(wrapper, "--help")
    assert wrapped.returncode == direct.returncode == 0
    assert wrapped.stdout == direct.stdout
    bad_direct = sandbox.run(target, "--nope")
    bad_wrapped = sandbox.run(wrapper, "--nope")
    assert bad_wrapped.returncode == bad_direct.returncode == 2  # the exit code is forwarded


def test_the_root_wrapper_works_from_any_directory(sandbox: Env, tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603  # fixed argv, sandboxed PATH
        [sandbox.env["BASH"], str(ROOT_INSTALL), "--help"],
        env=sandbox.env,
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--check" in result.stdout


@pytest.mark.parametrize(
    ("target", "args", "expected"),
    [
        ("install", "", "./install.sh"),
        ("install", "--check", "./install.sh --check"),
        ("install", "--check --json", "./install.sh --check --json"),
        ("uninstall", "", "./uninstall.sh"),
        ("uninstall", "--help", "./uninstall.sh --help"),
    ],
)
def test_make_aliases_run_the_root_scripts_and_pass_args(
    target: str, args: str, expected: str
) -> None:
    command = ["make", "-n", "--no-print-directory", target, *([f"ARGS={args}"] if args else [])]
    dry = subprocess.run(  # noqa: S603  # dry run: prints, never runs
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert dry.returncode == 0, dry.stderr
    assert dry.stdout.strip() == expected  # `make install` without ARGS adds nothing


# -- options and the exit-code contract (AC-5) -------------------------------------------------


def test_help_lists_the_options_and_the_exit_codes(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, "--help")
    assert result.returncode == 0
    for needle in ("--check", "--json", "--help", "FORCE=1"):
        assert needle in result.stdout
    for code in ("0", "1", "2", "10", "11"):
        assert any(line.strip().startswith(code + " ") for line in result.stdout.splitlines()), code
    assert sandbox.tree() == []
    assert sandbox.calls() == []


@pytest.mark.parametrize("args", [["--nope"], ["--check", "--bogus"], ["install"], ["--json"]])
def test_a_usage_error_exits_2_with_usage_on_stderr_and_changes_nothing(
    sandbox: Env, args: list[str]
) -> None:
    result = sandbox.run(INSTALL, *args)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "Usage:" in result.stderr
    assert sandbox.tree() == []
    assert sandbox.calls() == []


def test_the_uninstall_script_rejects_unknown_options_and_answers_help(sandbox: Env) -> None:
    bad = sandbox.run(UNINSTALL, "--nope")
    assert bad.returncode == 2
    assert "Usage:" in bad.stderr
    helped = sandbox.run(UNINSTALL, "--help")
    assert helped.returncode == 0
    assert "keeps" in helped.stdout.lower()
    assert sandbox.calls() == []


@pytest.mark.parametrize(
    ("items", "code"),
    [
        ("", 0),
        (_add("OK", "tool.uv", "uv found", "installs the service"), 0),
        (_add("WARN", "tool.secret-tool", "missing", "optional", "sudo dnf install libsecret"), 0),
        (_add("SKIP", "ollama.reachable", "skipped", "depends on ollama.installed"), 0),
        (_add("HUMAN", "gpu.driver", "no driver", "needs a GPU", steps="install the driver"), 11),
        (_add("FIX", "tool.uv", "missing", "installs the service", "sudo dnf install uv"), 10),
        (
            _add("HUMAN", "gpu.driver", "no driver", "needs a GPU", steps="reboot")
            + _add("FIX", "tool.uv", "missing", "installs the service", "sudo dnf install uv"),
            10,
        ),
    ],
)
def test_exit_codes_match_the_contract(sandbox: Env, items: str, code: int) -> None:
    for fmt in ("text", "json"):
        first = sandbox.snippet(f"{items} pf_report {fmt}; exit $(pf_exit_code)")
        again = sandbox.snippet(f"{items} pf_report {fmt}; exit $(pf_exit_code)")
        assert first.returncode == again.returncode == code, (
            first.stderr
        )  # the same input, the same code
    if code:
        assert _json(sandbox.snippet(f"{items} pf_report json"))["exit_code"] == code


def test_a_recording_in_progress_exits_1_after_a_clean_preflight(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_GDBUS_MODE="recording")
    assert result.returncode == 1
    assert "recording is in progress" in result.stderr


def test_check_never_calls_the_service_even_while_a_recording_runs(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, "--check", FAKE_GDBUS_MODE="recording")
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("gdbus") for line in sandbox.calls())


# -- the report (AC-3, AC-6) -------------------------------------------------------------------


def test_json_report_is_one_valid_document(sandbox: Env) -> None:
    nasty = 'a "quote", a \\ backslash, a\ttab, a\nnewline, a \x01 control and an accent: é'
    one_line = nasty.replace("\n", " ")  # a fix or a step is one line by contract
    items = (
        _add("OK", "tool.uv", "uv " + nasty, "why " + nasty)
        + _add("FIX", "tool.curl", "missing", nasty, "sudo dnf install curl", "")
        + _add("HUMAN", "gpu.driver", "no driver", "needs one", steps="step " + one_line)
    )
    result = sandbox.snippet(f"{items} pf_report json")
    document = _json(result)
    assert document["schema_version"] == 1
    assert document["result"] == "blocked"
    assert document["exit_code"] == 10
    assert isinstance(document["os"], str)
    by_id = {c["id"]: c for c in document["checks"]}
    assert by_id["tool.uv"]["summary"] == "uv " + nasty
    assert by_id["tool.curl"]["why"] == nasty
    assert by_id["tool.curl"]["fix"] == ["sudo dnf install curl"]
    assert by_id["tool.curl"]["human"] is False
    assert by_id["gpu.driver"]["human"] is True
    assert by_id["gpu.driver"]["fix"] == []
    assert by_id["gpu.driver"]["steps"] == ["step " + one_line]
    for check in document["checks"]:
        assert set(check) >= {"id", "status", "summary", "why", "fix", "human", "detail"}
        assert check["status"] in STATUSES


def test_json_mode_keeps_stdout_clean_and_sends_the_prose_to_stderr(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, "--check", "--json", **IDLE)
    assert result.returncode == 0, result.stderr
    document = _json(result)
    assert document["result"] == "ok"
    assert document["exit_code"] == 0
    assert {c["id"] for c in document["checks"]} <= CHECK_IDS


def test_the_text_report_has_one_tagged_line_per_item_and_explains_blockers(sandbox: Env) -> None:
    items = (
        _add("OK", "tool.uv", "uv is on PATH", "installs the service")
        + _add(
            "FIX",
            "gtk.libadwaita",
            "libadwaita is missing",
            "the app uses it",
            "sudo dnf install libadwaita",
        )
        + _add(
            "HUMAN",
            "gpu.driver",
            "nvidia-smi is not installed",
            "needs a GPU",
            steps="install the driver",
        )
    )
    result = sandbox.snippet(f"{items} pf_report text")
    lines = result.stdout.splitlines()
    assert lines[0].startswith("[ OK  ] tool.uv")
    assert "uv is on PATH" in lines[0]
    fix_at = next(i for i, line in enumerate(lines) if line.startswith("[ FIX ] gtk.libadwaita"))
    assert lines[fix_at + 1].strip() == "why: the app uses it"
    assert lines[fix_at + 2].strip() == "fix: sudo dnf install libadwaita"
    human_at = next(i for i, line in enumerate(lines) if line.startswith("[HUMAN] gpu.driver"))
    assert lines[human_at + 2].strip() == "steps: install the driver"
    assert not any(line.strip().startswith("fix:") for line in lines[human_at + 1 : human_at + 4])
    assert lines[-1] == (
        "Summary: 1 fixable, 1 human-only. Nothing was changed. "
        "Run the fix commands, then run ./install.sh --check again."
    )
    # an item starts at column 0 with a `[` tag; everything else is indented
    assert all(line.startswith(("[", " ", "Summary:")) for line in lines)


def test_the_text_summary_says_nothing_blocks_when_it_does_not(sandbox: Env) -> None:
    result = sandbox.snippet(f"{_add('OK', 'tool.uv', 'uv', 'why')} pf_report text")
    assert result.stdout.splitlines()[-1].startswith("Summary: nothing blocks the install")


# -- --check changes nothing (AC-1) and sudo/prompts never happen (AC-4) ------------------------


@pytest.mark.parametrize("extra", [[], ["--json"]])
@pytest.mark.parametrize("mode", ["idle", "recording", "owner-fail"])
@pytest.mark.parametrize("missing", [[], ["ffmpeg", "uv", "gdbus"]])
def test_check_changes_nothing(
    sandbox: Env, extra: list[str], mode: str, missing: list[str]
) -> None:
    for tool in missing:  # with every prerequisite present and with some missing
        _remove(sandbox, tool)
    before = sandbox.tree()
    result = sandbox.run(INSTALL, "--check", *extra, FAKE_GDBUS_MODE=mode)
    assert result.returncode == (10 if missing else 0)
    assert sandbox.tree() == before == []  # no file or directory under HOME
    calls = sandbox.calls()
    for write in WRITE_CALLS:
        assert not any(line.startswith(write) for line in calls), (write, calls)
    assert not (sandbox.home.parent / "calls.log.failed").exists()


@pytest.mark.parametrize("env", [{}, {"FAKE_GI_MISSING": "gi", "XDG_SESSION_TYPE": "x11"}])
@pytest.mark.parametrize("args", [[], ["--check"], ["--check", "--json"], ["--help"]])
def test_sudo_is_never_called_and_nothing_prompts(
    sandbox: Env, args: list[str], env: dict[str, str]
) -> None:
    result = sandbox.run(INSTALL, *args, **IDLE, **env)  # standard input is closed by the sandbox
    assert not any(line.startswith("sudo") for line in sandbox.calls())
    assert "sudo was called" not in result.stderr
    if "--json" not in args:
        output = (result.stdout + result.stderr).replace(str(sandbox.home.parent), "<tmp>")
        for line in output.splitlines():
            if "sudo" in line:
                assert line.strip().startswith("fix:"), line  # only inside a printed fix command
        if env and "--help" not in args:  # the failing path really printed a sudo fix
            assert "fix: sudo dnf install python3-gobject" in output


def test_a_printed_sudo_fix_is_inside_a_fix_line_and_never_executed(sandbox: Env) -> None:
    items = _add("FIX", "tool.uv", "uv is missing", "installs the service", "sudo dnf install uv")
    result = sandbox.snippet(f"{items} pf_report text")
    sudo_lines = [line for line in result.stdout.splitlines() if "sudo" in line]
    assert sudo_lines == ["         fix: sudo dnf install uv"]
    assert not any(line.startswith("sudo") for line in sandbox.calls())


def test_the_scripts_hold_no_command_that_executes_sudo() -> None:
    for script in [*SCRIPTS.glob("*.sh"), ROOT_INSTALL, ROOT_UNINSTALL]:
        for number, line in enumerate(script.read_text().splitlines(), 1):
            stripped = line.strip()
            if "sudo" not in stripped or stripped.startswith("#"):
                continue
            # only quoted text (a fix command, a message) may name sudo, never a command position
            assert not stripped.startswith("sudo "), f"{script}:{number}: {line}"
            assert "$(sudo" not in stripped and "| sudo" not in stripped, f"{script}:{number}"


# -- the system checks: session, tools, GTK stack (F6.2) ---------------------------------------

F62_IDS = {
    "session.wayland",
    "session.gnome-shell",
    "session.bus",
    "tool.uv",
    "tool.curl",
    "tool.ffmpeg",
    "tool.pactl",
    "tool.systemctl",
    "tool.gdbus",
    "tool.gnome-extensions",
    "gtk.pygobject",
    "gtk.gtk4",
    "gtk.libadwaita",
    "gtk.libsecret",
    "gtk.gstreamer",
    "tool.secret-tool",
}
TOOL_FIXES = {
    "tool.uv": ("uv", "sudo dnf install uv"),
    "tool.curl": ("curl", "sudo dnf install curl"),
    "tool.ffmpeg": ("ffmpeg", "sudo dnf install ffmpeg-free"),
    "tool.pactl": ("pactl", "sudo dnf install pulseaudio-utils"),
    "tool.systemctl": ("systemctl", "sudo dnf install systemd"),
    "tool.gdbus": ("gdbus", "sudo dnf install glib2"),
    "tool.gnome-extensions": ("gnome-extensions", "sudo dnf install gnome-shell"),
}


def _check(sandbox: Env, *args: str, **extra: str) -> tuple[int, dict[str, dict]]:
    """Run ``--check --json`` and return the exit code with the checks keyed by id."""
    result = sandbox.run(INSTALL, "--check", "--json", *args, **extra)
    document = _json(result)
    assert result.returncode == document["exit_code"]
    return result.returncode, {c["id"]: c for c in document["checks"]}


def _remove(sandbox: Env, *tools: str) -> None:
    for tool in tools:
        (Path(sandbox.env["PATH"]) / tool).unlink()


def test_with_every_prerequisite_present_all_system_checks_pass(sandbox: Env) -> None:
    code, checks = _check(sandbox)
    assert code == 0
    assert set(checks) >= F62_IDS
    assert {i for i in F62_IDS if checks[i]["status"] != "OK"} == set()
    assert json.loads(sandbox.run(INSTALL, "--check", "--json").stdout)["os"] == "fedora-44"


@pytest.mark.parametrize("ident", sorted(TOOL_FIXES))
def test_a_missing_tool_is_a_fix_item_with_the_dnf_command(sandbox: Env, ident: str) -> None:
    tool, command = TOOL_FIXES[ident]
    _remove(sandbox, tool)
    code, checks = _check(sandbox)
    assert code == 10
    item = checks[ident]
    assert item["status"] == "FIX"
    assert item["human"] is False
    assert command in item["fix"]
    assert item["summary"]
    assert item["why"]


def test_secret_tool_is_only_a_warning(sandbox: Env) -> None:
    _remove(sandbox, "secret-tool")
    code, checks = _check(sandbox)
    assert code == 0
    assert checks["tool.secret-tool"]["status"] == "WARN"
    assert checks["tool.secret-tool"]["fix"] == ["sudo dnf install libsecret"]


def test_a_missing_voice_sample_plugin_is_only_a_warning(sandbox: Env) -> None:
    code, checks = _check(sandbox, FAKE_GSTREAMER="0")
    assert code == 0
    assert checks["gtk.gstreamer"]["status"] == "WARN"
    assert checks["gtk.gstreamer"]["fix"] == ["sudo dnf install gstreamer1-plugins-good"]


def test_every_blocking_item_has_what_why_and_fix(sandbox: Env) -> None:
    _remove(
        sandbox,
        *(tool for tool, _ in TOOL_FIXES.values()),
        "secret-tool",
        "gnome-shell",
        "nvidia-smi",
        "ollama",
    )
    env = {
        "FAKE_GI_MISSING": "gi",
        "XDG_SESSION_TYPE": "x11",
        "DBUS_SESSION_BUS_ADDRESS": "",
        "FAKE_DF": "/|/dev/small|1048576|/",
    }
    code, checks = _check(sandbox, XDG_RUNTIME_DIR=str(sandbox.home.parent / "run"), **env)
    assert code == 10
    blocking = [c for c in checks.values() if c["status"] in {"FIX", "HUMAN"}]
    assert {c["id"] for c in blocking} >= {
        "session.wayland",
        "session.gnome-shell",
        "session.bus",
        "gtk.pygobject",
        "gpu.driver",
        "disk.space",
        "ollama.installed",
        *TOOL_FIXES,
    }
    for item in blocking:
        assert item["id"] in CHECK_IDS
        assert item["summary"], item  # what is missing
        assert item["why"], item  # why it is needed
        if item["status"] == "FIX":
            assert item["fix"], item
            for command in item["fix"]:  # one printed, copy-pasteable command per line
                assert command == command.strip()
                assert command
                assert "\n" not in command
                if "sudo" in command:
                    assert command.startswith(("sudo dnf install ", "sudo systemctl start ")), (
                        command
                    )
        else:
            assert item["human"] is True
            assert item["fix"] == []
            assert item["steps"], item  # the manual steps instead of a command
    text = sandbox.run(INSTALL, "--check", XDG_RUNTIME_DIR=str(sandbox.home.parent / "run"), **env)
    for item in blocking:
        tag = "[HUMAN]" if item["status"] == "HUMAN" else "[ FIX ]"
        assert any(line.startswith(f"{tag} {item['id']}") for line in text.stdout.splitlines()), (
            item["id"]
        )


def test_human_only_items_are_labelled_and_exit_11(sandbox: Env) -> None:
    run_dir = sandbox.home.parent / "run"
    code, checks = _check(
        sandbox,
        XDG_SESSION_TYPE="x11",
        DBUS_SESSION_BUS_ADDRESS="",
        XDG_RUNTIME_DIR=str(run_dir),
        FAKE_SHELL_VERSION="49.2",
    )
    assert code == 11
    for ident in ("session.wayland", "session.bus", "session.gnome-shell"):
        item = checks[ident]
        assert item["status"] == "HUMAN"
        assert item["human"] is True
        assert item["fix"] == []
        assert item["steps"]
    assert "x11" in checks["session.wayland"]["summary"]
    assert "terminal" in " ".join(checks["session.bus"]["steps"])


def test_a_missing_package_is_fix_and_exits_10_even_next_to_human_items(sandbox: Env) -> None:
    code, checks = _check(sandbox, FAKE_GI_MISSING="Adw", XDG_SESSION_TYPE="x11")
    assert code == 10
    assert checks["gtk.libadwaita"]["status"] == "FIX"
    assert checks["session.wayland"]["status"] == "HUMAN"


def test_the_session_bus_is_found_from_the_runtime_directory(sandbox: Env) -> None:
    run_dir = sandbox.home.parent / "run"
    run_dir.mkdir()
    (run_dir / "bus").write_text("")
    _, checks = _check(sandbox, DBUS_SESSION_BUS_ADDRESS="", XDG_RUNTIME_DIR=str(run_dir))
    assert checks["session.bus"]["status"] == "OK"


def test_the_shell_not_installed_is_a_human_item(sandbox: Env) -> None:
    _remove(sandbox, "gnome-shell")
    code, checks = _check(sandbox)
    assert code == 11
    assert checks["session.gnome-shell"]["status"] == "HUMAN"
    assert "not installed" in checks["session.gnome-shell"]["summary"]


def _repo_copy(sandbox: Env, shell_versions: str) -> Path:
    """A copy of the scripts and the extension metadata with another ``shell-version`` list."""
    root = sandbox.home.parent / "repo"
    (root / "scripts").mkdir(parents=True)
    for script in SCRIPTS.glob("*.sh"):
        (root / "scripts" / script.name).write_text(script.read_text())
        (root / "scripts" / script.name).chmod(0o755)
    meta_dir = root / "gnome-extension" / "meeting-scribe@meetingscribe.dev"
    meta_dir.mkdir(parents=True)
    (meta_dir / "metadata.json").write_text(
        f'{{\n  "uuid": "x@y",\n  "shell-version": {shell_versions}\n}}\n'
    )
    return root / "scripts" / "install.sh"


@pytest.mark.parametrize(
    ("declared", "shell", "status"),
    [
        ('["50"]', "50.4", "OK"),
        ('["50"]', "51.0", "HUMAN"),
        ('["49", "51"]', "50.1", "HUMAN"),
        ('[\n    "49",\n    "50"\n  ]', "50.4", "OK"),
        ('["51.beta"]', "51.beta", "OK"),
        ('["50"]', "garbage", "HUMAN"),
    ],
)
def test_session_checks_read_the_extension_metadata(
    sandbox: Env, declared: str, shell: str, status: str
) -> None:
    script = _repo_copy(sandbox, declared)
    result = sandbox.run(script, "--check", "--json", FAKE_SHELL_VERSION=shell)
    item = {c["id"]: c for c in _json(result)["checks"]}["session.gnome-shell"]
    assert item["status"] == status, item
    if status == "HUMAN":
        required = " ".join(re.findall(r"\d+", declared))  # what the metadata lists
        assert shell in item["summary"]  # what was detected
        assert required.split()[0] in " ".join([item["summary"], *item["steps"]])


def test_the_scripts_do_not_hardcode_the_shell_version() -> None:
    text = (SCRIPTS / "preflight.sh").read_text()
    assert not re.search(r"\b50\b", text.replace("/50/", ""))  # read from metadata.json instead


def test_an_unreadable_metadata_is_a_human_item(sandbox: Env) -> None:
    script = _repo_copy(sandbox, '["50"]')
    metadata = (
        script.parent.parent / "gnome-extension/meeting-scribe@meetingscribe.dev/metadata.json"
    )
    metadata.write_text("{}")
    result = sandbox.run(script, "--check", "--json")
    item = {c["id"]: c for c in _json(result)["checks"]}["session.gnome-shell"]
    assert item["status"] == "HUMAN"
    assert "shell-version" in item["summary"]


@pytest.mark.parametrize(
    ("missing", "blocked", "skipped"),
    [
        (
            "gi",
            {"gtk.pygobject": "python3-gobject"},
            {"gtk.gtk4", "gtk.libadwaita", "gtk.libsecret"},
        ),
        ("Gtk", {"gtk.gtk4": "gtk4"}, set()),
        ("Adw", {"gtk.libadwaita": "libadwaita"}, set()),
        ("Secret", {"gtk.libsecret": "libsecret"}, set()),
        (
            "Gtk,Adw,Secret",
            {"gtk.gtk4": "gtk4", "gtk.libadwaita": "libadwaita", "gtk.libsecret": "libsecret"},
            set(),
        ),
    ],
)
def test_gtk_stack_items(
    sandbox: Env, missing: str, blocked: dict[str, str], skipped: set[str]
) -> None:
    code, checks = _check(sandbox, FAKE_GI_MISSING=missing)
    assert code == 10
    for ident in ("gtk.pygobject", "gtk.gtk4", "gtk.libadwaita", "gtk.libsecret"):
        if ident in blocked:
            assert checks[ident]["status"] == "FIX", ident
            assert checks[ident]["fix"] == [f"sudo dnf install {blocked[ident]}"]
            assert sandbox.env["REVIEW_APP_PYTHON"] in checks[ident]["summary"]
        elif ident in skipped:
            assert checks[ident]["status"] == "SKIP", ident  # it depends on the failed import
        else:
            assert checks[ident]["status"] == "OK", ident


def test_an_absent_system_python_is_a_pygobject_fix(sandbox: Env) -> None:
    code, checks = _check(sandbox, REVIEW_APP_PYTHON="/nonexistent/python3")
    assert code == 10
    assert checks["gtk.pygobject"]["status"] == "FIX"
    assert "/nonexistent/python3" in checks["gtk.pygobject"]["summary"]


def test_the_gtk_probes_run_only_the_system_interpreter_and_never_uv(sandbox: Env) -> None:
    sandbox.run(INSTALL, "--check")
    calls = sandbox.calls()
    probes = [line for line in calls if line.startswith("python3-run")]
    assert probes  # the interpreter was used
    assert all("args=-c " in line for line in probes)
    assert not any("meeting_scribe_review" in line for line in probes)
    assert not any(line.startswith("uv") for line in calls)


# -- hardware and services: GPU, disk, Ollama (F6.3) -------------------------------------------

GIB_KIB = 1024 * 1024  # one GiB in the KiB that `df -Pk` reports


def _statuses(checks: dict[str, dict], *ids: str) -> dict[str, str]:
    return {i: checks[i]["status"] for i in ids}


def test_with_every_prerequisite_present_every_documented_check_passes(sandbox: Env) -> None:
    code, checks = _check(sandbox)
    assert code == 0
    assert set(checks) == CHECK_IDS  # every documented id, and nothing else
    assert {i: c["status"] for i, c in checks.items() if c["status"] != "OK"} == {}


@pytest.mark.parametrize(
    ("rows", "vram", "driver"),
    [
        ("NVIDIA GeForce RTX 5060 Laptop GPU, 8151 MiB, 610.57.04", "OK", "OK"),
        ("Card, 8000 MiB, 1.0", "OK", "OK"),
        ("Card, 7999 MiB, 1.0", "HUMAN", "OK"),
        ("Small GPU, 4096 MiB, 1.0;Big GPU, 24576 MiB, 1.0", "OK", "OK"),
        ("Small GPU, 4096 MiB, 1.0;Other, 6144 MiB, 1.0", "HUMAN", "OK"),
        ("", "SKIP", "HUMAN"),
    ],
)
def test_gpu_and_vram_checks(sandbox: Env, rows: str, vram: str, driver: str) -> None:
    code, checks = _check(sandbox, FAKE_NVIDIA_ROWS=rows)
    assert _statuses(checks, "gpu.driver", "gpu.vram") == {"gpu.driver": driver, "gpu.vram": vram}
    assert code == (0 if "HUMAN" not in {vram, driver} else 11)


def test_the_gpu_detail_names_the_gpu_the_memory_and_the_driver(sandbox: Env) -> None:
    _, checks = _check(sandbox)
    summary = checks["gpu.driver"]["summary"]
    assert "NVIDIA GeForce RTX 5060 Laptop GPU" in summary
    assert "8151 MiB" in summary
    assert "610.57.04" in summary
    assert "8151 MiB" in checks["gpu.vram"]["summary"]
    assert "7000" in checks["gpu.vram"]["detail"]  # the free amount is printed as information


def test_several_gpus_the_first_row_names_the_driver_and_the_largest_total_decides_vram(
    sandbox: Env,
) -> None:
    _, checks = _check(
        sandbox, FAKE_NVIDIA_ROWS="First GPU, 4096 MiB, 9.1;Second GPU, 8192 MiB, 9.1"
    )
    assert "First GPU" in checks["gpu.driver"]["summary"]
    assert "Second GPU" in checks["gpu.driver"]["detail"]
    assert checks["gpu.vram"]["status"] == "OK"  # the largest total counts
    assert "8192 MiB" in checks["gpu.vram"]["summary"]


@pytest.mark.parametrize("mode", ["missing", "fail", "hang"])
def test_a_missing_failing_or_hung_nvidia_smi_is_a_human_item(sandbox: Env, mode: str) -> None:
    if mode == "missing":
        _remove(sandbox, "nvidia-smi")
    code, checks = _check(sandbox, FAKE_NVIDIA_MODE=mode, MEETING_SCRIBE_NVIDIA_SMI_TIMEOUT="1")
    assert code == 11
    driver = checks["gpu.driver"]
    assert driver["status"] == "HUMAN"
    assert driver["human"] is True
    assert driver["fix"] == []
    assert driver["steps"]
    assert checks["gpu.vram"]["status"] == "SKIP"
    expected = {"missing": "not installed", "fail": "failed", "hang": "did not answer"}[mode]
    assert expected in driver["summary"]


def test_the_gpu_checks_read_nvidia_smi_only(sandbox: Env) -> None:
    sandbox.run(INSTALL, "--check")
    smi = [line for line in sandbox.calls() if line.startswith("nvidia-smi")]
    assert smi
    assert all("--query-gpu=" in line and "--format=csv" in line for line in smi)
    assert any("name,memory.total,driver_version" in line for line in smi)


def test_a_low_vram_item_has_no_fix_and_says_so(sandbox: Env) -> None:
    _, checks = _check(sandbox, FAKE_NVIDIA_ROWS="Card, 6144 MiB, 1.0")
    vram = checks["gpu.vram"]
    assert vram["status"] == "HUMAN"
    assert vram["fix"] == []
    assert "8000" in vram["summary"]
    assert "6144" in vram["summary"]
    assert "cannot run the local models" in " ".join(vram["steps"])


def _disk_dirs(sandbox: Env) -> tuple[Path, Path]:
    data = sandbox.home / ".local" / "share"
    cache = sandbox.home / ".cache" / "huggingface"
    data.mkdir(parents=True)
    cache.mkdir(parents=True)
    return data, cache


def _df(rows: list[tuple[Path, str, float, str]]) -> str:
    return ";".join(f"{path}|{dev}|{int(gib * GIB_KIB)}|{mount}" for path, dev, gib, mount in rows)


@pytest.mark.parametrize(
    ("free_gib", "status"),
    [(9.0, "FIX"), (9.5, "FIX"), (10.0, "OK"), (500.0, "OK")],
)
def test_disk_requirement_is_per_filesystem_service_and_models_share_one(
    sandbox: Env, free_gib: float, status: str
) -> None:
    data, cache = _disk_dirs(sandbox)
    one_device = _df([(sandbox.home, "/dev/home", free_gib, "/home")])
    _, checks = _check(sandbox, FAKE_DF=one_device)
    item = checks["disk.space"]
    assert item["status"] == status, item
    if status == "FIX":  # 8 GiB for the service and 2 GiB for the models add up on one filesystem
        assert "/dev/home" in item["summary"]
        assert "/home" in item["summary"]
        assert f"{int(free_gib)}.{int(free_gib * 10) % 10} GiB free" in item["summary"]
        assert "10.0 GiB required" in item["summary"]
        assert item["fix"] == ["du -xsh ~/.cache ~/.local/share"]
        assert item["why"]
    assert data.is_dir()
    assert cache.is_dir()


def test_directories_on_different_filesystems_are_checked_apart(sandbox: Env) -> None:
    data, cache = _disk_dirs(sandbox)
    table = _df([(cache, "/dev/cache", 1.5, "/cache"), (sandbox.home, "/dev/home", 8.5, "/home")])
    _, checks = _check(sandbox, FAKE_DF=table)
    item = checks["disk.space"]
    assert item["status"] == "FIX"
    assert "/dev/cache" in item["summary"]
    assert "1.5 GiB free" in item["summary"]
    assert "2.0 GiB required" in item["summary"]
    assert "/dev/home" not in item["summary"]  # 8.5 GiB covers the 8 GiB the service needs
    table = _df([(cache, "/dev/cache", 2.5, "/cache"), (sandbox.home, "/dev/home", 8.5, "/home")])
    _, checks = _check(sandbox, FAKE_DF=table)
    assert checks["disk.space"]["status"] == "OK"
    assert data.is_dir()


def test_an_existing_version_does_not_raise_the_requirement(sandbox: Env) -> None:
    # The old version is already on disk and is pruned after the new one runs: an upgrade needs
    # room for ONE new environment (8 GiB) plus the models (2 GiB), exactly like a first install.
    data, _ = _disk_dirs(sandbox)
    (data / "meeting-scribe" / "versions" / "20260101000000-1").mkdir(parents=True)
    _, checks = _check(sandbox, FAKE_DF=_df([(sandbox.home, "/dev/home", 9.5, "/home")]))
    assert checks["disk.space"]["status"] == "FIX"
    assert "10.0 GiB required" in checks["disk.space"]["summary"]
    _, checks = _check(sandbox, FAKE_DF=_df([(sandbox.home, "/dev/home", 10.0, "/home")]))
    assert checks["disk.space"]["status"] == "OK"


def test_cached_models_need_no_space_for_the_models(sandbox: Env) -> None:
    _, cache = _disk_dirs(sandbox)
    for repo in (
        "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
        "models--pyannote--speaker-diarization-community-1",
    ):
        (cache / "hub" / repo / "snapshots" / "abc").mkdir(parents=True)
    _, checks = _check(sandbox, FAKE_DF=_df([(sandbox.home, "/dev/home", 8.0, "/home")]))
    assert checks["disk.space"]["status"] == "OK"  # 8 GiB for the service, 0 for the models


def test_the_nearest_existing_parent_is_measured(sandbox: Env) -> None:
    data, _ = _disk_dirs(sandbox)  # meeting-scribe/ itself does not exist on a first install
    sandbox.run(INSTALL, "--check")
    df_calls = [line for line in sandbox.calls() if line.startswith("df")]
    assert f"df -Pk {data}" in df_calls
    assert not (data / "meeting-scribe").exists()


def test_an_unreadable_filesystem_is_a_warning_not_a_blocker(sandbox: Env) -> None:
    code, checks = _check(sandbox, FAKE_DF_FAIL="1")
    assert code == 0
    assert checks["disk.space"]["status"] == "WARN"


def test_ollama_items(sandbox: Env) -> None:
    _remove(sandbox, "ollama")
    code, checks = _check(sandbox)
    assert code == 10
    assert _statuses(checks, "ollama.installed", "ollama.reachable", "ollama.model") == {
        "ollama.installed": "FIX",
        "ollama.reachable": "SKIP",
        "ollama.model": "SKIP",
    }
    assert any("https://ollama.com/download" in line for line in checks["ollama.installed"]["fix"])


@pytest.mark.parametrize(
    ("system_unit", "fix"),
    [("1", "sudo systemctl start ollama"), ("0", "ollama serve")],
)
def test_an_unreachable_ollama_prints_how_to_start_it(
    sandbox: Env, system_unit: str, fix: str
) -> None:
    code, checks = _check(sandbox, FAKE_OLLAMA_MODE="down", FAKE_OLLAMA_SYSTEMD=system_unit)
    assert code == 10
    assert _statuses(checks, "ollama.installed", "ollama.reachable", "ollama.model") == {
        "ollama.installed": "OK",
        "ollama.reachable": "FIX",
        "ollama.model": "SKIP",
    }
    assert checks["ollama.reachable"]["fix"] == [fix]
    assert not any(x.startswith("ollama") for x in sandbox.calls())  # printed, never run


def test_a_hung_ollama_is_cut_off(sandbox: Env) -> None:
    code, checks = _check(sandbox, FAKE_OLLAMA_MODE="hang", MEETING_SCRIBE_OLLAMA_TIMEOUT="1")
    assert code == 10
    assert checks["ollama.reachable"]["status"] == "FIX"


def test_a_missing_default_model_prints_the_pull_command_and_never_pulls(sandbox: Env) -> None:
    code, checks = _check(sandbox, FAKE_OLLAMA_MODE="missing-model")
    assert code == 10
    assert checks["ollama.reachable"]["status"] == "OK"
    assert checks["ollama.model"]["status"] == "FIX"
    assert checks["ollama.model"]["fix"] == ["ollama pull qwen3:4b-instruct-2507-q4_K_M"]
    assert not any(x.startswith("ollama") for x in sandbox.calls())


def test_ollama_is_probed_through_its_http_api_on_the_default_address(sandbox: Env) -> None:
    sandbox.run(INSTALL, "--check")
    curls = [line for line in sandbox.calls() if line.startswith("curl")]
    assert len(curls) == 1
    assert "http://127.0.0.1:11434/api/tags" in curls[0]
    assert "--max-time" in curls[0]


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:9999/v1", "http://127.0.0.1:9999/v1/", "http://127.0.0.1:9999/"],
)
def test_a_non_default_ollama_address_from_the_environment_is_honoured(
    sandbox: Env, url: str
) -> None:
    sandbox.run(INSTALL, "--check", SUMMARIES_LOCAL_BASE_URL=url)
    curls = [line for line in sandbox.calls() if line.startswith("curl")]
    assert any("http://127.0.0.1:9999/api/tags" in line for line in curls), curls


def test_without_curl_the_ollama_probes_are_skipped(sandbox: Env) -> None:
    _remove(sandbox, "curl")
    code, checks = _check(sandbox)
    assert code == 10  # tool.curl is the FIX item
    assert checks["tool.curl"]["status"] == "FIX"
    assert _statuses(checks, "ollama.reachable", "ollama.model") == {
        "ollama.reachable": "SKIP",
        "ollama.model": "SKIP",
    }


def _config(sandbox: Env) -> Path:
    config = sandbox.home / ".config" / "meeting-scribe"
    config.mkdir(parents=True, exist_ok=True)
    return config


def _model_check(sandbox: Env, **extra: str) -> dict:
    _, checks = _check(sandbox, **extra)
    return checks["ollama.model"]


def test_the_configured_model_comes_from_the_environment(sandbox: Env) -> None:
    item = _model_check(sandbox, SUMMARIES_LOCAL_MODEL="mistral:7b", FAKE_OLLAMA_MODEL="mistral:7b")
    assert item["status"] == "OK"
    assert "mistral:7b" in item["summary"]


def test_the_dotenv_is_read_for_that_one_line_and_nothing_else(sandbox: Env) -> None:
    (_config(sandbox) / ".env").write_text(
        'HF_TOKEN=not-a-real-token\nSUMMARIES_LOCAL_MODEL="gemma3:4b"  \n'
    )
    result = sandbox.run(INSTALL, "--check", "--json", FAKE_OLLAMA_MODEL="gemma3:4b")
    item = {c["id"]: c for c in _json(result)["checks"]}["ollama.model"]
    assert item["status"] == "OK"
    assert "gemma3:4b" in item["summary"]
    assert "not-a-real-token" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "line",
    [
        'SUMMARIES_LOCAL_MODEL="gemma3:4b"  # the small one\r\n',  # CRLF, quotes, inline comment
        "SUMMARIES_LOCAL_MODEL='gemma3:4b'\r\n",
        "SUMMARIES_LOCAL_MODEL=  gemma3:4b   # note\n",
        "SUMMARIES_LOCAL_MODEL=gemma3:4b\t#note\n",
    ],
)
def test_the_dotenv_model_line_is_parsed_robustly(sandbox: Env, line: str) -> None:
    (_config(sandbox) / ".env").write_bytes(line.encode())
    assert _model_check(sandbox, FAKE_OLLAMA_MODEL="gemma3:4b")["status"] == "OK"


def test_a_model_name_containing_a_hash_is_kept_when_no_space_precedes_it(sandbox: Env) -> None:
    (_config(sandbox) / ".env").write_text("SUMMARIES_LOCAL_MODEL=org/model#1\n")
    item = _model_check(sandbox)
    assert item["status"] == "FIX"
    assert item["fix"] == ["ollama pull org/model#1"]


def test_the_configured_model_comes_from_the_preferences_file(sandbox: Env) -> None:
    (_config(sandbox) / "settings.json").write_text(
        '{"version": 1, "values": {"summaries.provider": "local",'
        ' "summaries.local_model": "gemma3:4b"}}\n'
    )
    assert _model_check(sandbox, FAKE_OLLAMA_MODEL="gemma3:4b")["status"] == "OK"


def test_the_environment_and_dotenv_override_the_preferences_file(sandbox: Env) -> None:
    (_config(sandbox) / "settings.json").write_text(
        '{"version":1,"values":{"summaries.local_model":"gemma3:4b"}}'
    )
    from_env = _model_check(sandbox, SUMMARIES_LOCAL_MODEL="mistral:7b")
    assert from_env["fix"] == ["ollama pull mistral:7b"]
    (_config(sandbox) / ".env").write_text("SUMMARIES_LOCAL_MODEL=llama3.2\n")
    assert _model_check(sandbox)["status"] == "OK"  # the fake lists llama3.2:latest


def test_an_unreadable_preferences_file_falls_back_to_the_default_model(sandbox: Env) -> None:
    (_config(sandbox) / "settings.json").write_text("not json at all")
    item = _model_check(sandbox)
    assert item["status"] == "OK"
    assert "qwen3:4b-instruct-2507-q4_K_M" in item["summary"]


def test_a_model_name_without_a_tag_matches_its_latest_tag(sandbox: Env) -> None:
    assert _model_check(sandbox, SUMMARIES_LOCAL_MODEL="llama3.2")["status"] == "OK"


def test_a_model_name_is_matched_whole_not_as_a_prefix(sandbox: Env) -> None:
    assert _model_check(sandbox, SUMMARIES_LOCAL_MODEL="llama3")["status"] == "FIX"


# -- review fixes: Ollama address, timeouts, os-release, wording ---------------------------------


def _curl_urls(sandbox: Env) -> list[str]:
    return [line for line in sandbox.calls() if line.startswith("curl")]


def test_the_ollama_address_comes_from_the_dotenv_line(sandbox: Env) -> None:
    (_config(sandbox) / ".env").write_text(
        'HF_TOKEN=not-a-real-token\nSUMMARIES_LOCAL_BASE_URL="http://127.0.0.1:9998/v1"  # mine\n'
    )
    result = sandbox.run(INSTALL, "--check", "--json")
    assert any("http://127.0.0.1:9998/api/tags" in line for line in _curl_urls(sandbox))
    assert "not-a-real-token" not in result.stdout + result.stderr


def test_the_environment_address_wins_over_the_dotenv_and_the_default(sandbox: Env) -> None:
    (_config(sandbox) / ".env").write_text("SUMMARIES_LOCAL_BASE_URL=http://127.0.0.1:9998\n")
    sandbox.run(INSTALL, "--check", SUMMARIES_LOCAL_BASE_URL="http://127.0.0.1:9999/v1")
    (url,) = _curl_urls(sandbox)
    assert "http://127.0.0.1:9999/api/tags" in url
    assert "9998" not in url


def test_the_preferences_file_has_no_ollama_address_so_it_is_ignored(sandbox: Env) -> None:
    # Preferences owns no such field (preferences/domain/fields.py): the service reads the address
    # from the environment and `.env` only, so the preflight must not read it from settings.json.
    (_config(sandbox) / "settings.json").write_text(
        '{"version":1,"values":{"summaries.local_base_url":"http://127.0.0.1:9997/v1"}}'
    )
    sandbox.run(INSTALL, "--check")
    (url,) = _curl_urls(sandbox)
    assert "http://127.0.0.1:11434/api/tags" in url
    assert "9997" not in url


def _log_timeouts(sandbox: Env) -> None:
    """Replace the `timeout` link by a wrapper that logs its arguments, then runs the real one."""
    fakes = Path(sandbox.env["PATH"])
    real = (fakes / "timeout").resolve()
    (fakes / "timeout").unlink()
    wrapper = fakes / "timeout"
    wrapper.write_text(f'#!/usr/bin/env bash\necho "timeout $*" >> "$FAKE_LOG"\nexec {real} "$@"\n')
    wrapper.chmod(0o755)


def _timeouts(sandbox: Env, tool: str) -> list[str]:
    return [x for x in sandbox.calls() if x.startswith("timeout") and f" {tool}" in x]


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5", ""])
def test_an_invalid_nvidia_smi_timeout_falls_back_to_10_seconds(sandbox: Env, value: str) -> None:
    _log_timeouts(sandbox)
    sandbox.run(INSTALL, "--check", MEETING_SCRIBE_NVIDIA_SMI_TIMEOUT=value)
    assert _timeouts(sandbox, "nvidia-smi")[0].startswith("timeout --kill-after=2 10 nvidia-smi")


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5", ""])
def test_an_invalid_ollama_timeout_falls_back_to_3_seconds(sandbox: Env, value: str) -> None:
    _log_timeouts(sandbox)
    sandbox.run(INSTALL, "--check", MEETING_SCRIBE_OLLAMA_TIMEOUT=value)
    (call,) = _timeouts(sandbox, "curl")
    assert call.startswith("timeout --kill-after=2 5 curl")  # 3 s plus 2 s of margin
    assert "--max-time 3 " in call


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5", ""])
def test_an_invalid_models_check_timeout_falls_back_to_30_seconds(sandbox: Env, value: str) -> None:
    _log_timeouts(sandbox)
    result = sandbox.run(INSTALL, MEETING_SCRIBE_MODELS_CHECK_TIMEOUT=value, **IDLE)
    assert result.returncode == 0, result.stderr
    (call,) = _timeouts(sandbox, str(sandbox.root))
    assert call.startswith("timeout --kill-after=5 30 ")
    assert call.endswith("prefetch-models --check")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('ID=fedora\nVERSION_ID="44"\n', "fedora-44"),
        ("ID=fedora\nVERSION_ID=44", "fedora-44"),  # no trailing newline
        ("ID='fedora'\nVERSION_ID='44'", "fedora-44"),  # single quotes
        ('NAME="Fedora Linux"\nID=fedora\nVERSION_ID="44"\nVARIANT_ID=workstation\n', "fedora-44"),
        ("ID=arch\n", "arch"),  # a rolling release has no VERSION_ID
        ("NAME=Nothing\n", "unknown"),
        ("", "unknown"),
    ],
)
def test_pf_os_reads_the_os_release_file_as_data(
    sandbox: Env, tmp_path: Path, content: str, expected: str
) -> None:
    fixture = tmp_path / "os-release-fixture"
    fixture.write_text(content)
    result = sandbox.snippet("pf_os", OS_RELEASE_FILE=str(fixture))
    assert result.stdout.strip() == expected


def test_pf_os_without_the_file_is_unknown(sandbox: Env, tmp_path: Path) -> None:
    result = sandbox.snippet("pf_os", OS_RELEASE_FILE=str(tmp_path / "missing"))
    assert result.stdout.strip() == "unknown"


def test_the_session_bus_summary_names_both_probes(sandbox: Env) -> None:
    run_dir = sandbox.home.parent / "run"
    _, checks = _check(sandbox, DBUS_SESSION_BUS_ADDRESS="", XDG_RUNTIME_DIR=str(run_dir))
    summary = checks["session.bus"]["summary"]
    assert "DBUS_SESSION_BUS_ADDRESS" in summary
    assert "XDG_RUNTIME_DIR" in summary
    assert "/bus" in summary


def test_the_human_only_summary_has_no_fix_commands_to_run(sandbox: Env) -> None:
    items = _add("HUMAN", "gpu.driver", "no driver", "needs a GPU", steps="install it")
    result = sandbox.snippet(f"{items} pf_report text")
    assert result.stdout.splitlines()[-1] == (
        "Summary: 0 fixable, 1 human-only. Nothing was changed. "
        "Do the human-only steps, then run ./install.sh --check again."
    )
