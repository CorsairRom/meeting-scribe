# Preflight library for install.sh (sourced, not executed). Everything here is READ-ONLY: it never
# creates a file, never runs sudo, never prompts and never calls a service. It collects one item per
# check (stable id, status, summary, why, printed fix commands or manual steps) and renders them as
# text or as one JSON document. Contract: docs/specs/install.md section 4 and ADR-010.
#
# Statuses: OK, WARN (advisory), FIX (a command fixes it), HUMAN (only the user can), SKIP (depends
# on a failed check). FIX and HUMAN block the install.
# Exit codes (pf_exit_code): 0 nothing blocks, 10 at least one FIX, 11 only HUMAN items remain.
# External commands come from PATH so tests can substitute fakes.

PF_ID=()
PF_STATUS=()
PF_SUMMARY=()
PF_WHY=()
PF_FIX=()    # newline-joined commands; each is printed, never run
PF_STEPS=()  # newline-joined manual steps of a HUMAN item
PF_DETAIL=() # one informational line

readonly PF_EXIT_OK=0
readonly PF_EXIT_FIXABLE=10
readonly PF_EXIT_HUMAN_ONLY=11
readonly PF_SCHEMA_VERSION=1

# pf_add STATUS ID SUMMARY WHY: starts an item; pf_fix, pf_step and pf_detail attach to the last one.
pf_add() {
  PF_STATUS+=("$1")
  PF_ID+=("$2")
  PF_SUMMARY+=("$3")
  PF_WHY+=("${4:-}")
  PF_FIX+=("")
  PF_STEPS+=("")
  PF_DETAIL+=("")
}

