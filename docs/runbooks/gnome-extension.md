---
type: runbook
status: active
last_updated: 2026-10-17
phase: F3
verifiable_by:
  - gnome-extension/meeting-scribe@meetingscribe.dev/lib/indicatorModel.js
  - gnome-extension/meeting-scribe@meetingscribe.dev/lib/meetingsClient.js
  - gnome-extension/meeting-scribe@meetingscribe.dev/metadata.json
  - gnome-extension/meeting-scribe@meetingscribe.dev/extension.js
  - Makefile
  - tests/unit/test_extension_pack.py
ai_instructions:
  - "An agent must NEVER run these steps: they install or enable an extension, start a shell, start the service or record real audio. Only the user runs them."
  - "Never touch ~/.local/share/gnome-shell/ or run gnome-extensions install, enable or disable on the user's behalf."
---

# Try and install the GNOME extension

## Purpose

Build the Meeting Scribe panel indicator (F2), try it in a nested GNOME Shell that cannot disturb your session,
and install it in your real session. The extension is a thin D-Bus client of the recording service
(`dev.meetingscribe.Service`); it does no recording itself.

## Privacy warning

> **Choosing "Start recording" in the indicator menu records your real microphone and the system audio** whenever
> the service is reachable on the same session bus, including inside a nested shell. A plain click only opens the menu. Start the service only knowingly,
> tell anyone who can be heard, and never commit or share the output. An AI agent must not run these steps for you.

## Preconditions

- GNOME Shell 50 (`metadata.json` declares `shell-version: ["50"]`) on Wayland; `gjs` and the `gnome-extensions`
  CLI installed.
