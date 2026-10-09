// Test fixtures: a private dbus-daemon and a fake dev.meetingscribe.Recording1 service.
// Nothing here may touch the user's session bus (Gio.DBus.session) or the real service.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {INTERFACE_XML} from '../meeting-scribe@meetingscribe.dev/lib/recordingClient.js';

Gio._promisify(Gio.DataInputStream.prototype, 'read_line_async', 'read_line_finish_utf8');
Gio._promisify(Gio.DBusConnection, 'new_for_address', 'new_for_address_finish');
Gio._promisify(Gio.DBusConnection.prototype, 'close', 'close_finish');
Gio._promisify(Gio.Subprocess.prototype, 'wait_async', 'wait_finish');

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
        bus._process = Gio.Subprocess.new(
            ['dbus-daemon', `--config-file=${config}`, '--nofork', '--print-address=1'],
            Gio.SubprocessFlags.STDOUT_PIPE);
        try {
            const reader = Gio.DataInputStream.new(bus._process.get_stdout_pipe());
            const [line] = await reader.read_line_async(GLib.PRIORITY_DEFAULT, null);
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
        this.address = '';
    }

    /** Open an explicit connection to the private bus (never DBUS_SESSION_BUS_ADDRESS). */
    async connect() {
        const connection = await Gio.DBusConnection.new_for_address(
            this.address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            null, null);
        this._connections.push(connection);
        return connection;
    }

    /** Close connections, kill the daemon by PID and remove the temp directory. */
    async stop() {
        for (const connection of this._connections) {
            if (!connection.is_closed())
                await connection.close(null).catch(() => {});
        }
        this._connections = [];
        if (this._process) {
            this._process.force_exit();
            await this._process.wait_async(null).catch(() => {});
            this._process = null;
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
    }

    /** Export the object and own the name; resolves once the name is acquired. */
    appear() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(INTERFACE_XML, this);
        this._impl.export(this._connection, this._objectPath);
        return new Promise(resolve => {
            this._ownerId = Gio.bus_own_name_on_connection(
                this._connection, this._busName, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                () => resolve(), null);
        });
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
        if (this.State === 'RECORDING') {
            invocation.return_dbus_error(`${ERROR_PREFIX}RECORDING_ALREADY_ACTIVE`, 'already');
            return;
        }
        this._publish('RECORDING', 'session-1', '');
        invocation.return_value(new GLib.Variant('(s)', [this.SessionId]));
    }

    StopAsync(_params, invocation) {
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
