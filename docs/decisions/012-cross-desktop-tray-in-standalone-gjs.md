---
type: decision
adr_number: 012
status: active  # proposed | active | superseded | deprecated
date: 2026-10-16
deciders: [richard, ai-assistant]
supersedes: null
superseded_by: null
ai_instructions:
  - "If this decision changes, do NOT edit this ADR. Create a new ADR and set superseded_by."
  - "Active since the owner approved docs/specs/desktop-tray.md on 2026-10-16."
  - "The tray reuses the extension's Shell-free modules; never fork them into a second copy."
---

# ADR-012: A cross-desktop tray in standalone GJS that reuses the extension's modules

## Status

`active` (approved 2026-10-16) - drafted with [`../specs/desktop-tray.md`](../specs/desktop-tray.md) on 2026-10-16,
from the owner's decisions of the same day.

## Context

The only GNOME-specific part of meeting-scribe is the GNOME Shell extension (ADR-001): the service runs under
`systemd --user` on the D-Bus session bus, capture uses `ffmpeg -f pulse`, notifications use
`org.freedesktop.Notifications`, secrets use the Secret Service, and the review app is GTK 4. A tester runs Fedora
KDE (Plasma 6), where the extension cannot load, so nothing can start a recording from the desktop.

Research of 2026-10-16, read in the upstream sources:

- The Plasma 6 system tray never reads the `XAyatanaLabel` property of a StatusNotifierItem
  (`plasma-workspace` `applets/systemtray/statusnotifieritemsource.cpp`), and every tray cell is a fixed square
  (`applets/systemtray/qml/main.qml`): a tray icon cannot show text such as "12:34" next to it, and a wide pixmap is
  scaled down until text is unreadable.
- A pure-QML Plasma panel widget can show an icon and a ticking label (KDE's own Timer widget does) and talk to a
  session-bus service through `org.kde.plasma.workspace.dbus` (Plasma 6.4 or later), but it is KDE-only code and
  the user must add it to the panel by hand.
- The extension's view model, controller, D-Bus clients and scheduler import only `gi://Gio` and `gi://GLib`; only
  `extension.js` and `lib/panelIndicator.js` use GNOME Shell.

## Options Considered

### Option A: A Plasma panel widget (QML)

**Pros**: the same look as GNOME, with a live counter in the panel.
**Cons**: a second client written for one desktop only; the user adds it to the panel by hand; needs Plasma 6.4;
no public QML API to launch a desktop-file action.
**Cost**: a new QML client, its tests and its packaging.

### Option B: A tray in Python (dbus-fast)

**Pros**: the same language as the service.
**Cons**: the view model and controller would be rewritten, so two copies of the menu logic and its strings.
**Cost**: the full client logic again.

### Option C: A tray in standalone GJS that reuses the extension's modules (chosen)

**Description**: a `gjs -m` process exports a StatusNotifierItem and a `com.canonical.dbusmenu` menu, and drives
them with the extension's unchanged view model, controller, clients and scheduler. Only the view is new.
**Pros**: one source of truth for states, menu items and strings; one client for KDE Plasma, Xfce, Cinnamon,
LXQt and waybar; tested with the existing `gjs` harness and private `dbus-daemon`.
**Cons**: no text next to the icon on Plasma (the elapsed time moves to the menu and the tooltip); `gjs` must be
installed outside GNOME (on Fedora KDE `dnf install gjs` adds about 8 MB).
**Cost**: an exporter for two standard D-Bus interfaces, a thin view and the install changes.

### Option D: A Qt tray (PySide6)

**Pros**: a mature tray API.
**Cons**: a large new dependency and a second copy of the client logic.
**Cost**: highest.

## Decision

Option C. The owner accepted on 2026-10-16 that the recording state is shown by a red icon, with the elapsed time
in the menu and the tooltip, instead of a counter next to the icon.

## Consequences

- GNOME keeps the extension unchanged; the install puts the tray on every other desktop.
- The tray never starts a recording on a click: Start and Stop stay explicit menu actions (CR-013 applies).
- The extension's Shell-free modules become shared code: a change to them is tested by both clients.
- The preflight requires `gjs` outside GNOME and reports a missing tray host as a warning.

## Revisit when

- Users on Plasma ask for the counter next to the icon: add the Plasma widget of Option A as an optional client.
- Distribution packages (dnf, apt) are designed: the tray ships in them.
