import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const INTERFACE_XML = `<node>
  <interface name="dev.meetingscribe.Recording1">
    <method name="Start"><arg type="s" direction="out" name="session_id"/></method>
    <method name="Stop"><arg type="s" direction="out" name="session_id"/></method>
    <property name="State" type="s" access="read"/>
    <property name="SessionId" type="s" access="read"/>
    <property name="ElapsedSeconds" type="d" access="read"/>
    <property name="Directory" type="s" access="read"/>
    <property name="FailureReason" type="s" access="read"/>
    <signal name="StateChanged">
      <arg type="s" name="state"/>
      <arg type="s" name="session_id"/>
    </signal>
  </interface>
</node>`;

const INTERFACE_NAME = 'dev.meetingscribe.Recording1';
const UNKNOWN_SERVICE = 'org.freedesktop.DBus.Error.ServiceUnknown';
const MICROSECONDS = 1_000_000;
// Stop blocks up to RECORDING_STOP_TIMEOUT_SECONDS (10) + 4 s of SIGTERM/SIGKILL grace in the
// service (api-contracts.md); 20 s leaves margin without waiting out the 25 s D-Bus default.
export const DEFAULT_CALL_TIMEOUT_MS = 20_000;

Gio._promisify(Gio.DBusConnection.prototype, 'call', 'call_finish');

/**
 * Read one property with an explicit Properties.Get (the proxy cache is stale for properties
 * the service does not announce). Resolves with the unpacked value.
 */
export async function getProperty(connection, busName, objectPath, name, cancellable, timeoutMs) {
    const reply = await connection.call(busName, objectPath,
        'org.freedesktop.DBus.Properties', 'Get',
        new GLib.Variant('(ss)', [INTERFACE_NAME, name]),
        new GLib.VariantType('(v)'), Gio.DBusCallFlags.NO_AUTO_START, timeoutMs, cancellable);
    const [value] = reply.deepUnpack();
    return value.unpack();
}

const RecordingProxy = Gio.DBusProxy.makeProxyWrapper(INTERFACE_XML);

/**
 * Thin client for dev.meetingscribe.Recording1: watches the service name, keeps a snapshot
 * shaped for `viewModel()` and reports every change through `onChange`. Nothing touches the
 * bus until `connect()`, and `destroy()` releases everything.
 */
export class RecordingClient {
    /**
     * @param {object} options
     * @param {Gio.DBusConnection} [options.connection] - bus to use (the session bus by default)
     * @param {string} options.busName - well-known name of the service
     * @param {string} options.objectPath - object path of the recording object
     * @param {Function} options.onChange - called with the snapshot after every change
     * @param {Function} [options.now] - monotonic clock in microseconds
     * @param {Function} [options.getProperty] - Properties.Get reader (injectable for tests)
     * @param {number} [options.callTimeoutMs] - timeout of every D-Bus call, in milliseconds
     */
    constructor({connection = Gio.DBus.session, busName, objectPath, onChange,
        now = () => GLib.get_monotonic_time(), getProperty: readProperty = getProperty,
        callTimeoutMs = DEFAULT_CALL_TIMEOUT_MS}) {
        this._connection = connection;
        this._busName = busName;
        this._objectPath = objectPath;
        this._onChange = onChange;
        this._now = now;
        this._getProperty = readProperty;
        this._callTimeoutMs = callTimeoutMs;

        this._destroyed = false;
        this._watchId = 0;
        this._proxy = null;
        this._proxyHandlers = [];
        this._cancellable = null;
        this._generation = 0; // bumped on every appear/vanish to drop stale async results
        this._recording = 0; // bumped on every entry into RECORDING and on vanish
        this._stateSeq = 0; // bumped whenever the state is applied, to drop a pending FAILED

        this._available = false;
        this._busy = false;
        this._state = 'IDLE';
        this._sessionId = '';
        this._failureReason = '';
        this._elapsedBase = 0; // seconds at _elapsedSince
        this._elapsedSince = 0; // monotonic microseconds, set while RECORDING
    }

    /** The current view of the service; elapsed time is computed locally on each read. */
    get snapshot() {
        return {
            available: this._available,
            busy: this._busy,
            state: this._state,
            sessionId: this._sessionId,
            elapsedSeconds: this._elapsed(),
            failureReason: this._failureReason,
        };
    }

    /** Start watching the service name (without starting the service). */
    connect() {
        if (this._destroyed || this._watchId)
            return;
        this._watchId = Gio.bus_watch_name_on_connection(
            this._connection, this._busName, Gio.BusNameWatcherFlags.NONE,
            () => this._onAppeared(), () => this._onVanished());
    }

    /** Call Start; resolves with the session id, or null when another call is in flight. */
    start() {
        return this._call('StartAsync');
    }

    /** Call Stop; resolves with the session id, or null when another call is in flight. */
    stop() {
        return this._call('StopAsync');
    }

    /** Unwatch the name, disconnect every handler and drop the proxy. Idempotent. */
    destroy() {
        if (this._destroyed)
            return;
        this._destroyed = true;
        if (this._watchId)
            Gio.bus_unwatch_name(this._watchId);
        this._watchId = 0;
        this._dropProxy();
        this._available = false;
        this._busy = false;
    }

    _elapsed() {
        if (this._state !== 'RECORDING')
            return this._elapsedBase;
        return this._elapsedBase + (this._now() - this._elapsedSince) / MICROSECONDS;
    }

    _notify() {
        if (!this._destroyed)
            this._onChange(this.snapshot);
    }

