"""Sandbox for the install, uninstall and preflight tests: a throwaway HOME and a PATH of fakes.

The scripts run in a throwaway HOME with a PATH that holds only fake system tools plus symlinks to a
whitelist of coreutils, so the real tools can never run. Every fake appends its argv to a log file,
which is also the proof the fakes were used.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
INSTALL = SCRIPTS / "install.sh"
UNINSTALL = SCRIPTS / "uninstall.sh"
ROOT_INSTALL = ROOT / "install.sh"
ROOT_UNINSTALL = ROOT / "uninstall.sh"
PREFLIGHT = SCRIPTS / "preflight.sh"
UNIT = "meeting-scribe.service"
ACTIVATION = "dev.meetingscribe.Service.service"
EXTENSION = "meeting-scribe@meetingscribe.dev"
DESKTOP_ENTRY = "dev.meetingscribe.Review.desktop"
COREUTILS = [
    "bash",
    "env",
    "mkdir",
    "chmod",
    "cp",
    "rm",
    "rmdir",
    "mktemp",
    "dirname",
    "basename",
    "cat",
    "sed",
    "grep",
    "tr",
    "head",
    "cut",
    "sort",
    "ln",
    "date",
    "mv",
    "touch",
    "readlink",
    "timeout",
    "sleep",
]

FAKES = {
    "uv": """
case "$1 $2" in
  "tool install")
    echo "uv-env UV_TOOL_DIR=${UV_TOOL_DIR:-} UV_TOOL_BIN_DIR=${UV_TOOL_BIN_DIR:-}" >> "$FAKE_LOG"
    [ "${FAKE_UV_FAIL:-0}" = 1 ] && { mkdir -p "$UV_TOOL_DIR"; echo "uv: boom" >&2; exit 2; }
    mkdir -p "$UV_TOOL_BIN_DIR"
    [ "${FAKE_UV_NO_EXE:-0}" = 1 ] && exit 0
    # the installed entry point: only `prefetch-models --check` is ever run by the scripts
    cat > "$UV_TOOL_BIN_DIR/meeting-scribe" <<'EOS'
#!/usr/bin/env bash
echo "meeting-scribe $* (from $0)" >> "$FAKE_LOG"
if [ "$1" = prefetch-models ]; then
  # The installer never downloads: only the offline cache check may run.
  [ "$2" = --check ] || { echo "download attempted" >> "$FAKE_LOG.download"; exit 9; }
  [ -n "${FAKE_MODELS_CHECK_SLEEP:-}" ] && exec sleep "$FAKE_MODELS_CHECK_SLEEP"
  [ "${FAKE_MODELS_CACHED:-1}" = 1 ] && exit 0 || exit 1
fi
exit 0
EOS
    chmod +x "$UV_TOOL_BIN_DIR/meeting-scribe" ;;
  "tool dir")
    echo "uv-env-dir UV_TOOL_DIR=${UV_TOOL_DIR:-}" \\
         "UV_TOOL_BIN_DIR=${UV_TOOL_BIN_DIR:-}" >> "$FAKE_LOG"
    echo "${FAKE_UV_TOOL_BIN:-/legacy/.local/bin}" ;;
esac
""",
    "systemctl": """
case "$*" in
  *"cat ollama.service"*) [ "${FAKE_OLLAMA_SYSTEMD:-1}" = 1 ] && exit 0 || exit 1 ;;
  *is-active*) [ ! -e "$FAKE_LOG.failed" ] && [ "${FAKE_UNIT_ACTIVE:-0}" = 1 ] \\
               && exit 0 || exit 3 ;;
  *enable*|*restart*|*daemon-reload*)
    case "$*" in
      *"${FAKE_SYSTEMCTL_FAIL:-@none@}"*) touch "$FAKE_LOG.failed"; echo "Job failed" >&2; exit 1 ;;
    esac ;;
esac
""",
    "gnome-extensions": """
case "$1" in
  pack|install)
    [ "${FAKE_EXT_FAIL:-}" = "$1" ] && { echo "ext $1 failed" >&2; exit 4; }
    exit 0 ;;
  info) [ "${FAKE_EXT_KNOWN:-1}" = 1 ] && echo "State: ENABLED" && exit 0 || exit 1 ;;
  enable) [ "${FAKE_EXT_ENABLE_FAIL:-0}" = 1 ] && exit 2 || exit 0 ;;
esac
""",
    # The review app's interpreter probe. Each probe fails when its own name is listed in
    # FAKE_GI_MISSING (comma separated: gi, Gtk, Adw, Secret). Any other call just succeeds.
    "python3": """
