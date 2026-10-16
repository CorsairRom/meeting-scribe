---
type: spec
feature: desktop-tray
status: approved  # draft | approved | in-progress | done | blocked
mvp: false     # v1.1 phase F6.5
prd_ref: FR-1, FR-2, FR-5, FR-6, FR-7, safety requirement 1
depends_on:
  - decisions/001-thin-gnome-extension-dbus-service.md (thin clients over the D-Bus session bus)
  - decisions/010-install-never-uses-sudo.md (the preflight prints fixes, never runs them)
  - decisions/012-cross-desktop-tray-in-standalone-gjs.md (proposed with this spec)
  - specs/install.md (preflight checks, install layout, update and uninstall)
  - architecture/api-contracts.md (Recording1, Meetings1 and Summaries1, consumed unchanged)
verifiable_by:
  - desktop-tray/tests/
  - gnome-extension/tests/
  - tests/unit/test_preflight.py
  - tests/unit/test_install_scripts.py
last_updated: 2026-10-16
ai_instructions:
  - "This spec is a contract. Do NOT implement anything that is not in it."
  - "If you need to change the spec, update this doc FIRST and ask for confirmation."
  - "Every acceptance criterion must have a test that verifies it."
  - "The tray reuses the extension's Shell-free modules unchanged; never copy them into desktop-tray/."
---

# Spec: Desktop tray

## Goal (one sentence)

Let the user do everything the GNOME extension offers from a tray icon on KDE Plasma and other desktops, without
GNOME Shell.

---

## Background

- The owner's specs S1-S6 are in `odd/tasks/meeting-scribe.md` (F6.5); the decision and the options are in ADR-012.
- The Plasma 6 tray shows one square icon per item and never shows text next to it (ADR-012, Context). So the
  recording state is a red icon, and the elapsed time moves to the menu and the tooltip.
- The service, the review app, the keyring and the D-Bus API do not change.

---

## Users / Personas

| Persona | How they use the feature |
|---|---|
| A tester on Fedora KDE (Plasma 6) | Installs with `./install.sh`, records from the tray, opens the review app and the last summary |
| The owner on Fedora GNOME | Nothing changes: the extension stays in the top bar |
| A user on Xfce, Cinnamon, LXQt or waybar | Gets the same tray if the desktop has a StatusNotifierItem host |

---

## User stories

- **US-1**: As a KDE user, I want to start and stop a recording from a tray menu, so that I do not need GNOME.
- **US-2**: As a KDE user, I want the tray icon to turn red while recording, so that I do not forget to stop it.
- **US-3**: As a KDE user, I want the menu to show the elapsed time, the processing progress and the last meeting,
  and to open the review app, the last summary and Preferences, as the GNOME menu does.
- **US-4**: As a KDE user, I want `./install.sh` to work on my desktop and to tell me exactly what is missing.

---

## Design

### 1. Code layout and reuse

| Path | Role |
|---|---|
| `gnome-extension/meeting-scribe@meetingscribe.dev/lib/indicatorModel.js`, `indicatorController.js`, `recordingClient.js`, `meetingsClient.js`, `glibScheduler.js`, `preferencesLauncher.js` | Shared, unchanged: the view model, the controller, the D-Bus clients, the scheduler |
| `desktop-tray/main.js` | Entry point (`gjs -m`): main loop, single instance, wiring, shutdown on SIGTERM/SIGINT |
| `desktop-tray/lib/statusNotifierItem.js` | Exports `org.kde.StatusNotifierItem` and registers with the watcher |
| `desktop-tray/lib/dbusMenu.js` | Exports `com.canonical.dbusmenu` from a list of menu items |
| `desktop-tray/lib/trayModel.js` | Pure: turns the extension's view model into the tray's icon, tooltip and menu (no imports) |
| `desktop-tray/lib/trayView.js` | The `view` the controller renders into: applies `trayModel` to the item and the menu |
| `desktop-tray/icons/` | Five colored SVG status icons (section 4) |
| `desktop-tray/tests/` | Tests, reusing `gnome-extension/tests/harness.js` and `privateBus.js` |

