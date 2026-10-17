import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GioUnix from 'gi://GioUnix';

import {IndicatorController} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/indicatorController.js';
import {MeetingsClient} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/meetingsClient.js';
import {
    REVIEW_DESKTOP_ENTRY, launchPreferencesAction,
} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/preferencesLauncher.js';
import {RecordingClient} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/recordingClient.js';
import {DBusMenu} from './dbusMenu.js';
import {Notifier} from './notifier.js';
import {StatusNotifierItem} from './statusNotifierItem.js';
import {TrayView} from './trayView.js';

export const SERVICE_BUS_NAME = 'dev.meetingscribe.Service';
export const SERVICE_OBJECT_PATH = '/dev/meetingscribe/Service';
/** Owned by the running tray: one tray per session. */
export const TRAY_BUS_NAME = 'dev.meetingscribe.Tray';
/** Owned by GNOME Shell, where the extension is the client and the tray never starts. */
export const GNOME_SHELL_BUS_NAME = 'org.gnome.Shell';

const NAME_CALL_TIMEOUT_MS = 5000;

Gio._promisify(Gio.DBusConnection.prototype, 'call', 'call_finish');

// Installed by the install (packaging/applications/); the app resolves "last meeting" itself.
const launchReview = () =>
    GioUnix.DesktopAppInfo.new(REVIEW_DESKTOP_ENTRY)?.launch([], null) ?? false;

// The same desktop entry's "preferences" action; the dialog and every setting live in the review
// app and the service, never here.
const launchPreferences = () => launchPreferencesAction(id => GioUnix.DesktopAppInfo.new(id));

// The path is the service's answer (opaque); the desktop hands it to the default handler for
// Markdown. launch_default_for_uri_async never blocks and rejects when no app is set.
Gio._promisify(Gio.AppInfo, 'launch_default_for_uri_async', 'launch_default_for_uri_finish');
const openSummary = path =>
    Gio.AppInfo.launch_default_for_uri_async(Gio.File.new_for_path(path).get_uri(), null, null);

async function nameHasOwner(connection, name) {
    const reply = await connection.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'NameHasOwner', new GLib.Variant('(s)', [name]),
        new GLib.VariantType('(b)'), Gio.DBusCallFlags.NONE, NAME_CALL_TIMEOUT_MS, null);
    const [owned] = reply.deepUnpack();
    return owned;
}

/**
 * The tray: the extension's view model, controller and D-Bus clients, shown through a
 * StatusNotifierItem and a dbusmenu menu instead of a GNOME Shell panel button. The wiring is the
 * extension's `enable()` without GNOME Shell. Nothing touches the bus until `start()`, and
 * `destroy()` releases everything. The clients never start the service.
 */
export class Tray {
    /**
     * @param {object} options
     * @param {Gio.DBusConnection} [options.connection] - bus to use (the session bus by default)
     * @param {{every: Function, cancel: Function}} options.scheduler - the controller's tick
     *     (a GLibScheduler); the caller owns and destroys it
     * @param {string} [options.iconThemePath] - directory the host searches for the icon names
     * @param {Function} [options.notify] - `notify(title, body)`; a Notifier on the connection
     *     by default
     * @param {Function} [options.launchReview] - opens the review app
     * @param {Function} [options.launchPreferences] - opens the review app's Preferences dialog
     * @param {Function} [options.openSummary] - opens the summary file at the given path
     * @param {Function} [options.now] - monotonic clock in microseconds for the elapsed time
     * @param {Function} [options.logError] - called with one line of text per problem
     */
    constructor({connection = Gio.DBus.session, scheduler, iconThemePath = '', notify = null,
        launchReview: review = launchReview, launchPreferences: preferences = launchPreferences,
        openSummary: summary = openSummary, now = undefined,
        logError = message => printerr(`meeting-scribe-tray: ${message}`)}) {
        this._connection = connection;
        this._scheduler = scheduler;
        this._iconThemePath = iconThemePath;
        this._launchReview = review;
        this._launchPreferences = preferences;
        this._openSummary = summary;
        this._now = now;
        this._logError = logError;
        if (notify === null) {
            const notifier = new Notifier({connection, logError});
            this._notify = (title, body) => notifier.notify(title, body);
        } else {
            this._notify = notify;
        }

        this._started = false;
        this._destroyed = false;
        this._ownerId = 0;
        this._settleOwnership = null; // resolves a pending name request
        this._menu = null;
        this._item = null;
        this._client = null;
        this._meetings = null;
        this._controller = null;
    }

