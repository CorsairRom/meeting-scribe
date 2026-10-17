// Test fixture: an in-process org.freedesktop.Notifications server that records every
// notification it is asked to show. Not named *.test.js, so the runner does not import it as a test.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {withTimeout} from '../../gnome-extension/tests/privateBus.js';

export const NOTIFICATIONS_NAME = 'org.freedesktop.Notifications';
export const NOTIFICATIONS_PATH = '/org/freedesktop/Notifications';

const NOTIFICATIONS_XML = `<node>
  <interface name="org.freedesktop.Notifications">
    <method name="Notify">
      <arg type="s" name="app_name" direction="in"/>
      <arg type="u" name="replaces_id" direction="in"/>
      <arg type="s" name="app_icon" direction="in"/>
      <arg type="s" name="summary" direction="in"/>
      <arg type="s" name="body" direction="in"/>
      <arg type="as" name="actions" direction="in"/>
      <arg type="a{sv}" name="hints" direction="in"/>
      <arg type="i" name="expire_timeout" direction="in"/>
      <arg type="u" name="id" direction="out"/>
    </method>
  </interface>
</node>`;

/** Owns the notifications name on its own connection and records every Notify call. */
export class FakeNotifications {
    /** @param {Gio.DBusConnection} connection - a private-bus connection of its own */
    constructor(connection) {
        this._connection = connection;
        this._ownerId = 0;
        this._impl = null;
        this.received = []; // one plain object per Notify call, in call order
    }

    /** Export the server and own its name; rejects when the name is taken or never acquired. */
    async start() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(NOTIFICATIONS_XML, this);
        this._impl.export(this._connection, NOTIFICATIONS_PATH);
        const acquired = new Promise((resolve, reject) => {
            this._ownerId = Gio.bus_own_name_on_connection(
                this._connection, NOTIFICATIONS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                () => resolve(),
                () => reject(new Error(`could not own ${NOTIFICATIONS_NAME}`)));
        });
        try {
            await withTimeout(acquired, `own ${NOTIFICATIONS_NAME}`);
        } catch (e) {
            this.stop();
            throw e;
        }
    }

    /** Release the name and unexport the server. Idempotent. */
    stop() {
        if (this._ownerId)
            Gio.bus_unown_name(this._ownerId);
        this._ownerId = 0;
        this._impl?.unexport();
        this._impl = null;
    }

    NotifyAsync([appName, replacesId, appIcon, summary, body, actions, hints, expireTimeout],
        invocation) {
        this.received.push({
            appName, replacesId, appIcon, summary, body, actions,
            hints: Object.keys(hints), expireTimeout,
        });
        invocation.return_value(new GLib.Variant('(u)', [this.received.length]));
    }
}
