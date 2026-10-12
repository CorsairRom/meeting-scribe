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

# Runs every check. The groups are added by the tasks that own them (session and tools, then
# hardware and services); each one only calls pf_add and its modifiers.
preflight_run() {
  :
}
