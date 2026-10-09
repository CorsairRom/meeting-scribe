// Test fixtures: a private dbus-daemon and a fake dev.meetingscribe.Recording1 service.
// Nothing here may touch the user's session bus (Gio.DBus.session) or the real service.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {registerCleanup} from './harness.js';
import {INTERFACE_XML} from '../meeting-scribe@meetingscribe.dev/lib/recordingClient.js';

Gio._promisify(Gio.DataInputStream.prototype, 'read_line_async', 'read_line_finish_utf8');
Gio._promisify(Gio.DBusConnection, 'new_for_address', 'new_for_address_finish');
Gio._promisify(Gio.DBusConnection.prototype, 'close', 'close_finish');
Gio._promisify(Gio.Subprocess.prototype, 'wait_async', 'wait_finish');

// Every wait in a fixture is bounded below the harness timeout (5 s) so a test's `finally`
// can still stop the daemon.
export const FIXTURE_WAIT_MS = 2000;

/** Reject after `ms`; `onTimeout` lets the caller cancel the underlying operation. */
export function withTimeout(promise, what, ms = FIXTURE_WAIT_MS, onTimeout = () => {}) {
    let sourceId = 0;
    const timer = new Promise((_resolve, reject) => {
        sourceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            sourceId = 0;
            onTimeout();
            reject(new Error(`fixture timed out after ${ms} ms: ${what}`));
            return GLib.SOURCE_REMOVE;
        });
    });
    const cancel = () => {
        if (sourceId)
            GLib.source_remove(sourceId);
    };
    return Promise.race([promise, timer]).finally(cancel);
}

// No service directories: nothing can be bus-activated. EXTERNAL auth, own socket.
const bus_config = dir => `<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=${dir}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
`;

export class PrivateBus {
    /** Start the daemon; the address is read from its stdout. Always `await bus.stop()`. */
    static async start() {
        const dir = GLib.Dir.make_tmp('meeting-scribe-bus-XXXXXX');
        const config = GLib.build_filenamev([dir, 'bus.conf']);
        GLib.file_set_contents(config, bus_config(dir));
        const bus = new PrivateBus(dir);
        registerCleanup(() => bus.stop());
        bus._process = Gio.Subprocess.new(
            ['dbus-daemon', `--config-file=${config}`, '--nofork', '--print-address=1'],
            Gio.SubprocessFlags.STDOUT_PIPE);
        try {
            const reader = Gio.DataInputStream.new(bus._process.get_stdout_pipe());
            const cancellable = new Gio.Cancellable();
            const [line] = await withTimeout(
                reader.read_line_async(GLib.PRIORITY_DEFAULT, cancellable),
                'private dbus-daemon address', FIXTURE_WAIT_MS, () => cancellable.cancel());
            if (!line?.startsWith('unix:'))
                throw new Error('private dbus-daemon did not print an address');
            bus.address = line;
        } catch (e) {
            await bus.stop();
            throw e;
        }
        return bus;
    }

    constructor(dir) {
        this._dir = dir;
        this._connections = [];
        this._stopped = false;
        this.address = '';
    }

    /** Open an explicit connection to the private bus (never DBUS_SESSION_BUS_ADDRESS). */
    async connect() {
        const cancellable = new Gio.Cancellable();
        const connection = await withTimeout(Gio.DBusConnection.new_for_address(
            this.address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            null, cancellable), 'connect to the private bus', FIXTURE_WAIT_MS,
        () => cancellable.cancel());
        this._connections.push(connection);
        return connection;
    }

    /** Kill the daemon by PID, close connections and remove the temp directory. Idempotent. */
    async stop() {
        if (this._stopped)
            return;
        this._stopped = true;
        const process = this._process;
        this._process = null;
        process?.force_exit(); // first: nothing below may leave the daemon running
        const connections = this._connections;
        this._connections = [];
        const settle = async (start, what) => {
            const cancellable = new Gio.Cancellable();
            await withTimeout(start(cancellable), what, FIXTURE_WAIT_MS, () => cancellable.cancel())
                .catch(() => {});
        };
        if (process)
            await settle(c => process.wait_async(c), 'daemon exit');
        for (const connection of connections) {
            if (!connection.is_closed())
                await settle(c => connection.close(c), 'connection close');
        }
        // The killed daemon leaves its socket behind next to the config.
        const dir = GLib.Dir.open(this._dir, 0);
        let name;
        while ((name = dir.read_name()) !== null)
            GLib.unlink(GLib.build_filenamev([this._dir, name]));
        dir.close();
        GLib.rmdir(this._dir);
    }
}

const ERROR_PREFIX = 'dev.meetingscribe.Error.';

/** In-process stand-in for the Python service; behaves like the documented contract. */
export class FakeRecordingService {
    constructor(connection, busName, objectPath) {
        this._connection = connection;
        this._busName = busName;
        this._objectPath = objectPath;
        this._ownerId = 0;
        this._impl = null;
        this.State = 'IDLE';
        this.SessionId = '';
        this.ElapsedSeconds = 0.0;
        this.Directory = '';
        this.FailureReason = '';
        this.hangCalls = false;
    }

    /** Export the object and own the name; rejects when the name is taken or never acquired. */
    async appear() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(INTERFACE_XML, this);
        this._impl.export(this._connection, this._objectPath);
        const acquired = new Promise((resolve, reject) => {
            this._ownerId = Gio.bus_own_name_on_connection(
                this._connection, this._busName, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                () => resolve(),
                () => reject(new Error(`could not own ${this._busName}`)));
        });
        try {
            await withTimeout(acquired, `own ${this._busName}`);
        } catch (e) {
            this.vanish();
            throw e;
        }
    }

    /** Release the name (the watcher sees it vanish) and unexport the object. */
    vanish() {
        if (this._ownerId)
            Gio.bus_unown_name(this._ownerId);
        this._ownerId = 0;
        this._impl?.unexport();
        this._impl = null;
    }

    StartAsync(_params, invocation) {
        if (this.hangCalls)
            return; // never answer: simulates a stuck service
        if (this.State === 'RECORDING') {
            invocation.return_dbus_error(`${ERROR_PREFIX}RECORDING_ALREADY_ACTIVE`, 'already');
            return;
        }
        this._publish('RECORDING', 'session-1', '');
        invocation.return_value(new GLib.Variant('(s)', [this.SessionId]));
    }

    StopAsync(_params, invocation) {
        if (this.hangCalls)
            return;
        if (this.State !== 'RECORDING') {
            invocation.return_dbus_error(`${ERROR_PREFIX}NO_ACTIVE_RECORDING`, 'nothing recording');
            return;
        }
        this._publish('STOPPED', this.SessionId, '');
        invocation.return_value(new GLib.Variant('(s)', [this.SessionId]));
    }

    /** Simulate a capture crash detected by the service's background check. */
    crash(reason) {
        this._publish('FAILED', this.SessionId, reason);
    }

    _publish(state, sessionId, failureReason) {
        this.State = state;
        this.SessionId = sessionId;
        this.FailureReason = failureReason;
        this._impl.emit_signal('StateChanged', new GLib.Variant('(ss)', [state, sessionId]));
        // Like the real service: ElapsedSeconds is deliberately not announced.
        this._impl.emit_property_changed('State', new GLib.Variant('s', state));
        this._impl.emit_property_changed('SessionId', new GLib.Variant('s', sessionId));
        this._impl.emit_property_changed('FailureReason', new GLib.Variant('s', failureReason));
    }
}
