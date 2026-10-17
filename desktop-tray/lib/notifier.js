import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const NOTIFICATIONS_NAME = 'org.freedesktop.Notifications';
const NOTIFICATIONS_PATH = '/org/freedesktop/Notifications';
const APP_NAME = 'Meeting Scribe';
const DEFAULT_EXPIRE_TIMEOUT = -1; // the server decides how long it stays
const NOTIFY_TIMEOUT_MS = 5000;

Gio._promisify(Gio.DBusConnection.prototype, 'call', 'call_finish');

/**
 * Shows the controller's messages through `org.freedesktop.Notifications`, the desktop's own
 * notification server (what `Main.notify` is inside GNOME Shell).
 */
export class Notifier {
    /**
     * @param {object} [options]
     * @param {Gio.DBusConnection} [options.connection] - bus to use (the session bus by default)
     * @param {Function} [options.logError] - called with one line of text per failure
     */
    constructor({connection = Gio.DBus.session,
        logError = message => printerr(`meeting-scribe-tray: ${message}`)} = {}) {
        this._connection = connection;
        this._logError = logError;
    }

    /**
     * Show one notification: no actions, no hints, the server's default timeout. A failure is
     * logged without the text (it may name a meeting) and never reaches the caller.
     *
     * @param {string} title - the notification summary
     * @param {string} body - the notification body
     * @returns {Promise<void>} settles once the server answered or the call failed
     */
    async notify(title, body) {
        try {
            await this._connection.call(NOTIFICATIONS_NAME, NOTIFICATIONS_PATH, NOTIFICATIONS_NAME,
                'Notify', new GLib.Variant('(susssasa{sv}i)',
                    [APP_NAME, 0, '', title, body, [], {}, DEFAULT_EXPIRE_TIMEOUT]),
                new GLib.VariantType('(u)'), Gio.DBusCallFlags.NONE, NOTIFY_TIMEOUT_MS, null);
        } catch (e) {
            this._logError(`could not show a notification: ${e.message}`);
        }
    }
}
