// Test fixture: an in-process org.kde.StatusNotifierWatcher, the role plasmashell plays on KDE.
// Not named *.test.js, so the runner does not import it as a test file.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {withTimeout} from '../../gnome-extension/tests/privateBus.js';

export const WATCHER_NAME = 'org.kde.StatusNotifierWatcher';
export const WATCHER_PATH = '/StatusNotifierWatcher';

const WATCHER_XML = `<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem"><arg name="service" type="s" direction="in"/></method>
    <method name="RegisterStatusNotifierHost"><arg name="service" type="s" direction="in"/></method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg name="service" type="s"/></signal>
    <signal name="StatusNotifierItemUnregistered"><arg name="service" type="s"/></signal>
    <signal name="StatusNotifierHostRegistered"/>
  </interface>
</node>`;

const NAME_CALL_TIMEOUT_MS = 1000;

/**
 * Owns the watcher name on its own connection and records every registration. `stop()` and a
 * later `start()` (or a second instance on another connection) simulate a Plasma restart.
 */
export class FakeStatusNotifierWatcher {
    /** @param {Gio.DBusConnection} connection - a private-bus connection of its own */
    constructor(connection) {
        this._connection = connection;
        this._ownerId = 0;
        this._impl = null;
        this.registrations = []; // {service, sender, ownedAtRegistration}, in call order
        this.hosts = [];
    }

    get RegisteredStatusNotifierItems() {
        return this.registrations.map(registration => registration.service);
    }

    get IsStatusNotifierHostRegistered() {
        return true;
    }

    get ProtocolVersion() {
        return 0;
    }

    /** Export the watcher and own its name; rejects when the name is taken or never acquired. */
    async start() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(WATCHER_XML, this);
        this._impl.export(this._connection, WATCHER_PATH);
        const acquired = new Promise((resolve, reject) => {
            this._ownerId = Gio.bus_own_name_on_connection(
                this._connection, WATCHER_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                () => resolve(),
                () => reject(new Error(`could not own ${WATCHER_NAME}`)));
        });
        try {
            await withTimeout(acquired, `own ${WATCHER_NAME}`);
        } catch (e) {
            this.stop();
            throw e;
        }
    }

    /** Release the name (items see the watcher vanish) and unexport the object. Idempotent. */
    stop() {
        if (this._ownerId)
            Gio.bus_unown_name(this._ownerId);
        this._ownerId = 0;
        this._impl?.unexport();
        this._impl = null;
    }

    RegisterStatusNotifierItemAsync([service], invocation) {
        // Like Plasma's watcher, which drops a registration whose name has no owner yet.
        this.registrations.push({
            service,
            sender: invocation.get_sender(),
            ownedAtRegistration: this._nameHasOwner(service),
        });
        this._impl.emit_signal('StatusNotifierItemRegistered', new GLib.Variant('(s)', [service]));
        invocation.return_value(new GLib.Variant('()', []));
    }

    RegisterStatusNotifierHostAsync([service], invocation) {
        this.hosts.push(service);
        invocation.return_value(new GLib.Variant('()', []));
    }

    // Asks the bus daemon, never the item: a blocking call to an item served by this same
    // main loop would deadlock.
    _nameHasOwner(name) {
        const reply = this._connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'NameHasOwner', new GLib.Variant('(s)', [name]),
            new GLib.VariantType('(b)'), Gio.DBusCallFlags.NONE, NAME_CALL_TIMEOUT_MS, null);
        const [owned] = reply.deepUnpack();
        return owned;
    }
}
