---
type: runbook
status: active
last_updated: 2026-10-17
phase: F6.5
verifiable_by:
  - desktop-tray/main.js
  - desktop-tray/lib/tray.js
  - desktop-tray/lib/trayModel.js
  - desktop-tray/lib/statusNotifierItem.js
  - desktop-tray/lib/dbusMenu.js
  - desktop-tray/icons/
  - desktop-tray/tests/
  - Makefile
ai_instructions:
  - "An agent must NEVER start the tray, the service or a recording on the user's behalf: the tray talks to the real session bus. Only the user runs those steps; `make test-tray` is safe (private dbus-daemon, fake service)."
  - "Stop a tray by its PID (GetConnectionUnixProcessID), never with pkill -f."
---

# Run and troubleshoot the desktop tray

## Purpose

Use Meeting Scribe from a tray icon on KDE Plasma and other desktops that are not GNOME Shell (F6.5,
[spec](../specs/desktop-tray.md), [ADR-012](../decisions/012-cross-desktop-tray-in-standalone-gjs.md)). The tray is a
standalone `gjs -m` process that shows the GNOME extension's menu through two standard D-Bus interfaces
(`org.kde.StatusNotifierItem` and `com.canonical.dbusmenu`). Like the extension, it is a thin D-Bus client of the
service (`dev.meetingscribe.Service`): it does no recording itself and never starts the service.

## Privacy warning

> **Choosing "Start recording" in the tray menu records your real microphone and the system audio** whenever the
> service is reachable on the same session bus. A click on the icon only opens the menu. Tell anyone who can be heard,
> and never commit or share the output. An AI agent must not run the tray or the service for you.

## Preconditions

- A desktop that is not GNOME Shell and has a StatusNotifierItem host (a process that owns
  `org.kde.StatusNotifierWatcher` on the session bus): KDE Plasma 6 has one built in; Xfce, Cinnamon, LXQt and waybar
  show the icon once their tray or status notifier applet is enabled.
- `gjs` with GLib 2.80 or later (`sudo dnf install gjs`): the tray imports the `GioUnix` and `GLibUnix` namespaces,
  which GLib 2.80 introduced.
- The service installed and running (`./install.sh`, see [`install.md`](install.md)).

## When the tray runs

- `./install.sh` decides the desktop from the session bus: when `org.gnome.Shell` owns a name it installs the GNOME
  extension, otherwise it installs the tray. On the tray path it copies `desktop-tray/` and the extension's shared
  `lib/` into the version directory, installs the five icons into
  `${XDG_DATA_HOME:-~/.local/share}/icons/hicolor/scalable/status/`, writes the autostart entry
  `~/.config/autostart/dev.meetingscribe.Tray.desktop` (it runs `<current>/bin/meeting-scribe-tray`), and restarts the
  tray in the current session (the old one is stopped by PID). `./uninstall.sh` stops the tray by PID and removes the
  autostart entry and the icons. Procedure, exit codes and the preflight checks: [`install.md`](install.md).
- On GNOME Shell the tray exits 0 at once, because the extension is the client there. This also covers a machine with
  both a GNOME and a KDE session: the autostarted tray does nothing in the GNOME one.
- One tray per session: the tray owns the bus name `dev.meetingscribe.Tray` and a second one exits 0 at once.
- With no tray host it logs one line and waits; the icon appears when a host starts. When the host restarts (for
  example after `plasmashell` crashes) the tray registers its icon again by itself.
- SIGTERM or SIGINT (Ctrl+C) ends it cleanly with exit 0.

## What the tray shows

**Icon.** Five colored SVG icons in `desktop-tray/icons/`, mapped from the extension's view model by
`lib/trayModel.js`:

