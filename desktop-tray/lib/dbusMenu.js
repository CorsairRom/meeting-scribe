import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const MENU_XML = `<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
  </interface>
</node>`;

const ROOT_ID = 0;
const ROOT_PROPERTIES = {'children-display': 'submenu'};
const LAYOUT_TYPE = '(ia{sv}av)';

// dbusmenu reads a single `_` as a mnemonic marker; `__` shows one underscore.
const escapeMnemonic = text => String(text).replaceAll('_', '__');

// What makes two menus the same layout for the host: the kinds and, for actions, what they do.
const shapeOf = item => (item.kind === 'action' ? `action:${item.action}` : item.kind);

function itemProperties(item) {
    switch (item.kind) {
    case 'separator':
        return {type: 'separator'};
    case 'action':
        return {label: escapeMnemonic(item.text), enabled: true};
    default: // status: a row that only informs
        return {label: escapeMnemonic(item.text), enabled: false};
    }
}

// The asked properties as a{sv}; no names means every property.
function toVariants(properties, names = []) {
    const variants = {};
    for (const [name, value] of Object.entries(properties)) {
        if (names.length === 0 || names.includes(name))
            variants[name] = new GLib.Variant(typeof value === 'boolean' ? 'b' : 's', value);
    }
    return variants;
}

const invalidArgs = message => new GLib.Error(Gio.DBusError, Gio.DBusError.INVALID_ARGS, message);

/**
 * Exports a `com.canonical.dbusmenu` menu built from the extension's view-model menu items
 * (`status`, `action` and `separator`). Nothing touches the bus until `export()`, and
 * `destroy()` unexports it for good.
 *
 * Ids stay stable while the kinds and actions keep their order, so a new label (the elapsed
 * time, every second) is one `ItemsPropertiesUpdated` and an open menu does not rebuild. Any
 * other change is a new layout revision with fresh ids: a host keeps the type of an item it
 * already knows, so an id is never reused for another item.
 */
export class DBusMenu {
    /**
     * @param {object} options
     * @param {Gio.DBusConnection} [options.connection] - bus to export on (the session bus by default)
     * @param {string} [options.objectPath] - object path of the menu
     * @param {Function} [options.onActivate] - called with the item's `action` when it is clicked
     * @param {Function} [options.onAboutToShow] - called when the host is about to show the menu
     */
    constructor({connection = Gio.DBus.session, objectPath = '/MenuBar',
        onActivate = () => {}, onAboutToShow = () => {}} = {}) {
        this._connection = connection;
        this._objectPath = objectPath;
        this._onActivate = onActivate;
        this._onAboutToShow = onAboutToShow;

        this._destroyed = false;
        this._impl = null;
        this._revision = 0;
        this._nextId = ROOT_ID + 1;
        this._entries = []; // {id, shape, action, properties} per item, in menu order
    }

    /** Export the menu on the connection. Idempotent; does nothing after `destroy()`. */
    export() {
        if (this._destroyed || this._impl)
            return;
        this._impl = Gio.DBusExportedObject.wrapJSObject(MENU_XML, this._handlers());
        this._impl.export(this._connection, this._objectPath);
    }

    /**
     * Show these items. A label-only change sends the changed labels; any other change sends a
     * new layout revision.
     *
     * @param {object[]} menuItems - the view model's `menuItems`, in order
     */
    setItems(menuItems) {
        if (this._destroyed)
            return;
        const shapes = menuItems.map(shapeOf);
        const sameLayout = shapes.length === this._entries.length &&
            shapes.every((shape, index) => shape === this._entries[index].shape);
        if (!sameLayout) {
            this._entries = menuItems.map((item, index) => ({
                id: this._nextId++, shape: shapes[index],
                action: item.kind === 'action' ? item.action : undefined,
                properties: itemProperties(item),
            }));
            this._revision++;
            this._emit('LayoutUpdated', '(ui)', [this._revision, ROOT_ID]);
            return;
        }
        const updated = [];
        menuItems.forEach((item, index) => {
            const entry = this._entries[index];
            const properties = itemProperties(item);
            if (properties.label !== entry.properties.label)
                updated.push([entry.id, toVariants({label: properties.label})]);
            entry.properties = properties;
        });
        if (updated.length > 0)
            this._emit('ItemsPropertiesUpdated', '(a(ia{sv})a(ias))', [updated, []]);
    }

    /** Unexport the menu and ignore everything afterwards. Idempotent. */
    destroy() {
        this._destroyed = true;
        this._impl?.unexport();
        this._impl = null;
    }

    _emit(name, signature, args) {
        this._impl?.emit_signal(name, new GLib.Variant(signature, args));
    }

    // The root and every item as {id, properties}; the root is id 0.
    _nodes() {
        return [{id: ROOT_ID, properties: ROOT_PROPERTIES}, ...this._entries];
    }

    _node(id) {
        return this._nodes().find(node => node.id === id);
    }

    _layout(parentId, depth, names) {
        const node = this._node(parentId);
        if (!node)
            throw invalidArgs(`unknown menu item ${parentId}`);
        const children = parentId === ROOT_ID && depth !== 0
            ? this._entries.map(entry =>
                new GLib.Variant(LAYOUT_TYPE, [entry.id, toVariants(entry.properties, names), []]))
            : [];
        return new GLib.Variant(`(u${LAYOUT_TYPE})`,
            [this._revision, [node.id, toVariants(node.properties, names), children]]);
    }

    _groupProperties(ids, names) {
        const nodes = ids.length === 0
            ? this._nodes()
            : ids.map(id => this._node(id)).filter(node => node !== undefined);
        return new GLib.Variant('(a(ia{sv}))',
            [nodes.map(node => [node.id, toVariants(node.properties, names)])]);
    }

    _property(id, name) {
        const value = this._node(id)?.properties[name];
        if (value === undefined)
            throw invalidArgs(`menu item ${id} has no property ${name}`);
        return new GLib.Variant('(v)', [toVariants({[name]: value})[name]]);
    }

    // Only a click on an action does something; returns false for an unknown id.
    _event(id, eventId) {
        const node = this._node(id);
        if (eventId === 'clicked' && node?.action !== undefined)
            this._onActivate(node.action);
        return node !== undefined;
    }

    _aboutToShow(ids) {
        if (ids.includes(ROOT_ID))
            this._onAboutToShow();
        return ids.filter(id => !this._node(id));
    }

    // The D-Bus implementation, kept off the public API of the class.
    _handlers() {
        return {
            Version: 3,
            TextDirection: 'ltr',
            Status: 'normal',
            IconThemePath: [],
            GetLayout: (parentId, depth, names) => this._layout(parentId, depth, names),
            GetGroupProperties: (ids, names) => this._groupProperties(ids, names),
            GetProperty: (id, name) => this._property(id, name),
            Event: (id, eventId) => {
                this._event(id, eventId);
            },
            EventGroup: events => events.filter(([id, eventId]) => !this._event(id, eventId))
                .map(([id]) => id),
            AboutToShow: id => {
                this._aboutToShow([id]);
                return false; // the items are always current: the host needs no refresh
            },
            AboutToShowGroup: ids => [[], this._aboutToShow(ids)],
        };
    }
}