missing=",${FAKE_GI_MISSING:-},"
case "$*" in
  *Secret*) [[ "$missing" == *,Secret,* ]] && exit 1 ;;
  *Adw*) [[ "$missing" == *,Adw,* ]] && exit 1 ;;
  *Gtk*) [[ "$missing" == *,Gtk,* ]] && exit 1 ;;
  *"import gi"*) [[ "$missing" == *,gi,* ]] && exit 1 ;;
esac
echo "python3-run PYTHONPATH=${PYTHONPATH:-} PYTHONDONTWRITEBYTECODE=${PYTHONDONTWRITEBYTECODE:-}" \
     "args=$*" >> "$FAKE_LOG"
exit 0
""",
    # Presence only: the preflight reads the HTTP API through curl and never runs ollama.
    # Any call is logged and fails, so a pull, run, list or serve by the installer is visible.
    "ollama": 'echo "ollama: unexpected call" >&2; exit 9',
    # Records the call: the installer must never run it (it only prints sudo commands).
    "sudo": "echo 'sudo was called by the installer' >&2; exit 99",
    # Present and working by default; a test removes the file to make the tool "missing".
    "ffmpeg": "",
    "pactl": "",
    # The Ollama API. FAKE_OLLAMA_MODE: ok (default), missing-model (the configured model is not
    # listed), down (connection refused), hang. FAKE_OLLAMA_MODEL names the configured model.
    "curl": r"""
case "${FAKE_OLLAMA_MODE:-ok}" in
  down) echo "curl: (7) Failed to connect" >&2; exit 7 ;;
  hang) exec sleep 30 ;;
esac
model="${FAKE_OLLAMA_MODEL:-qwen3:4b-instruct-2507-q4_K_M}"
tags='{"models":[{"name":"llama3.2:latest","model":"llama3.2:latest"}'
if [ "${FAKE_OLLAMA_MODE:-ok}" != missing-model ]; then
  tags="$tags"',{"name":"'"$model"'","model":"'"$model"'"}'
fi
echo "$tags]}"
""",
    # name, memory.total and driver_version rows separated by `;` in FAKE_NVIDIA_ROWS.
    "nvidia-smi": r"""
case "${FAKE_NVIDIA_MODE:-ok}" in
  fail) echo "NVIDIA-SMI has failed: no driver" >&2; exit 9 ;;
  hang) exec sleep 30 ;;
esac
case "$*" in
  *memory.free*) echo "${FAKE_NVIDIA_FREE:-7000}"; exit 0 ;;
esac
default="NVIDIA GeForce RTX 5060 Laptop GPU, 8151 MiB, 610.57.04"
printf '%s\n' "${FAKE_NVIDIA_ROWS-$default}" | tr ';' '\n'
""",
    # `df -Pk <path>`: FAKE_DF holds `prefix|device|available KiB|mount` rows separated by `;`; the
    # first row whose prefix starts the path answers. The default is 500 GiB free on one device.
    "df": r"""
[ -n "${FAKE_DF_FAIL:-}" ] && { echo "df: cannot read" >&2; exit 1; }
path="${!#}"
IFS=';' read -ra rows <<<"${FAKE_DF:-/|/dev/fake1|524288000|/}"
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
for row in "${rows[@]}"; do
  IFS='|' read -r prefix device avail mount <<<"$row"
  case "$path" in "$prefix"*) echo "$device 999999999 1 $avail 1% $mount"; exit 0 ;; esac
done
exit 1
""",
    "secret-tool": "",
    # The tray runs on it; present by default, a test removes the file to make it "missing".
    "gjs": "",
    "gnome-shell": 'echo "GNOME Shell ${FAKE_SHELL_VERSION:-50.4}"',
    # `rpm -q <package>`: the voice sample plugin is installed unless FAKE_GSTREAMER=0.
    "rpm": '[ "${FAKE_GSTREAMER:-1}" = 1 ]',
    "update-desktop-database": "",
    # The desktop names (docs/specs/desktop-tray.md section 7) answer apart from the service:
    # FAKE_GNOME_SHELL_OWNER and FAKE_TRAY_HOST_OWNER are 1 (default, owned), 0 (no owner) or
    # fail (the bus does not answer). The default is a GNOME Shell session.
    "gdbus": """