| State | Extension glyph | Tray icon | Look |
|---|---|---|---|
| Idle, stopped | `●` | `dev.meetingscribe.Tray-idle` | filled dot, neutral gray |
| A meeting is being processed (not recording) | `●` with `…` | `dev.meetingscribe.Tray-processing` | filled dot with a small ellipsis, neutral gray |
| Recording | `■ HH:MM:SS` | `dev.meetingscribe.Tray-recording` | filled square, red `#e01b24`, steady (no pulse) |
| Recording failed | `⚠` | `dev.meetingscribe.Tray-failed` | warning triangle, amber `#e5a50a` |
| Service unavailable, unknown state | `○` | `dev.meetingscribe.Tray-unavailable` | hollow circle, gray at half opacity |

The red icon is how you see that a recording is running. The icon names do not end in `-symbolic`, because Plasma
recolors symbolic icons to the theme color and the red would be lost. The item's `Status` is always `Active`
(`NeedsAttention` would make Plasma pulse it endlessly).

**No text next to the icon on Plasma.** The Plasma 6 system tray draws every item in a fixed square cell and never
reads a text label (`XAyatanaLabel`), so the extension's `■ 00:12:34` cannot sit next to the icon (ADR-012). The
elapsed time moves to two places instead, in the extension's own format:

- the tooltip: `Recording in progress · 00:12:34`;
- the first menu row: `Recording in progress (<session id>) · 00:12:34`.

Both change every second; an open menu stays open and does not jump.

**Menu.** The same items, order and texts as the extension's menu, from the same shared view model and controller:
the recording status row, **Start recording** or **Stop recording**, "Working…" while a call is in flight, the
processing lines (stage, queue length, the last result), **Processing failed — Reprocess**, **Review last meeting**,
**Open last summary (date)** and **Preferences…**. What each item does, and when it is hidden, is described in
[`gnome-extension.md`](gnome-extension.md), "What the indicator shows". Errors are shown as desktop notifications
(`org.freedesktop.Notifications`, app name "Meeting Scribe") with the extension's texts. Opening the menu asks the
service for the last summary again, as the extension does.

**A click never records.** A click on the icon only opens the menu (the item sets `ItemIsMenu`); the item's
`Activate`, `SecondaryActivate`, `ContextMenu` and `Scroll` methods do nothing. Start and Stop are explicit menu
actions (CR-014).

## Run it from the repository (development)

Only on the user's explicit go: this tray talks to the real session bus, and **Start recording** in its menu records
real audio. It never starts a recording or the service by itself.

1. Stop the installed tray, if one runs (a second tray exits at once). Find its PID from the bus and stop it by PID:

   ```bash
   gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
     --method org.freedesktop.DBus.GetConnectionUnixProcessID dev.meetingscribe.Tray
   kill <pid>
   ```

   The first command prints `(uint32 <pid>,)`, or an error when no tray runs.

2. From the repository root, in a terminal of the desktop session:

   ```bash
   gjs -m desktop-tray/main.js
   ```

   It imports the extension's shared modules by relative path, so it runs from the working tree without installing.
   Its icon names resolve only when the icons are installed (a previous `./install.sh`); otherwise the host shows its
   fallback icon. On GNOME Shell it prints `meeting-scribe-tray: not started: GNOME Shell is running; the extension is
   the client there` and exits 0.

3. Stop it with Ctrl+C. To get the installed tray back, log out and back in, or run `./install.sh` again.

## Test

```bash
make test-tray
```

