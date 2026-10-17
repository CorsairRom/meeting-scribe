import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const INTERFACE_XML = `<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
</node>`;

const TITLE = 'Meeting Scribe';
const WATCHER_PATH = '/StatusNotifierWatcher';
const WATCHER_INTERFACE = 'org.kde.StatusNotifierWatcher';
const REGISTER_TIMEOUT_MS = 5000;

Gio._promisify(Gio.DBusConnection.prototype, 'call', 'call_finish');

// The name KDE's own KStatusNotifierItem owns for the first item of a process.
function itemBusName() {
    return `org.kde.StatusNotifierItem-${new Gio.Credentials().get_unix_pid()}-1`;
}

/**
 * Exports `org.kde.StatusNotifierItem` and keeps it registered with the watcher: once the item
 * owns its bus name and whenever the watcher name gets an owner (for example after plasmashell
 * restarts). Nothing touches the bus until `start()`, and `destroy()` releases everything.
 */
export class StatusNotifierItem {
    /**
     * @param {object} options
     * @param {Gio.DBusConnection} [options.connection] - bus to use (the session bus by default)
     * @param {string} [options.objectPath] - object path of the item
     * @param {string} [options.menuPath] - object path of the com.canonical.dbusmenu menu
     * @param {string} [options.iconThemePath] - directory the host searches for the icon names
     * @param {string} [options.iconName] - initial icon name
     * @param {string} [options.tooltip] - initial tooltip text
     * @param {string} [options.watcherName] - well-known name of the StatusNotifierWatcher
     * @param {Function} [options.logError] - called with one line of text per problem
     */
    constructor({connection = Gio.DBus.session, objectPath = '/StatusNotifierItem',
        menuPath = '/MenuBar', iconThemePath = '', iconName = '', tooltip = '',
        watcherName = 'org.kde.StatusNotifierWatcher',
        logError = message => printerr(`meeting-scribe-tray: ${message}`)} = {}) {
        this._connection = connection;
        this._objectPath = objectPath;
        this._watcherName = watcherName;
        this._logError = logError;
        this._iconName = iconName;
        this._tooltip = tooltip;
        this._busName = itemBusName();

        this._started = false;
        this._destroyed = false;
        this._exported = null;
        this._ownerId = 0;
        this._watchId = 0;
        this._nameOwned = false;
        this._watcherOwner = ''; // unique name of the current watcher, '' while there is none
        this._registeredWith = ''; // the watcher owner this item last registered with
        this._cancellable = null;
        this._implementation = this._dbusObject(menuPath, iconThemePath);
    }

    /**
     * Export the item, own its bus name and watch for the watcher. Idempotent; does nothing
     * after `destroy()`.
     */
    start() {
        if (this._started || this._destroyed)
            return;
        this._started = true;
        // Export before owning the name: the watcher checks the object as soon as it is told.
        this._exported = Gio.DBusExportedObject.wrapJSObject(INTERFACE_XML, this._implementation);
        this._exported.export(this._connection, this._objectPath);
        this._ownerId = Gio.bus_own_name_on_connection(
            this._connection, this._busName, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
            () => this._onNameAcquired(), () => this._onNameLost());
        this._watchId = Gio.bus_watch_name_on_connection(
            this._connection, this._watcherName, Gio.BusNameWatcherFlags.NONE,
            (_connection, _name, owner) => this._onWatcherAppeared(owner),
            () => this._onWatcherVanished());
    }

    /**
     * Show a new icon or tooltip. Emits `NewIcon` or `NewToolTip` only for the value that
     * changed, so a host re-reads nothing else; `Status` never changes, so `NewStatus` is never
     * emitted.
     *
     * @param {object} values
     * @param {string} [values.iconName] - icon name (unchanged when omitted)
     * @param {string} [values.tooltip] - tooltip text (unchanged when omitted)
     */
    update({iconName = this._iconName, tooltip = this._tooltip} = {}) {
        if (iconName !== this._iconName) {
            this._iconName = iconName;
            this._emit('NewIcon');
        }
        if (tooltip !== this._tooltip) {
            this._tooltip = tooltip;
            this._emit('NewToolTip');
        }
    }

    /** Stop watching, release the bus name and unexport the item. Idempotent. */
    destroy() {
        if (this._destroyed)
            return;
        this._destroyed = true;
        this._cancellable?.cancel();
        this._cancellable = null;
        if (this._watchId)
            Gio.bus_unwatch_name(this._watchId);
        this._watchId = 0;
        if (this._ownerId)
            Gio.bus_unown_name(this._ownerId);
        this._ownerId = 0;
        this._exported?.unexport();
        this._exported = null;
    }

    // The object GJS exports: D-Bus property and method names map to its members.
    _dbusObject(menuPath, iconThemePath) {
        const item = this;
        return {
            Category: 'ApplicationStatus',
            Id: 'meeting-scribe',
            Title: TITLE,
            // Always Active: NeedsAttention makes Plasma pulse the icon endlessly.
            Status: 'Active',
            WindowId: 0,
            IconThemePath: iconThemePath,
            get IconName() {
                return item._iconName;
            },
            IconPixmap: [],
            OverlayIconName: '',
            OverlayIconPixmap: [],
            AttentionIconName: '',
            AttentionIconPixmap: [],
            AttentionMovieName: '',
            get ToolTip() {
                return ['', [], TITLE, item._tooltip];
            },
            ItemIsMenu: true,
            Menu: menuPath,
            // A click never starts or stops a recording (CR-013): the menu is the only way in.
            ContextMenu() {},
            Activate() {},
            SecondaryActivate() {},
            Scroll() {},
        };
    }

    _emit(signal) {
        this._exported?.emit_signal(signal, null); // null before start() and after destroy()
    }

    _onNameAcquired() {
        if (this._destroyed)
            return;
        this._nameOwned = true;
        this._register();
    }

    _onNameLost() {
        if (this._destroyed)
            return;
        this._nameOwned = false;
        this._logError(`could not own the bus name ${this._busName}; the tray icon will not show`);
    }

    _onWatcherAppeared(owner) {
        if (this._destroyed)
            return;
        this._watcherOwner = owner;
        this._register();
    }

    _onWatcherVanished() {
        if (this._destroyed)
            return;
        // A new owner, even the same connection again, has none of our registrations.
        this._cancellable?.cancel();
        this._cancellable = null;
        this._watcherOwner = '';
        this._registeredWith = '';
        this._logError(`no ${this._watcherName} on the session bus; ` +
            'waiting for a tray host to show the icon');
    }

    // Registers once per watcher owner, and only once the name is ours: the watcher drops a
    // registration whose name has no owner.
    async _register() {
        const watcher = this._watcherOwner;
        if (!this._nameOwned || !watcher || this._registeredWith === watcher)
            return;
        this._registeredWith = watcher;
        const cancellable = new Gio.Cancellable();
        this._cancellable = cancellable;
        try {
            // Sent to the owner's unique name, so a reply never comes from a later watcher.
            await this._connection.call(watcher, WATCHER_PATH, WATCHER_INTERFACE,
                'RegisterStatusNotifierItem', new GLib.Variant('(s)', [this._busName]), null,
                Gio.DBusCallFlags.NO_AUTO_START, REGISTER_TIMEOUT_MS, cancellable);
        } catch (e) {
            if (!cancellable.is_cancelled())
                this._logError(`could not register with ${this._watcherName}: ${e.message}`);
        }
    }
}