- For the real session: `./install.sh` (alias `make install`; [`install.md`](install.md)) does everything below (service, unit, extension) in one command. For the nested shell only: `make setup`, so the service can run (`make run`).
- For the nested shell on GNOME 49 and later: the `mutter-devkit` package (Fedora, Arch). Source:
  [gjs.guide, Creating an Extension](https://gjs.guide/extensions/development/creating.html).

## What the indicator shows

Derived from `lib/indicatorModel.js`:

| State | Icon and label | Click | Menu |
|---|---|---|---|
| `IDLE`, `STOPPED` | `●` | Opens the menu | "Start recording" (after `STOPPED`, preceded by "Last recording: `<session id>`") |
| `RECORDING` | `■ HH:MM:SS` (elapsed, ticks every second) | Opens the menu | "Recording in progress", **Stop recording** |
| `FAILED` | `⚠` | Opens the menu | "Recording failed: `<reason>`", **Start recording** |
| Service unavailable | `○`, dimmed | Opens the menu | "The Meeting Scribe service is not running." and how to start it |
| A Start or Stop call in flight | unchanged | Ignored | "Working…" |

Meeting processing (from `dev.meetingscribe.Meetings1`, F3.4b) adds lines under the recording items, after a
separator. It never changes what a click does: every click opens the menu and nothing starts a recording on click.

| Pipeline | Panel | Extra menu lines |
|---|---|---|
| Idle, nothing finished yet | unchanged | none |
| A job runs | label `…` (the `■` timer wins while recording) | "Processing meeting… (transcribing)", "(diarizing)" or "(extracting samples)"; "N meetings queued" when N > 0 |
| Jobs waiting, none running (for example during a recording) | unchanged | "N meetings queued" |
| Last job `DONE` | unchanged | "Last meeting processed: N speakers" ("no speech detected" for 0); no meeting id or content |
| Last job `FAILED` | unchanged | **Processing failed — Reprocess** (calls `Meetings1.Reprocess`; a refusal is shown as a notification) |
| Service unavailable | `○` | none (processing is hidden) |

**Review last meeting** (F4.7) is the last item of the recording group when the state is `IDLE`, `STOPPED` or
`FAILED` (hidden while recording, in an unknown state, with the service unavailable and while a call is in flight). It launches the review app through its desktop
entry (`dev.meetingscribe.Review.desktop`, installed by `./install.sh`), with no argument and no meeting data; the app
picks the newest `DONE` meeting itself, and shows an empty state when there is none. If the launch fails, one
notification says to run `./install.sh`. Steps and troubleshooting: [`review-app.md`](review-app.md).

**Preferences...** (F5.10) ends every menu that can open (also with the service unavailable, where the dialog explains).
It asks the review app to open its Preferences dialog through the `preferences` action of the same desktop entry
(`Exec=<launcher> --preferences`, written by `./install.sh`; an install older than this feature has no action, so the
same notification says to run `./install.sh`). The gear in the Extensions app runs `prefs.js`, which does the same and
then closes its own window; it holds no setting, no D-Bus client and no key. Everything configurable lives in the
review app and the service ([`../specs/preferences.md`](../specs/preferences.md)).

**Open last summary (date)** (F5.7) follows the same visibility as the review item. It shows the start of the most
recent summarized meeting as a readable date and time in your locale (no title, no path) and opens its `summary.md`
with the default app for Markdown files. It is hidden when there is no summary
yet or the service is older. The menu asks the service again each time it opens and after every `SummaryFinished`. If no
app opens it, one notification says to check the app set for Markdown files; fix it with
`xdg-mime default <app>.desktop text/markdown`.

The last result is kept until a job starts or the service restarts; a successful Reprocess forgets a `FAILED` result so
the action cannot fire twice. The service already sends the desktop notification; the extension sends none.

## Build the bundle

```bash
make extension-pack
```

This runs `gnome-extensions pack` and writes
`dist/meeting-scribe@meetingscribe.dev.shell-extension.zip` (`dist/` is gitignored). The bundle holds
`metadata.json`, `extension.js`, `prefs.js`, `stylesheet.css` and `lib/*.js`, and nothing else (checked by
`tests/unit/test_extension_pack.py`).

The Shell-free `lib/` modules are shared with the desktop tray
([ADR-012](../decisions/012-cross-desktop-tray-in-standalone-gjs.md)): after changing them, run `make test-tray` too,
not only `make test-extension`.

## Try it in a nested shell (your session is not touched)

The nested shell runs in a window on its own private session bus. Sources:
[gjs.guide, Creating an Extension](https://gjs.guide/extensions/development/creating.html) and
[Debugging](https://gjs.guide/extensions/development/debugging.html) for the invocation;
[`js/misc/fileUtils.js`](https://gitlab.gnome.org/GNOME/gnome-shell/-/raw/50.0/js/misc/fileUtils.js) for the
extension lookup.

How the shell finds extensions: it scans `gnome-shell/extensions/` under `GLib.get_user_data_dir()` (that is
`$XDG_DATA_HOME`, default `~/.local/share`) and under every system data dir. There is no `--extension-dir` option
(`gnome-shell --help-all` on 50 lists none), so the nested shell gets its own `XDG_DATA_HOME`.

1. Unpack the bundle into a throwaway data dir (plain file operations, nothing is installed):

   ```bash
   make extension-pack
   export NESTED_DATA="$(mktemp -d)"
   mkdir -p "$NESTED_DATA/gnome-shell/extensions/meeting-scribe@meetingscribe.dev"
   unzip -q dist/meeting-scribe@meetingscribe.dev.shell-extension.zip \
     -d "$NESTED_DATA/gnome-shell/extensions/meeting-scribe@meetingscribe.dev"
   ```

2. Start the nested shell. `dbus-run-session` wraps it in a new D-Bus session, so it cannot collide with the host.
   `GSETTINGS_BACKEND=memory` keeps the nested session's settings (including the list of enabled extensions) out of
   your real dconf database; this variable is a GLib feature, not something the gjs.guide pages mention.

   ```bash
   XDG_DATA_HOME="$NESTED_DATA" GSETTINGS_BACKEND=memory \
     dbus-run-session gnome-shell --devkit --wayland
   ```

   On GNOME 48 and earlier the flag is `--nested` instead of `--devkit`. The terminal prints many Shell and Mutter
   debug messages and harmless warnings; your extension's own messages appear there too.

3. Open a terminal inside the nested window and enable the extension (the terminal inherits the nested session's
   environment and bus):

   ```bash
   gnome-extensions enable meeting-scribe@meetingscribe.dev
   ```

   The indicator appears in the nested top bar as `○` (service unavailable).

4. Close the nested window (or Ctrl+C the terminal) and remove the throwaway directory: `rm -r "$NESTED_DATA"`.

### Seeing something other than "service unavailable"

The nested shell has its own session bus, so it cannot see a service running in your real session. To see `●`
and the other states, start the service on the nested bus: in a terminal opened inside the nested window, `cd` to
the repository and run `make run`. **From then on a click on `●` records your real microphone** (see the privacy
warning). Stop with Ctrl+C in that terminal.

Not verified by the author of this runbook: the nested shell was never started while writing it (no shell is run
by agents here); the steps follow the cited documentation. `GSETTINGS_BACKEND=memory` and starting the service from a
terminal inside the nested window are the least-documented parts; if either misbehaves, note it in the backlog.

## Install in your session

Run `./install.sh` (it checks the prerequisites first): it installs and starts the service, then packs, installs and enables the extension. If it
prints "log out and back in", do that once (Wayland cannot restart the shell); the service is already running.

Manual steps, for development of the extension only:

1. Build and install (copies the bundle into `~/.local/share/gnome-shell/extensions/`, does not enable it):

   ```bash
   make extension-install
   ```

2. Log out and back in. A Wayland session cannot restart GNOME Shell while you are logged in
   ([Debugging](https://gjs.guide/extensions/development/debugging.html)); a new shell is needed to discover the
   new extension.

3. Enable it:

   ```bash
   gnome-extensions enable meeting-scribe@meetingscribe.dev
   ```

4. Make sure the service runs (`./install.sh` starts it; `make run` only for development) and look at the top bar.

## Verify

```bash
gnome-extensions info meeting-scribe@meetingscribe.dev
```

Expect `State: ACTIVE` (or `ENABLED`). Press `Alt`+`F2`, type `lg`, open the Extensions tab in Looking Glass to see
errors for the extension. Live shell log:

```bash
journalctl --user -f -o cat /usr/bin/gnome-shell
```

## Rollback

`./uninstall.sh` removes the extension together with the service. To remove only the extension:

```bash
gnome-extensions disable meeting-scribe@meetingscribe.dev
gnome-extensions uninstall meeting-scribe@meetingscribe.dev
```

The extension's code stays cached by the running shell until the next login.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Indicator is `○`, "service is not running" | The service does not own `dev.meetingscribe.Service` on this session bus. Run `./install.sh` (or `systemctl --user start meeting-scribe.service`); `make run` is for development. The extension never launches the service itself. In a nested shell, see "Seeing something other than service unavailable". |
| After starting a recording: "No default microphone found" | `AUDIO_DEVICE_NOT_FOUND`: no default microphone, or the default source is a monitor. Pick a real input with `pactl set-default-source <name>`. |
| Indicator missing from the top bar | `gnome-extensions info meeting-scribe@meetingscribe.dev` and Looking Glass (`lg`) show state and error. `OUT OF DATE` means `metadata.json` does not list your shell major version (it lists only 50). |
| `State: ERROR` | Read the error in Looking Glass or in the journal command above; fix, re-run `make extension-install` and log out and in (cached code). |
| Extension not listed after install | The shell has not rescanned: log out and in. |
| `mutter-devkit` missing, `--devkit` fails | Install `mutter-devkit` (GNOME 49 and later). |