The tray imports the shared modules by relative path (`../gnome-extension/meeting-scribe@meetingscribe.dev/lib/`).
The install copies `desktop-tray/` and that `lib/` directory into `versions/<id>/` with the same relative layout,
so the same imports work from the repository and from the install. No tray file goes under the extension directory,
because every file there ships in the extension zip (`tests/unit/test_extension_pack.py`).

### 2. The tray process

- Started as `gjs -m <current>/desktop-tray/main.js` through the launcher `<current>/bin/meeting-scribe-tray`.
- If `org.gnome.Shell` owns a name on the session bus, it exits 0 at once: on GNOME Shell the extension is the
  client (this also covers a user who has both a GNOME and a KDE session).
- Single instance: it requests the bus name `dev.meetingscribe.Tray` with `DO_NOT_QUEUE`; if the name is taken, it
  exits 0.
- It never starts the service: the clients keep `DO_NOT_AUTO_START` and show the service-unavailable state, as the
  extension does.
- It registers its item with `org.kde.StatusNotifierWatcher` (`RegisterStatusNotifierItem`) and registers again
  whenever the watcher name gets a new owner (for example after `plasmashell` restarts). With no watcher it logs one
  line to stderr and waits.
- On SIGTERM or SIGINT it calls `controller.destroy()`, destroys the clients, unexports its objects and exits 0.

### 3. The StatusNotifierItem

Object path `/StatusNotifierItem`, interface `org.kde.StatusNotifierItem`:

| Property | Value |
|---|---|
| `Category` | `ApplicationStatus` |
| `Id` | `meeting-scribe` |
| `Title` | `Meeting Scribe` |
| `Status` | `Active` (always: the item stays visible in the panel; `NeedsAttention` is never used, because Plasma pulses it endlessly) |
| `IconName` | the status icon of section 4 |
| `IconThemePath` | the installed icon directory |
| `ToolTip` | `("", [], "Meeting Scribe", <tooltip text of section 5>)` |
| `ItemIsMenu` | `true` (a click opens the menu) |
| `Menu` | `/MenuBar` |

Methods: `Activate`, `SecondaryActivate`, `ContextMenu` and `Scroll` do nothing. A click never starts or stops a
recording (CR-013; new CR-014 records it for the tray). Signals: `NewIcon`, `NewToolTip` and `NewStatus`, emitted
only when the value changed.

### 4. Status icons

The extension shows a glyph colored by CSS. The tray maps the same glyph to an SVG icon with the same color. The
names do not end in `-symbolic`, because Plasma recolors symbolic icons to the theme color and the red would be
lost.

| View model `icon` | Panel label | Icon name | Look |
|---|---|---|---|
| `●` (idle) | empty | `dev.meetingscribe.Tray-idle` | filled dot, neutral gray |
| `●` (idle) | `…` (processing) | `dev.meetingscribe.Tray-processing` | filled dot with a small ellipsis, neutral gray |
| `■` (recording) | elapsed time | `dev.meetingscribe.Tray-recording` | filled square, red `#e01b24` |
| `⚠` (failed) | empty | `dev.meetingscribe.Tray-failed` | warning triangle, amber `#e5a50a` |
| `○` (service unavailable) | empty | `dev.meetingscribe.Tray-unavailable` | hollow circle, gray at half opacity |

### 5. Tooltip and menu

`trayModel.js` takes the extension's view model `{icon, label, menuItems, tooltip, dimmed}` and returns
`{iconName, tooltip, menuItems}`:

- **Tooltip**: the view model's `tooltip`; while recording it ends with the elapsed time, for example
  `Recording in progress · 12:34`.
- **Menu**: the view model's `menuItems` in the same order, with the same texts. While recording, the first status
  row ends with the elapsed time (`Recording in progress (…) · 12:34`). The elapsed time uses the extension's
  `formatElapsed`, so it reads the same as the GNOME label.
- Every second while recording the controller renders again. The tray then updates only the tooltip and that row's
  label (`NewToolTip` and `ItemsPropertiesUpdated`), so an open menu does not close or jump.

