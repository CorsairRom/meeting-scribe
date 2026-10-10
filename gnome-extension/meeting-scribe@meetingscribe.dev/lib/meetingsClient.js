import Gio from 'gi://Gio';

export const INTERFACE_XML = `<node>
  <interface name="dev.meetingscribe.Meetings1">
    <method name="Reprocess">
      <arg type="s" direction="in" name="meeting_id"/>
      <arg type="s" direction="out" name="state"/>
    </method>
    <property name="ActiveMeetingId" type="s" access="read"/>
    <property name="ActiveState" type="s" access="read"/>
    <property name="QueuedCount" type="u" access="read"/>
    <signal name="ProcessingStateChanged">
      <arg type="s" name="meeting_id"/>
      <arg type="s" name="state"/>
    </signal>
    <signal name="ProcessingFinished">
      <arg type="s" name="meeting_id"/>
      <arg type="s" name="state"/>
      <arg type="i" name="speaker_count"/>
    </signal>
  </interface>
</node>`;

const UNKNOWN_SERVICE = 'org.freedesktop.DBus.Error.ServiceUnknown';
// Reprocess only queues the job and answers at once; 10 s is generous and below the 25 s default.
export const DEFAULT_CALL_TIMEOUT_MS = 10_000;

const MeetingsProxy = Gio.DBusProxy.makeProxyWrapper(INTERFACE_XML);

/**
 * Thin client for dev.meetingscribe.Meetings1: watches the service name (never starting it),
 * keeps a snapshot of the processing pipeline and of the last finished job, and reports every
 * change through `onChange`. Nothing touches the bus until `connect()`; `destroy()` releases
 * everything. The snapshot carries ids and counts only, never meeting content.
 */
export class MeetingsClient {
    /**
     * @param {object} options
     * @param {Gio.DBusConnection} [options.connection] - bus to use (the session bus by default)
     * @param {string} options.busName - well-known name of the service
     * @param {string} options.objectPath - object path of the service
     * @param {Function} options.onChange - called with the snapshot after every change
     * @param {number} [options.callTimeoutMs] - timeout of every D-Bus call, in milliseconds
     * @param {Function} [options.logError] - receives a message when the proxy cannot be built
     * @param {Function} [options.createProxy] - proxy factory (test seam), same shape as `new MeetingsProxy`
     */
    constructor({connection = Gio.DBus.session, busName, objectPath, onChange,
        callTimeoutMs = DEFAULT_CALL_TIMEOUT_MS, logError = message => console.error(message),
        createProxy = (...args) => new MeetingsProxy(...args)}) {
        this._connection = connection;
        this._busName = busName;
        this._objectPath = objectPath;
        this._onChange = onChange;
        this._callTimeoutMs = callTimeoutMs;
        this._logError = logError;
        this._createProxy = createProxy;

        this._destroyed = false;
        this._watchId = 0;
        this._proxy = null;
        this._proxyHandlers = [];
        this._cancellable = null;
        this._generation = 0; // bumped on every appear/vanish to drop stale async results
        this._busy = false;

        this._available = false;
        this._activeMeetingId = '';
        this._activeState = 'IDLE';
        this._queuedCount = 0;
        this._last = null; // {meetingId, state, speakerCount} of the last ProcessingFinished
    }

    /** The current view of the pipeline, shaped for `viewModel()`'s `processing` input. */
    get snapshot() {
        return {
            available: this._available,
            activeMeetingId: this._activeMeetingId,
            activeState: this._activeState,
            queuedCount: this._queuedCount,
            last: this._last,
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

    /**
     * Call Reprocess; resolves with the new state, or null when a call is in flight. The last
     * result is forgotten once the service accepted the job, so its action cannot fire twice.
     */
    async reprocess(meetingId) {
        if (this._busy)
            return null;
        if (!this._proxy)
            throw {name: UNKNOWN_SERVICE, message: 'The service is not running.'};
        const proxy = this._proxy;
        this._busy = true;
        try {
            const [state] = await proxy.ReprocessAsync(meetingId);
            if (!this._destroyed && this._last?.meetingId === meetingId) {
                this._last = null;
                this._notify();
            }
            return state;
        } catch (e) {
            const name = Gio.DBusError.get_remote_error(e) ?? '';
            Gio.DBusError.strip_remote_error(e);
            throw {name, message: e.message};
        } finally {
            this._busy = false;
        }
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
    }

    _notify() {
        if (!this._destroyed)
            this._onChange(this.snapshot);
    }

    _onAppeared() {
        if (this._destroyed)
            return;
        this._dropProxy();
        this._last = null; // a (re)started service has no result to show yet
        const generation = ++this._generation;
        const cancellable = new Gio.Cancellable();
        this._cancellable = cancellable;
        // DO_NOT_AUTO_START: creating the proxy must never launch the service.
        this._createProxy(this._connection, this._busName, this._objectPath,
            (proxy, error) => {
                if (this._destroyed || generation !== this._generation)
                    return;
                if (error) {
                    // Stays unavailable (processing hidden) until the next name-owner change.
                    this._logError(`meeting-scribe: cannot create the Meetings1 proxy: ${error.message}`);
                    return;
                }
                this._adopt(proxy);
            }, cancellable, Gio.DBusProxyFlags.DO_NOT_AUTO_START);
    }

    _adopt(proxy) {
        this._proxy = proxy;
        proxy.set_default_timeout(this._callTimeoutMs);
        this._proxyHandlers = [
            proxy.connect('g-properties-changed', (_proxy, changed) => this._onProperties(changed)),
            proxy.connectSignal('ProcessingFinished',
                (_proxy, _sender, [meetingId, state, speakerCount]) =>
                    this._onFinished(meetingId, state, speakerCount)),
        ];
        this._activeMeetingId = proxy.ActiveMeetingId ?? '';
        this._activeState = proxy.ActiveState ?? 'IDLE';
        this._queuedCount = proxy.QueuedCount ?? 0;
        this._available = true;
        this._notify();
    }

    _onProperties(changed) {
        const values = changed.deepUnpack();
        if ('ActiveMeetingId' in values)
            this._activeMeetingId = values.ActiveMeetingId.unpack();
        if ('ActiveState' in values) {
            const next = values.ActiveState.unpack();
            // A job starting ends the "last result" period even if its signal never arrived.
            if (this._activeState === 'IDLE' && next !== 'IDLE')
                this._last = null;
            this._activeState = next;
        }
        if ('QueuedCount' in values)
            this._queuedCount = values.QueuedCount.unpack();
        this._notify();
    }

    _onFinished(meetingId, state, speakerCount) {
        this._last = {meetingId, state, speakerCount};
        this._notify();
    }

    _onVanished() {
        if (this._destroyed)
            return;
        this._generation++;
        this._dropProxy();
        const wasAvailable = this._available;
        this._available = false;
        this._activeMeetingId = '';
        this._activeState = 'IDLE';
        this._queuedCount = 0;
        this._last = null;
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
        proxy.disconnect(handlers[0]);
        proxy.disconnectSignal(handlers[1]);
    }
}