pf_fix() {
  local last=$((${#PF_ID[@]} - 1))
  PF_FIX[last]+="${PF_FIX[last]:+$'\n'}$1"
}

pf_step() {
  local last=$((${#PF_ID[@]} - 1))
  PF_STEPS[last]+="${PF_STEPS[last]:+$'\n'}$1"
}

pf_detail() {
  PF_DETAIL[${#PF_ID[@]} - 1]="$1"
}

# Prints 0, 10 or 11 (see the header).
pf_exit_code() {
  local status fixable=0 human=0
  for status in "${PF_STATUS[@]}"; do
    case "$status" in
      FIX) fixable=1 ;;
      HUMAN) human=1 ;;
    esac
  done
  if ((fixable)); then
    echo "$PF_EXIT_FIXABLE"
  elif ((human)); then
    echo "$PF_EXIT_HUMAN_ONLY"
  else
    echo "$PF_EXIT_OK"
  fi
}

pf_count() { # pf_count STATUS: how many items have it
  local status n=0
  for status in "${PF_STATUS[@]}"; do
    if [[ "$status" == "$1" ]]; then n=$((n + 1)); fi
  done
  echo "$n"
}

# `<ID>-<VERSION_ID>` from the os-release file (read as data, never sourced), or `unknown`.
pf_os() {
  local file="${OS_RELEASE_FILE:-/etc/os-release}" key value id="" version=""
  if [[ -r "$file" ]]; then
    while IFS='=' read -r key value; do
      value="${value%\"}"
      value="${value#\"}"
      case "$key" in
        ID) id="$value" ;;
        VERSION_ID) version="$value" ;;
      esac
    done <"$file"
  fi
  if [[ -n "$id" ]]; then echo "$id${version:+-$version}"; else echo unknown; fi
}

# Escapes $1 for a JSON string: backslash, quote and every control character.
pf_json_escape() {
  local s="$1" out="" ch hex i
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\r'/\\r}"
  if [[ "$s" =~ [[:cntrl:]] ]]; then
    for ((i = 0; i < ${#s}; i++)); do
      ch="${s:i:1}"
      if [[ "$ch" =~ [[:cntrl:]] ]]; then
        printf -v hex '\\u%04x' "'$ch"
        out+="$hex"
      else
        out+="$ch"
      fi
    done
    s="$out"
  fi
  printf '%s' "$s"
}

pf_json_array() { # pf_json_array NEWLINE_JOINED
  local line out="" first=1
  if [[ -n "$1" ]]; then
    while IFS= read -r line; do
      if ((first)); then first=0; else out+=", "; fi
      out+="\"$(pf_json_escape "$line")\""
    done <<<"$1"
  fi
  printf '[%s]' "$out"
}

pf_report_json() {
  local i n=${#PF_ID[@]} result=ok code human
  code="$(pf_exit_code)"
  if ((code != PF_EXIT_OK)); then result=blocked; fi
  printf '{\n  "schema_version": %s,\n  "result": "%s",\n  "exit_code": %s,\n' \
    "$PF_SCHEMA_VERSION" "$result" "$code"
  printf '  "os": "%s",\n  "checks": [' "$(pf_json_escape "$(pf_os)")"
  for ((i = 0; i < n; i++)); do
    human=false
    if [[ "${PF_STATUS[i]}" == HUMAN ]]; then human=true; fi
    ((i)) && printf ','
    printf '\n    {"id": "%s", "status": "%s", "human": %s,\n' \
      "$(pf_json_escape "${PF_ID[i]}")" "${PF_STATUS[i]}" "$human"
    printf '     "summary": "%s",\n     "why": "%s",\n' \
      "$(pf_json_escape "${PF_SUMMARY[i]}")" "$(pf_json_escape "${PF_WHY[i]}")"
    printf '     "fix": %s, "steps": %s,\n     "detail": "%s"}' \
      "$(pf_json_array "${PF_FIX[i]}")" "$(pf_json_array "${PF_STEPS[i]}")" \
      "$(pf_json_escape "${PF_DETAIL[i]}")"
  done
  if ((n)); then printf '\n  '; fi
  printf ']\n}\n'
}

pf_tag() {
  case "$1" in
    OK) printf '[ OK  ]' ;;
    WARN) printf '[ WARN]' ;;
    FIX) printf '[ FIX ]' ;;
    HUMAN) printf '[HUMAN]' ;;
    *) printf '[ SKIP]' ;;
  esac
}

# One tagged line per item at column 0; the explanation of a FIX, HUMAN or WARN item is indented
# under it (`why:`, `fix:`, `steps:`, `note:`), so a line-oriented parser can follow it.
# $1 limits the output to one status (used to show only the warnings of a passing preflight).
pf_items_text() {
  local only="${1:-}" i line
  for i in "${!PF_ID[@]}"; do
    if [[ -n "$only" && "${PF_STATUS[i]}" != "$only" ]]; then continue; fi
    printf '%s %-22s %s\n' "$(pf_tag "${PF_STATUS[i]}")" "${PF_ID[i]}" "${PF_SUMMARY[i]}"
    case "${PF_STATUS[i]}" in
      FIX | HUMAN | WARN)
        printf '         why: %s\n' "${PF_WHY[i]}"
        if [[ -n "${PF_FIX[i]}" ]]; then
          while IFS= read -r line; do printf '         fix: %s\n' "$line"; done <<<"${PF_FIX[i]}"
        fi
        if [[ -n "${PF_STEPS[i]}" ]]; then
          while IFS= read -r line; do printf '         steps: %s\n' "$line"; done <<<"${PF_STEPS[i]}"
        fi
        if [[ -n "${PF_DETAIL[i]}" ]]; then printf '         note: %s\n' "${PF_DETAIL[i]}"; fi ;;
    esac
  done
}

pf_report_text() {
  local fixable human warnings
  pf_items_text
  fixable="$(pf_count FIX)"
  human="$(pf_count HUMAN)"
  warnings="$(pf_count WARN)"
  if ((fixable + human == 0)); then
    if ((warnings)); then
      echo "Summary: nothing blocks the install ($warnings warning(s))."
    else
      echo "Summary: nothing blocks the install."
    fi
  elif ((fixable == 0)); then
    echo "Summary: $fixable fixable, $human human-only. Nothing was changed. Do the human-only steps, then run ./install.sh --check again."
  else
    echo "Summary: $fixable fixable, $human human-only. Nothing was changed. Run the fix commands, then run ./install.sh --check again."
  fi
}

# pf_report text|json
pf_report() {
  case "$1" in
    json) pf_report_json ;;
    *) pf_report_text ;;
  esac
}

# -- checks ---------------------------------------------------------------------------------------