`dbusMenu.js` maps the three item kinds:

| Kind | dbusmenu item |
|---|---|
| `status` | `label`, `enabled=false` |
| `action` | `label`, `enabled=true`; the `clicked` event calls `controller.onMenuAction(action)` |
| `separator` | `type=separator` |

- Item ids stay stable while the list of kinds and actions is unchanged. A change in that list increments the layout
  revision and emits `LayoutUpdated`.
- `AboutToShow` on the root calls `controller.onMenuOpened()`, which refreshes the last summary, as the extension
  does when its menu opens.
- Underscores in labels are escaped (`__`), because dbusmenu reads `_` as a mnemonic.

### 6. Glue (the same as `extension.js`, without GNOME Shell)

| Controller option | Tray |
|---|---|
| `scheduler` | `GLibScheduler` (shared) on the tray's own `GLib.MainLoop` |
| `launchReview` | `Gio.DesktopAppInfo.new('dev.meetingscribe.Review.desktop')?.launch([], null)` |
| `launchPreferences` | `launchPreferencesAction(id => Gio.DesktopAppInfo.new(id))` (shared) |
| `openSummary` | `Gio.AppInfo.launch_default_for_uri_async` on the summary file URI |
| `notify(title, body)` | `org.freedesktop.Notifications.Notify` (app name `Meeting Scribe`, no actions, default timeout) |
| `_` | identity (the extension ships no translations either) |

### 7. Install, update, uninstall and preflight

**Desktop detection**: the GNOME path applies when `org.gnome.Shell` owns a name on the session bus; otherwise the
tray path applies. The same rule is used by the preflight, the install and the tray process.

**Preflight** (`scripts/preflight.sh`, check ids pinned in `tests/unit/test_preflight.py` and `specs/install.md`
section 4):

| Check id | GNOME path | Tray path |
|---|---|---|
| `session.desktop` (new) | OK "GNOME Shell: the extension will be installed" | OK "<XDG_CURRENT_DESKTOP>: the tray will be installed" |
| `session.wayland` | unchanged (HUMAN when not Wayland) | OK on Wayland; WARN on X11 ("not tested") |
| `session.gnome-shell` | unchanged | SKIP "not a GNOME Shell session" |
| `tool.gnome-extensions` | unchanged | SKIP "not a GNOME Shell session" |
| `tool.gjs` (new) | OK (GNOME Shell ships gjs) | FIX when missing: `sudo dnf install gjs` |
| `session.tray-host` (new) | SKIP | OK when `org.kde.StatusNotifierWatcher` has an owner; WARN otherwise ("the desktop shows no tray icons; enable its status notifier or tray applet") |

The GNOME-only wording of `session.bus` ("inside the GNOME session") and `gtk.libsecret` ("GNOME Keyring") becomes
neutral ("inside your desktop session", "the keyring through the Secret Service").

**Install, tray path**:

1. Copies `desktop-tray/` and the shared `lib/` into `versions/<id>/` (section 1) and writes the launcher
   `bin/meeting-scribe-tray`.
2. Installs the icons into `$DATA_HOME/icons/hicolor/scalable/status/`.
3. Writes `~/.config/autostart/dev.meetingscribe.Tray.desktop` with `Exec=<current>/bin/meeting-scribe-tray`, so
   the tray starts at every login.
4. Restarts the tray in the current session: stops the running one by PID (from
   `org.freedesktop.DBus.GetConnectionUnixProcessID` for `dev.meetingscribe.Tray`, never `pkill -f`) and starts the
   new one detached.
5. Skips every `gnome-extensions` step; the summary prints `tray: running` instead of `extension: enabled`.

**Update** (`git pull && ./install.sh`) repeats the same steps and is idempotent. **Uninstall** stops the tray by
PID and removes the autostart entry and the icons; it keeps user data, as today.

### 8. Tests