    /**
     * Start the tray unless GNOME Shell runs (the extension is the client there) or another
     * tray owns `dev.meetingscribe.Tray`. Call it once. Rejects only when the bus daemon cannot
     * be asked.
     *
     * @returns {Promise<{started: boolean, reason?: 'gnome-shell'|'already-running'|'stopped'}>}
     *     `stopped` when `destroy()` ran before the start finished, or on a second call
     */
    async start() {
        if (this._started || this._destroyed)
            return {started: false, reason: 'stopped'};
        this._started = true;
        if (await nameHasOwner(this._connection, GNOME_SHELL_BUS_NAME))
            return {started: false, reason: 'gnome-shell'};
        if (this._destroyed)
            return {started: false, reason: 'stopped'};
        const owned = await this._ownTrayName();
        if (this._destroyed) // destroy() already released the name
            return {started: false, reason: 'stopped'};
        if (!owned) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = 0;
            return {started: false, reason: 'already-running'};
        }
        this._wire();
        return {started: true};
    }

    /** Destroy the clients, the controller, the item and the menu; release the name. Idempotent. */
    destroy() {
        if (this._destroyed)
            return;
        this._destroyed = true;
        this._client?.destroy();
        this._client = null;
        this._meetings?.destroy();
        this._meetings = null;
        this._controller?.destroy();
        this._controller = null;
        this._item?.destroy();
        this._item = null;
        this._menu?.destroy();
        this._menu = null;
        if (this._ownerId)
            Gio.bus_unown_name(this._ownerId);
        this._ownerId = 0;
        // After unown no callback runs: settle a pending request so start() can return.
        this._settleOwnership?.(false);
        this._settleOwnership = null;
    }

    // DO_NOT_QUEUE: a second tray learns at once that the name is taken, and exits.
    _ownTrayName() {
        return new Promise(resolve => {
            this._settleOwnership = resolve;
            this._ownerId = Gio.bus_own_name_on_connection(this._connection, TRAY_BUS_NAME,
                Gio.BusNameOwnerFlags.DO_NOT_QUEUE, () => resolve(true), () => resolve(false));
        });
    }

    // The extension's enable(): every callback reaches the controller lazily, so none fires
    // into a half-built or destroyed tray.
    _wire() {
        const connection = this._connection;
        this._menu = new DBusMenu({
            connection,
            onActivate: action => this._controller?.onMenuAction(action),
            onAboutToShow: () => this._controller?.onMenuOpened(),
        });
        this._item = new StatusNotifierItem({
            connection,
            iconThemePath: this._iconThemePath,
            logError: this._logError,
        });
        this._client = new RecordingClient({
            connection,
            busName: SERVICE_BUS_NAME,
            objectPath: SERVICE_OBJECT_PATH,
            onChange: snapshot => this._controller?.handleChange(snapshot),
            now: this._now,
        });
        this._meetings = new MeetingsClient({
            connection,
            busName: SERVICE_BUS_NAME,
            objectPath: SERVICE_OBJECT_PATH,
            onChange: () => this._controller?.handleProcessingChange(),
        });
        this._controller = new IndicatorController({
            client: this._client,
            meetings: this._meetings,
            view: new TrayView({item: this._item, menu: this._menu}),
            scheduler: this._scheduler,
            notify: this._notify,
            launchReview: this._launchReview,
            launchPreferences: this._launchPreferences,
            openSummary: this._openSummary,
            _: text => text, // no translations, as in the extension
        });
        // Render before exporting, so the host's first read already shows the current state.
        this._controller.refresh();
        this._menu.export();
        this._item.start();
        this._client.connect();
        this._meetings.connect();
    }
}
