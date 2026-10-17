// Entry point of the desktop tray: gjs -m desktop-tray/main.js (spec desktop-tray, section 2).
// Exits 0 at once on GNOME Shell (the extension is the client there) or when another tray runs;
// otherwise runs until SIGTERM or SIGINT, then releases everything and exits 0.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GLibUnix from 'gi://GLibUnix';
import System from 'system';

import {GLibScheduler} from '../gnome-extension/meeting-scribe@meetingscribe.dev/lib/glibScheduler.js';
import {Tray} from './lib/tray.js';

const SIGINT = 2;
const SIGTERM = 15;
const NOT_STARTED = {
    'gnome-shell': 'GNOME Shell is running; the extension is the client there',
    'already-running': 'another tray is already running',
};

const logError = message => printerr(`meeting-scribe-tray: ${message}`);

const loop = new GLib.MainLoop(null, false);
const scheduler = new GLibScheduler();
let tray = null;
let stopping = false;

// Idempotent: runs on a signal and once more before the exit.
function stop() {
    stopping = true;
    tray?.destroy();
    scheduler.destroy();
    loop.quit();
    return GLib.SOURCE_REMOVE;
}

// Installed before the start, so a signal during it still ends in a clean exit 0.
GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, SIGTERM, stop);
GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, SIGINT, stop);

let exitCode = 0;
try {
    tray = new Tray({
        connection: Gio.DBus.session,
        scheduler,
        // The install puts the icons in $DATA_HOME/icons/hicolor/scalable/status/.
        iconThemePath: GLib.build_filenamev([GLib.get_user_data_dir(), 'icons']),
        logError,
    });
    const result = await tray.start();
    if (result.started && !stopping)
        await loop.runAsync();
    else if (result.reason in NOT_STARTED)
        logError(`not started: ${NOT_STARTED[result.reason]}`);
} catch (e) {
    logError(`could not start: ${e.message}`);
    exitCode = 1;
}
stop();
System.exit(exitCode);