- `make test-tray` runs `gjs -m desktop-tray/tests/run.js`; `make check` includes it.
- The D-Bus tests run against the private `dbus-daemon` of `gnome-extension/tests/privateBus.js`, with a fake
  `org.kde.StatusNotifierWatcher`, a fake `org.freedesktop.Notifications` and the existing fake service.
- `trayModel.js` is tested as a pure function for every state of `viewModel`.
- No test opens an audio device or starts a real recording: the fake service only changes its state.

---

## Acceptance criteria (Gherkin to tests)

### AC-1: The tray registers and stays visible

```gherkin
GIVEN a session bus with a StatusNotifierWatcher and the service available and idle
WHEN the tray starts
THEN it registers one item with the watcher
AND the item has Status "Active", ItemIsMenu true, IconName "dev.meetingscribe.Tray-idle"
```

**Test**: `desktop-tray/tests/statusNotifierItem.test.js`

### AC-2: The menu matches the extension's menu

```gherkin
GIVEN the service in each state (idle, recording, processing, failed, unavailable, last meeting processed)
WHEN the host calls GetLayout on /MenuBar
THEN the items have the same texts, order and kinds as the extension's viewModel for that state
```

**Test**: `desktop-tray/tests/dbusMenu.test.js`, `desktop-tray/tests/trayModel.test.js`

### AC-3: Start and Stop are explicit menu actions; a click never records

```gherkin
GIVEN the service idle
WHEN the host calls Activate, SecondaryActivate or ContextMenu on the item
THEN no Start call reaches the service
WHEN the host sends "clicked" for the "Start recording" item
THEN exactly one Start call reaches the service
```

**Test**: `desktop-tray/tests/tray.test.js`

### AC-4: The icon turns red and the elapsed time is in the menu and tooltip

```gherkin
GIVEN the service recording for 754 seconds
WHEN the tray renders
THEN IconName is "dev.meetingscribe.Tray-recording"
AND the tooltip and the first menu row end with "12:34"
WHEN one second passes
THEN the tooltip and that row end with "12:35" and the layout revision is unchanged
```

**Test**: `desktop-tray/tests/tray.test.js`

### AC-5: Review, summary and Preferences open as from the extension

```gherkin
GIVEN a last meeting with a summary
WHEN the host sends "clicked" for "Review last meeting", "Open last summary (<date>)" and "Preferences…"
THEN the tray calls launchReview, openSummary with the summary path, and launchPreferences
AND a failed launch shows the extension's fixed failure text as a notification
```

**Test**: `desktop-tray/tests/tray.test.js`

### AC-6: Errors and the unavailable service are reported as the extension does

```gherkin
GIVEN a Start call that fails with RECORDING_ALREADY_ACTIVE
THEN a notification "Meeting Scribe" with the extension's errorMessage text is sent
GIVEN the service name has no owner
THEN IconName is "dev.meetingscribe.Tray-unavailable" and the menu shows the extension's unavailable rows
```

**Test**: `desktop-tray/tests/tray.test.js`

### AC-7: One tray, and none on GNOME Shell

```gherkin
GIVEN dev.meetingscribe.Tray already owned
WHEN a second tray starts
THEN it exits 0 without registering an item
GIVEN org.gnome.Shell owned on the bus
WHEN the tray starts
THEN it exits 0 without registering an item
```

**Test**: `desktop-tray/tests/main.test.js`

### AC-8: The watcher restarts

```gherkin
GIVEN a registered tray
WHEN the watcher name loses its owner and a new watcher appears
THEN the tray registers its item with the new watcher
```

**Test**: `desktop-tray/tests/statusNotifierItem.test.js`

### AC-9: The preflight on a non-GNOME desktop

```gherkin
GIVEN a session without org.gnome.Shell, without gnome-shell and gnome-extensions, with gjs and a tray host
WHEN I run ./install.sh --check
THEN session.desktop is OK, session.gnome-shell and tool.gnome-extensions are SKIP, tool.gjs and session.tray-host are OK
AND the exit code is 0
GIVEN the same session without gjs
THEN tool.gjs is FIX with "sudo dnf install gjs" and the install stops with zero changes
```

**Test**: `tests/unit/test_preflight.py`