mode="${FAKE_GDBUS_MODE:-noowner}"
desktop_owner() {
  case "$1" in
    1) echo "(true,)" ;;
    0) echo "(false,)" ;;
    *) echo "Error: timeout" >&2; exit 1 ;;
  esac
}
case "$*" in
  *"NameHasOwner org.gnome.Shell") desktop_owner "${FAKE_GNOME_SHELL_OWNER:-1}" ;;
  *"NameHasOwner org.kde.StatusNotifierWatcher") desktop_owner "${FAKE_TRAY_HOST_OWNER:-1}" ;;
  *NameHasOwner*)
    case "$mode" in
      idle-then-none)
        n="$(cat "$FAKE_LOG.owners" 2>/dev/null || echo 0)"; echo $((n + 1)) > "$FAKE_LOG.owners"
        [ "$n" = 0 ] && echo "(true,)" || echo "(false,)" ;;
      owner-fail) echo "Error: timeout" >&2; exit 1 ;;
      owner-garbage) echo "what" ;;
      noowner) echo "(false,)" ;;
      *) echo "(true,)" ;;
    esac ;;
  *Properties.Get*)
    case "$mode" in
      recording) echo "(<'RECORDING'>,)" ;;
      weird) echo "(<'WEIRD'>,)" ;;
      stopped) echo "(<'STOPPED'>,)" ;;
      idle-then-recording)
        n="$(cat "$FAKE_LOG.gets" 2>/dev/null || echo 0)"; echo $((n + 1)) > "$FAKE_LOG.gets"
        [ "$n" = 0 ] && echo "(<'IDLE'>,)" || echo "(<'RECORDING'>,)" ;;
      get-fail) echo "Error: timeout" >&2; exit 1 ;;
      get-garbage) echo "what" ;;
      get-empty) ;;
      *) echo "(<'IDLE'>,)" ;;
    esac ;;
esac
""",
}

# The read-only questions the preflight asks the bus daemon to detect the desktop (never the
# service). Any other gdbus call during a preflight is a call to the service.
DESKTOP_NAMES = ("org.gnome.Shell", "org.kde.StatusNotifierWatcher")
DESKTOP_QUERY = (
    "gdbus call --session --timeout 5 --dest org.freedesktop.DBus "
    "--object-path /org/freedesktop/DBus --method org.freedesktop.DBus.NameHasOwner {name}"
)


def is_desktop_query(line: str) -> bool:
    """True for exactly one of the logged desktop detection calls, and nothing else."""
    return any(line == DESKTOP_QUERY.format(name=name) for name in DESKTOP_NAMES)


@dataclass
class Env:
    home: Path
    log: Path
    env: dict[str, str]

    @property
    def unit_path(self) -> Path:
        return self.home / ".config" / "systemd" / "user" / UNIT

    @property
    def activation_path(self) -> Path:
        return self.home / ".local" / "share" / "dbus-1" / "services" / ACTIVATION

    @property
    def root(self) -> Path:
        return self.home / ".local" / "share" / "meeting-scribe"

    @property
    def desktop_entry(self) -> Path:
        return self.home / ".local" / "share" / "applications" / DESKTOP_ENTRY

    @property
    def exe(self) -> Path:
        return self.root / "current" / "bin" / "meeting-scribe"

    @property
    def versions(self) -> list[Path]:
        folder = self.root / "versions"
        return sorted(folder.iterdir()) if folder.exists() else []

    @property
    def unit_exec_start(self) -> str:
        lines = self.unit_path.read_text().splitlines()
        return next(line for line in lines if line.startswith("ExecStart="))

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def snippet(self, code: str, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run bash code with the common helpers and the preflight library already sourced."""
        header = f'source "{SCRIPTS}/_common.sh"; source "{PREFLIGHT}"; '
        return subprocess.run(  # noqa: S603  # fixed argv, sandboxed PATH
            [str(self.env["BASH"]), "-c", header + code],
            env={**self.env, **extra},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def tree(self) -> list[str]:
        """Every path under HOME, relative and sorted: the proof that nothing was written."""
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*"))

    def run(self, script: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **extra}
        return subprocess.run(  # noqa: S603  # fixed argv, sandboxed PATH
            [str(self.env["BASH"]), str(script), *args],
            env=env,
            stdin=subprocess.DEVNULL,  # the scripts must never wait for input
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )


@pytest.fixture(name="sandbox")
def sandbox_fixture(tmp_path: Path) -> Env:
    home = tmp_path / "home"
    home.mkdir()
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    for name in COREUTILS:
        found = shutil.which(name)
        if found:
            (fakes / name).symlink_to(found)
    log = tmp_path / "calls.log"
    for name, body in FAKES.items():
        path = fakes / name
        path.write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "$FAKE_LOG"\n{body}')
        path.chmod(0o755)
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=fedora\nVERSION_ID="44"\n')
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "PATH": str(fakes),
        "FAKE_LOG": str(log),
        "BASH": str(shutil.which("bash")),
        "REVIEW_APP_PYTHON": str(fakes / "python3"),
        "OS_RELEASE_FILE": str(os_release),
        "XDG_SESSION_TYPE": "wayland",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent/bus",
    }
    return Env(home=home, log=log, env=env)