# The repository root: install.sh sets REPO_ROOT; sourced alone (tests), it is found from this file.
PF_REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PF_EXTENSION_METADATA="$PF_REPO_ROOT/gnome-extension/meeting-scribe@meetingscribe.dev/metadata.json"

pf_have() { command -v "$1" >/dev/null 2>&1; }

# pf_tool ID COMMAND STATUS PACKAGE WHY [DETAIL]: STATUS (FIX or WARN) is the one used when the
# command is not on PATH; the fix is the dnf package, printed and never run.
pf_tool() {
  local id="$1" command_name="$2" status="$3" package="$4" why="$5" detail="${6:-}"
  if pf_have "$command_name"; then
    pf_add OK "$id" "$command_name found at $(command -v "$command_name")" "$why"
  else
    pf_add "$status" "$id" "$command_name is not installed" "$why"
    pf_fix "sudo dnf install $package"
    if [[ -n "$detail" ]]; then pf_detail "$detail"; fi
  fi
}

# The majors of the `shell-version` list of the extension metadata, space separated (empty when the
# file or the key cannot be read). The required versions live there and nowhere else.
pf_declared_shell_majors() {
  local text list entry out="" entries=()
  if [[ ! -r "$PF_EXTENSION_METADATA" ]]; then return 0; fi
  text="$(tr -d '\n\r' <"$PF_EXTENSION_METADATA")"
  case "$text" in
    *'"shell-version"'*) ;;
    *) return 0 ;;
  esac
  list="${text#*\"shell-version\"}"
  list="${list#*\[}"
  list="${list%%\]*}"
  IFS=',' read -ra entries <<<"$list"
  for entry in "${entries[@]}"; do
    entry="${entry//[[:space:]\"]/}"
    entry="${entry%%.*}"
    if [[ -n "$entry" ]]; then out+="${out:+ }$entry"; fi
  done
  printf '%s' "$out"
}