### AC-10: Install, update and uninstall on the tray path

```gherkin
GIVEN the tray path
WHEN I run ./install.sh twice
THEN versions/<id>/ holds desktop-tray/ and the shared lib/, the autostart entry and icons exist, no gnome-extensions command runs
AND the previous tray process is stopped by PID and a new one is started
WHEN I run ./uninstall.sh
THEN the tray is stopped by PID and the autostart entry and icons are removed, and user data is kept
```

**Test**: `tests/unit/test_install_scripts.py`

### AC-11: GNOME does not change

```gherkin
GIVEN org.gnome.Shell owned on the bus
WHEN I run ./install.sh
THEN the extension is packed, installed and enabled as before, and no tray file, autostart entry or icon is installed
```

**Test**: `tests/unit/test_install_scripts.py` (existing GNOME cases keep passing), `tests/unit/test_preflight.py`

---

## What this is NOT (out of scope for this spec)

- Not a counter next to the tray icon, and not a Plasma panel widget (ADR-012, Option A).
- Not distribution packages (dnf, apt) or a KDE Store or extensions.gnome.org upload.
- Not translations (the extension ships none either).
- Not a change to the service, the D-Bus API, the review app or the keyring.
- Not the tray on GNOME Shell, with or without the AppIndicator extension.
- Not support for distributions other than Fedora in the printed fixes (as `specs/install.md`).

---

## Edge cases

| Case | Expected behavior |
|---|---|
| Plasma restarts (`plasmashell` crash) | The tray registers again with the new watcher (AC-8) |
| No tray host on the desktop | Preflight WARN `session.tray-host`; the tray runs, logs one line and waits for a watcher |
| GNOME and KDE sessions on one machine, installed from KDE | In GNOME the autostarted tray sees `org.gnome.Shell` and exits 0 |
| Service not running | Unavailable icon and rows; the tray never starts the service |
| Menu open during a recording | Only the elapsed-time row changes each second; the menu stays open |
| `gjs` removed after install | The tray does not start; `./install.sh --check` reports `tool.gjs` FIX |
| A label with `_` (for example a date) | Escaped as `__` so it is not read as a mnemonic |
| X11 session on KDE | Preflight WARN `session.wayland` ("not tested"); the install continues |

---

## Work units (pull requests to `feat/desktop-tray`)

| # | Unit | Tasks | Size estimate |
|---|---|---|---|
| 1 | This spec and ADR-012 | F6.5.1 | docs |
| 2 | `dbusMenu.js` with tests | F6.5.2 | ~300 lines |
| 3 | `statusNotifierItem.js` with the fake watcher and tests | F6.5.2 | ~300 lines |
| 4 | `trayModel.js`, `trayView.js`, icons, `main.js`, `make test-tray`, tests | F6.5.3 | ~400 lines |
| 5 | Preflight: desktop detection and the new checks | F6.5.4 | ~300 lines |
| 6 | Install, update and uninstall on the tray path | F6.5.4 | ~350 lines |
| 7 | Runbook `docs/runbooks/desktop-tray.md`, ARCHITECTURE, rules registry (CR-014, GO-069), api-contracts (the `dev.meetingscribe.Tray` name), README prerequisites | F6.5.5 | docs |
| 8 | Docs gate and the owner check on Fedora KDE | F6.5.6 | docs |

---

## Definition of Done

- [ ] All acceptance criteria pass in `make check` (including `make test-tray`)
- [ ] Unit tests written first (RED), then implementation (GREEN)
- [ ] Lint, format, type check and import contracts clean
- [ ] Traceability updated in `project/mvp-scope.md`
- [ ] CR-014 and GO-069 added to `project/rules-registry.md`
- [ ] Owner check on Fedora KDE: install, record with the red icon, stop, process, open the review app, the last summary and Preferences from the tray

---

## Decisions taken during implementation

- 2026-10-16: the recording state is a red icon with the elapsed time in the menu and the tooltip, not a counter next
  to the icon — the Plasma 6 tray cannot show text (owner decision, ADR-012).