It runs `gjs -m desktop-tray/tests/run.js`, and `make check` includes it. The D-Bus tests run on a private
`dbus-daemon` (the extension's `gnome-extension/tests/privateBus.js`) with a fake service, a fake
`org.kde.StatusNotifierWatcher` and a fake `org.freedesktop.Notifications`; they never touch your session bus, never
open an audio device and never start a real recording. `main.js` is also run as a child process whose session bus is
the private one.

## Logs

The tray writes one line per problem to stderr, each starting with `meeting-scribe-tray:`, and never writes meeting
content (a failed notification is logged without its text). From a terminal the lines appear there. When the desktop
starts the tray from the autostart entry, they go where the desktop sends a started program's output; on a
systemd-managed session such as Plasma 6 that is the user journal:

```bash
journalctl --user -b -g meeting-scribe-tray
```

| Line | Meaning |
|---|---|
| `not started: GNOME Shell is running; the extension is the client there` | Expected on GNOME Shell; exit 0 |
| `not started: another tray is already running` | `dev.meetingscribe.Tray` is owned; exit 0 |
| `no org.kde.StatusNotifierWatcher on the session bus; waiting for a tray host to show the icon` | No tray host yet; the tray waits |
| `could not register with org.kde.StatusNotifierWatcher: <error>` | The host refused or timed out; the tray retries every 2 s while the same host runs, and logs this once per host |
| `could not own the bus name org.kde.StatusNotifierItem-<pid>-1; the tray icon will not show` | Another connection holds the item's name |
| `could not show a notification: <error>` | No notification server answered |
| `could not start: <error>` | The start failed, for example the bus daemon could not be asked; exit 1 |

Not verified on a real Plasma session yet (the owner check on Fedora KDE is task F6.5.6); the journal location is the
part most likely to differ between desktops.

## Verify

Read-only questions to the bus daemon and the tray host:

```bash
# The tray runs: prints (true,)
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.NameHasOwner dev.meetingscribe.Tray
# The host knows the item: the list holds an entry for org.kde.StatusNotifierItem-<pid>-1
gdbus call --session --dest org.kde.StatusNotifierWatcher --object-path /StatusNotifierWatcher \
  --method org.freedesktop.DBus.Properties.Get org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems
```

## Rollback

`./uninstall.sh` stops the tray by PID and removes the autostart entry and the icons together with the service, and
keeps your data ([`install.md`](install.md)). To stop only the tray for this session, stop it by PID as in "Run it
from the repository", step 1; the autostart entry starts it again at the next login.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| No icon at all | The desktop has no tray host: `./install.sh --check` shows `session.tray-host` as WARN. Enable the desktop's tray or status notifier applet (Xfce: the panel's Status Tray plugin; LXQt: the Status Notifier widget; waybar: the `tray` module); the running tray registers as soon as the host appears. On Plasma, see the next row. |
| Plasma shows the icon only in the tray's expand popup | Plasma's System Tray settings decide which entries stay visible: open them from the tray's context menu, Entries, and set Meeting Scribe to Always shown. |
| Icon gone after Plasma restarts | The tray registers again when the new `plasmashell` owns `org.kde.StatusNotifierWatcher`; a busy host is retried every 2 s. If it stays gone, read the log lines above. |
| The tray does not start | `gjs` is missing (`./install.sh --check` reports `tool.gjs` FIX: `sudo dnf install gjs`), or GLib is older than 2.80 (gjs itself reports that the `GioUnix` or `GLibUnix` typelib is not found, before any `meeting-scribe-tray:` line). A `not started` line is expected on GNOME Shell and when another tray runs. |
| Icon is a hollow gray circle, menu says "The Meeting Scribe service is not running." | The service does not own `dev.meetingscribe.Service` on this session bus. Run `systemctl --user start meeting-scribe.service` or `./install.sh`; the tray never starts the service itself. |
| Text next to the icon is missing | Expected on Plasma 6: the tray cannot show text there. The elapsed time is in the tooltip and the first menu row. |
| "Review last meeting" or "Preferences…" shows "Could not open the review app. Run `make install` to install it." | The desktop entry `dev.meetingscribe.Review.desktop` (and its `preferences` action) is missing or older than the feature. Run `./install.sh`. Details: [`review-app.md`](review-app.md). |
| "Open last summary" shows "Could not open the summary. Check that an app is set for Markdown files." | No default app for Markdown: `xdg-mime default <app>.desktop text/markdown`. |
| No notification for an error | No notification server answered; the log has `could not show a notification`. |