pf_check_session() {
  local session="${XDG_SESSION_TYPE:-}" required majors version major wanted found=0 shell_out

  if [[ "$session" == wayland ]]; then
    pf_add OK session.wayland "XDG_SESSION_TYPE is wayland" \
      "the extension and the review app target GNOME on Wayland"
  else
    pf_add HUMAN session.wayland "XDG_SESSION_TYPE is '${session:-unset}'; GNOME on Wayland is required" \
      "the extension and the review app target GNOME on Wayland"
    pf_step 'Log out, choose "GNOME" (not "GNOME on Xorg") on the login screen, then run ./install.sh from a terminal in that session'
  fi

  majors="$(pf_declared_shell_majors)"
  required="${majors// /, }"
  if [[ -z "$majors" ]]; then
    pf_add HUMAN session.gnome-shell "the shell-version list could not be read from the extension metadata" \
      "GNOME Shell refuses to load an extension for another version, and the required versions are read from the metadata"
    pf_step "Check that the checkout is complete: $PF_EXTENSION_METADATA must list \"shell-version\""
  elif ! pf_have gnome-shell; then
    pf_add HUMAN session.gnome-shell "gnome-shell is not installed; the extension declares GNOME Shell $required" \
      "GNOME Shell refuses to load an extension for another version"
    pf_step "Run a GNOME session with GNOME Shell $required (the version listed in the extension metadata)"
  else
    shell_out="$(gnome-shell --version 2>/dev/null || true)"
    version="${shell_out##* }"
    major="${version%%.*}"
    for wanted in $majors; do
      if [[ "$wanted" == "$major" ]]; then found=1; fi
    done
    if ((found)); then
      pf_add OK session.gnome-shell "GNOME Shell $version (the extension declares $required)" \
        "GNOME Shell refuses to load an extension for another version"
    else
      pf_add HUMAN session.gnome-shell "GNOME Shell ${version:-unknown} found; the extension declares GNOME Shell $required" \
        "GNOME Shell refuses to load an extension for another version"
      pf_step "Use a GNOME session with GNOME Shell $required (the version listed in the extension metadata)"
    fi
  fi

  if [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" || ( -n "${XDG_RUNTIME_DIR:-}" && -e "${XDG_RUNTIME_DIR:-}/bus" ) ]]; then
    pf_add OK session.bus "the session bus is reachable" \
      "the service, the extension and the apps talk over the session bus"
  else
    pf_add HUMAN session.bus "no session bus was found (DBUS_SESSION_BUS_ADDRESS is unset)" \
      "the service, the extension and the apps talk over the session bus, and systemctl --user needs it"
    pf_step "Run ./install.sh from a terminal inside the GNOME session (not over SSH or from a tool without the session variables)"
  fi
}

pf_check_tools() {
  pf_tool tool.uv uv FIX uv "it installs the service and its Python 3.12 environment"
  pf_tool tool.curl curl FIX curl "it probes the local Ollama API"
  pf_tool tool.ffmpeg ffmpeg FIX ffmpeg-free "it captures the audio" \
    "the RPM Fusion ffmpeg package works instead of ffmpeg-free"
  pf_tool tool.pactl pactl FIX pulseaudio-utils "it finds the microphone and the system-audio monitor"
  pf_tool tool.systemctl systemctl FIX systemd "it runs the service as a systemd --user unit"
  pf_tool tool.gdbus gdbus FIX glib2 "it reads the recording state before an update"
  pf_tool tool.gnome-extensions gnome-extensions FIX gnome-shell "it packs, installs and enables the extension"
}

# The review app runs on the system Python (REVIEW_APP_PYTHON), never on the uv environment, so its
# toolkit is probed with that interpreter only.
pf_gi_probe() { # pf_gi_probe NAMESPACE VERSION
  "$REVIEW_APP_PYTHON" -c "import gi; gi.require_version(\"$1\", \"$2\")" >/dev/null 2>&1
}

pf_check_gtk() {
  local py="$REVIEW_APP_PYTHON" gi_ok=0 row id namespace version package label why
  local why_app="the review app runs on the system Python with this toolkit"

  if ! pf_have "$py"; then
    pf_add FIX gtk.pygobject "$py was not found, so PyGObject cannot be used" "$why_app"
    pf_fix "sudo dnf install python3-gobject"
  elif ! "$py" -c 'import gi' >/dev/null 2>&1; then
    pf_add FIX gtk.pygobject "PyGObject (gi) cannot be imported by $py" "$why_app"
    pf_fix "sudo dnf install python3-gobject"
  else
    gi_ok=1
    pf_add OK gtk.pygobject "PyGObject (gi) imports with $py" "$why_app"
  fi

  for row in "gtk.gtk4|Gtk|4.0|gtk4|GTK 4|the review app UI is built on GTK 4" \
    "gtk.libadwaita|Adw|1|libadwaita|libadwaita|the review app is built on libadwaita" \
    "gtk.libsecret|Secret|1|libsecret|libsecret|it stores the cloud API key and the Hugging Face token in the GNOME Keyring"; do
    IFS='|' read -r id namespace version package label why <<<"$row"
    if ((!gi_ok)); then
      pf_add SKIP "$id" "skipped: PyGObject is not available" "$why"
    elif pf_gi_probe "$namespace" "$version"; then
      pf_add OK "$id" "$label ($namespace $version typelib) loads with $py" "$why"
    else
      pf_add FIX "$id" "$label ($namespace/$version typelib) is missing for $py" "$why"
      pf_fix "sudo dnf install $package"
    fi
  done

  if pf_have rpm && rpm -q gstreamer1-plugins-good >/dev/null 2>&1; then
    pf_add OK gtk.gstreamer "gstreamer1-plugins-good is installed" "it plays the voice samples"
  else
    pf_add WARN gtk.gstreamer "gstreamer1-plugins-good is not installed (or rpm cannot tell)" \
      "it plays the voice samples; everything else works without it"
    pf_fix "sudo dnf install gstreamer1-plugins-good"
  fi
}

pf_check_optional_tools() {
  pf_tool tool.secret-tool secret-tool WARN libsecret "optional: it stores a key from a terminal (see the runbook)"
}

# Runs every check, in the order of the documented table. Each group only calls pf_add and its
# modifiers; nothing here writes, runs sudo, prompts or calls a service.
preflight_run() {
  pf_check_session
  pf_check_tools
  pf_check_gtk
  pf_check_optional_tools
}