    _onAppeared() {
        if (this._destroyed)
            return;
        this._dropProxy();
        const generation = ++this._generation;
        const cancellable = new Gio.Cancellable();
        this._cancellable = cancellable;
        // DO_NOT_AUTO_START: creating the proxy must never launch the service.
        new RecordingProxy(this._connection, this._busName, this._objectPath,
            (proxy, error) => {
                if (error || this._destroyed || generation !== this._generation)
                    return;
                this._adopt(proxy);
            }, cancellable, Gio.DBusProxyFlags.DO_NOT_AUTO_START);
    }

    _adopt(proxy) {
        this._proxy = proxy;
        proxy.set_default_timeout(this._callTimeoutMs);
        this._proxyHandlers = [
            proxy.connect('g-properties-changed', (_proxy, changed) => this._onProperties(changed)),
            proxy.connectSignal('StateChanged',
                (_proxy, _sender, [state, sessionId]) => this._applyState(state, sessionId)),
        ];
        this._assign(proxy.State ?? 'IDLE', proxy.SessionId ?? '', proxy.FailureReason ?? '');
        this._available = true;
        this._notify();
    }

    _onVanished() {
        if (this._destroyed)
            return;
        this._generation++;
        this._recording++;
        this._stateSeq++;
        this._dropProxy();
        const wasAvailable = this._available;
        this._available = false;
        this._state = 'IDLE';
        this._sessionId = '';
        this._failureReason = '';
        this._elapsedBase = 0;
        if (wasAvailable)
            this._notify();
    }

    _dropProxy() {
        this._cancellable?.cancel();
        this._cancellable = null;
        const proxy = this._proxy;
        this._proxy = null;
        const handlers = this._proxyHandlers;
        this._proxyHandlers = [];
        if (!proxy)
            return;
        // connectSignal ids are disconnected with disconnectSignal, GObject ids with disconnect.
        proxy.disconnect(handlers[0]);
        proxy.disconnectSignal(handlers[1]);
    }

    _onProperties(changed) {
        const values = changed.deepUnpack();
        const failureReason = 'FailureReason' in values
            ? values.FailureReason.unpack() : this._failureReason;
        const state = 'State' in values ? values.State.unpack() : this._state;
        const sessionId = 'SessionId' in values ? values.SessionId.unpack() : this._sessionId;
        this._applyState(state, sessionId, failureReason);
    }

    _applyState(state, sessionId, failureReason = this._failureReason) {
        if (state === 'FAILED' && this._state !== 'FAILED' && failureReason === '') {
            // StateChanged carries no reason and PropertiesChanged follows it: read the reason
            // before reporting so no snapshot shows FAILED without it.
            this._resolveFailure(state, sessionId);
            return;
        }
        this._commitState(state, sessionId, failureReason);
    }

    _commitState(state, sessionId, failureReason) {
        this._stateSeq++;
        if (this._assign(state, sessionId, failureReason))
            this._notify();
    }

    // Returns true when something a view depends on changed (elapsed time is polled).
    _assign(state, sessionId, failureReason) {
        const changed = state !== this._state || sessionId !== this._sessionId ||
            failureReason !== this._failureReason;
        const wasRecording = this._state === 'RECORDING';
        if (wasRecording && state !== 'RECORDING') {
            this._elapsedBase = this._elapsed(); // freeze
        } else if (!wasRecording && state === 'RECORDING') {
            this._elapsedBase = 0;
            this._elapsedSince = this._now();
            this._recording++;
            this._seedElapsed();
        }
        this._state = state;
        this._sessionId = sessionId;
        this._failureReason = failureReason;
        return changed;
    }

    // The proxy cache never refreshes ElapsedSeconds (the service does not announce it), so
    // read it once with an explicit Properties.Get when a recording is seen starting.
    async _seedElapsed() {
        const generation = this._generation;
        const recording = this._recording; // a later recording must not take this reply
        const requestedAt = this._now(); // the service's value is as of about now, not the reply
        try {
            const value = await this._getProperty(this._connection, this._busName,
                this._objectPath, 'ElapsedSeconds', this._cancellable, this._callTimeoutMs);
            if (this._destroyed || generation !== this._generation ||
                recording !== this._recording || this._state !== 'RECORDING')
                return;
            this._elapsedBase = value;
            this._elapsedSince = requestedAt;
            this._notify();
        } catch (_e) {
            // Keep the local count from zero; the next state change corrects nothing here.
        }
    }

    async _resolveFailure(state, sessionId) {
        const generation = this._generation;
        const seq = ++this._stateSeq;
        let reason = '';
        try {
            reason = await this._getProperty(this._connection, this._busName, this._objectPath,
                'FailureReason', this._cancellable, this._callTimeoutMs);
        } catch (_e) {
            // Report FAILED without a reason rather than never.
        }
        if (this._destroyed || generation !== this._generation || seq !== this._stateSeq)
            return; // vanished, or a newer state (or the PropertiesChanged) was applied first
        this._commitState(state, sessionId, reason);
    }

    async _call(method) {
        if (this._busy)
            return null;
        if (!this._proxy) {
            throw {name: UNKNOWN_SERVICE, message: 'The service is not running.'};
        }
        const proxy = this._proxy;
        this._busy = true;
        this._notify();
        try {
            const [sessionId] = await proxy[method]();
            return sessionId;
        } catch (e) {
            const name = Gio.DBusError.get_remote_error(e) ?? '';
            Gio.DBusError.strip_remote_error(e);
            throw {name, message: e.message};
        } finally {
            this._busy = false;
            this._notify();
        }
    }
}
