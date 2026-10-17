// The tray's StatusNotifierItem, exercised the way a tray host does: over D-Bus from a second
// connection on a private bus, with a fake org.kde.StatusNotifierWatcher (spec section 3).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {test, assertEqual, assertDeepEqual} from '../../gnome-extension/tests/harness.js';
import {PrivateBus} from '../../gnome-extension/tests/privateBus.js';
import {FakeStatusNotifierWatcher, WATCHER_NAME} from './fakeStatusNotifierWatcher.js';
import {StatusNotifierItem} from '../lib/statusNotifierItem.js';

Gio._promisify(Gio.DBusConnection.prototype, 'call', 'call_finish');

const ITEM_INTERFACE = 'org.kde.StatusNotifierItem';
const ITEM_PATH = '/StatusNotifierItem';
const ITEM_BUS_NAME = `org.kde.StatusNotifierItem-${new Gio.Credentials().get_unix_pid()}-1`;
const ICON_THEME_PATH = '/nonexistent/meeting-scribe/icons';
const IDLE_ICON = 'dev.meetingscribe.Tray-idle';
const RECORDING_ICON = 'dev.meetingscribe.Tray-recording';
const IDLE_TOOLTIP = 'Start recording';
const RECORDING_TOOLTIP = 'Recording in progress · 00:12:34';
const CALL_TIMEOUT_MS = 1000;
const WAIT_LIMIT_MS = 3000; // below the harness timeout so the fixture can still clean up
const QUIET_MS = 100; // long enough for a stray registration or signal to show up

const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));

// Polls `condition` (sync or async); rejects on timeout so a failing test still cleans up.
async function until(condition, description) {
    const deadline = GLib.get_monotonic_time() + WAIT_LIMIT_MS * 1000;
    while (!await condition()) {
        if (GLib.get_monotonic_time() > deadline)
            throw new Error(`condition never held: ${description}`);
        await sleep(10);
    }
}

function callBusDaemon(connection, method, args, replyType) {
    return connection.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        method, args, new GLib.VariantType(replyType), Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS,
        null);
}

async function nameOwner(connection, name) {
    const [owned] = (await callBusDaemon(connection, 'NameHasOwner',
        new GLib.Variant('(s)', [name]), '(b)')).deepUnpack();
    if (!owned)
        return '';
    const [owner] = (await callBusDaemon(connection, 'GetNameOwner',
        new GLib.Variant('(s)', [name]), '(s)')).deepUnpack();
    return owner;
}

function callItem(host, method, signature, args, destination = ITEM_BUS_NAME) {
    return host.call(destination, ITEM_PATH, ITEM_INTERFACE, method,
        new GLib.Variant(signature, args), null, Gio.DBusCallFlags.NO_AUTO_START,
        CALL_TIMEOUT_MS, null);
}

/** Properties.GetAll, as Plasma's host does: name -> GLib.Variant. */
async function getAll(host, destination = ITEM_BUS_NAME) {
    const reply = await host.call(destination, ITEM_PATH, 'org.freedesktop.DBus.Properties',
        'GetAll', new GLib.Variant('(s)', [ITEM_INTERFACE]), new GLib.VariantType('(a{sv})'),
        Gio.DBusCallFlags.NO_AUTO_START, CALL_TIMEOUT_MS, null);
    const [properties] = reply.deepUnpack();
    return properties;
}

function plain(properties) {
    return Object.fromEntries(Object.keys(properties).sort()
        .map(name => [name, properties[name].recursiveUnpack()]));
}

/** Record the name of every signal `sender` emits; resolves once the bus applies the match. */
async function subscribeSignals(host, sender) {
    const received = [];
    const id = host.signal_subscribe(sender, null, null, null, null, Gio.DBusSignalFlags.NONE,
        (_connection, _sender, _path, _interface, name) => received.push(name));
    // The match rule travels before this call on the same connection: once it is answered,
    // no signal can be missed.
    await callBusDaemon(host, 'GetId', null, '(s)');
    return {received, stop: () => host.signal_unsubscribe(id)};
}

/**
 * Private bus + fake watcher + started item + a host connection; always cleaned up.
 * `beforeStart(bus)` runs before the item touches the bus.
 */
async function withFixture(body, {
    watcher: watcherFirst = true,
    beforeStart = async () => {},
    failRegistrations = 0,
    itemOptions = {},
} = {}) {
    const bus = await PrivateBus.start();
    const watchers = [];
    let item = null;
    try {
        const newWatcher = async () => {
            const watcher = new FakeStatusNotifierWatcher(await bus.connect());
            watchers.push(watcher);
            watcher.failNext = failRegistrations;
            await watcher.start();
            return watcher;
        };
        const watcher = watcherFirst ? await newWatcher() : null;
        const connection = await bus.connect();
        const host = await bus.connect();
        const logged = [];
        item = new StatusNotifierItem({
            connection,
            iconThemePath: ICON_THEME_PATH,
            iconName: IDLE_ICON,
            tooltip: IDLE_TOOLTIP,
            logError: message => logged.push(message),
            ...itemOptions,
        });
        await beforeStart(bus);
        item.start();
        const watcherGone = () => until(async () => await nameOwner(host, WATCHER_NAME) === '',
            'the watcher name has no owner');
        await body({item, watcher, newWatcher, watcherGone, connection, host, logged});
    } finally {
        item?.destroy();
        watchers.forEach(watcher => watcher.stop());
        await bus.stop();
    }
}

const registered = watcher => () => watcher.registrations.length > 0;

test('the item owns its KDE-style bus name and registers it once with the watcher (AC-1)', async () => {
    await withFixture(async ({watcher, connection, host}) => {
        await until(registered(watcher), 'the item registered');
        await sleep(QUIET_MS);
        assertDeepEqual(watcher.registrations, [{
            service: ITEM_BUS_NAME,
            sender: connection.get_unique_name(),
            ownedAtRegistration: true,
        }]);
        assertEqual(await nameOwner(host, ITEM_BUS_NAME), connection.get_unique_name(), 'owner: ');
    });
});

test('the item exposes every StatusNotifierItem property with the spec values (AC-1)', async () => {
    await withFixture(async ({watcher, host}) => {
        await until(registered(watcher), 'the item registered');
        const properties = await getAll(host);
        const expected = {
            AttentionIconName: ['s', ''],
            AttentionIconPixmap: ['a(iiay)', []],
            AttentionMovieName: ['s', ''],
            Category: ['s', 'ApplicationStatus'],
            IconName: ['s', IDLE_ICON],
            IconPixmap: ['a(iiay)', []],
            IconThemePath: ['s', ICON_THEME_PATH],
            Id: ['s', 'meeting-scribe'],
            ItemIsMenu: ['b', true],
            Menu: ['o', '/MenuBar'],
            OverlayIconName: ['s', ''],
            OverlayIconPixmap: ['a(iiay)', []],
            Status: ['s', 'Active'],
            Title: ['s', 'Meeting Scribe'],
            ToolTip: ['(sa(iiay)ss)', ['', [], 'Meeting Scribe', IDLE_TOOLTIP]],
            WindowId: ['i', 0],
        };
        assertDeepEqual(Object.keys(properties).sort(), Object.keys(expected), 'property names: ');
        for (const [name, [signature, value]] of Object.entries(expected)) {
            const actual = properties[name];
            assertDeepEqual([actual.get_type_string(), actual.recursiveUnpack()], [signature, value],
                `${name}: `);
        }
    });
});

test('Activate, SecondaryActivate, ContextMenu and Scroll do nothing: a click never records (AC-3)', async () => {
    await withFixture(async ({watcher, connection, host, logged}) => {
        await until(registered(watcher), 'the item registered');
        const signals = await subscribeSignals(host, connection.get_unique_name());
        const before = plain(await getAll(host));
        for (const [method, signature, args] of [
            ['Activate', '(ii)', [10, 20]],
            ['SecondaryActivate', '(ii)', [10, 20]],
            ['ContextMenu', '(ii)', [10, 20]],
            ['Scroll', '(is)', [120, 'vertical']],
        ]) {
            const reply = await callItem(host, method, signature, args);
            assertEqual(reply.get_type_string(), '()', `${method} reply: `);
        }
        // Any signal the calls caused was sent before their replies, so it has arrived by now.
        assertDeepEqual(plain(await getAll(host)), before, 'properties after the calls: ');
        signals.stop();
        assertDeepEqual(signals.received, [], 'signals: ');
        assertEqual(watcher.registrations.length, 1, 'registrations: ');
        assertDeepEqual(logged, [], 'log lines: ');
    });
});

test('update emits NewIcon and NewToolTip only when that value changed', async () => {
    await withFixture(async ({item, watcher, connection, host}) => {
        await until(registered(watcher), 'the item registered');
        const signals = await subscribeSignals(host, connection.get_unique_name());
        const read = async () => {
            const properties = plain(await getAll(host));
            return [properties.IconName, properties.ToolTip[3]];
        };

        item.update({iconName: IDLE_ICON, tooltip: IDLE_TOOLTIP}); // nothing changed
        item.update({iconName: RECORDING_ICON, tooltip: IDLE_TOOLTIP});
        await until(() => signals.received.length >= 1, 'NewIcon');
        assertDeepEqual(await read(), [RECORDING_ICON, IDLE_TOOLTIP], 'after the icon change: ');

        item.update({tooltip: RECORDING_TOOLTIP}); // the icon is kept
        await until(() => signals.received.length >= 2, 'NewToolTip');
        assertDeepEqual(await read(), [RECORDING_ICON, RECORDING_TOOLTIP],
            'after the tooltip change: ');

        item.update({iconName: RECORDING_ICON, tooltip: RECORDING_TOOLTIP}); // nothing changed
        item.update({iconName: IDLE_ICON, tooltip: IDLE_TOOLTIP});
        await until(() => signals.received.length >= 4, 'NewIcon and NewToolTip');
        assertDeepEqual(await read(), [IDLE_ICON, IDLE_TOOLTIP], 'after both changed: ');

        signals.stop();
        assertDeepEqual(signals.received, ['NewIcon', 'NewToolTip', 'NewIcon', 'NewToolTip']);
    });
});

test('the item registers again with a restarted watcher (AC-8)', async () => {
    await withFixture(async ({watcher, newWatcher, watcherGone, connection}) => {
        await until(registered(watcher), 'the item registered');
        const expected = {
            service: ITEM_BUS_NAME,
            sender: connection.get_unique_name(),
            ownedAtRegistration: true,
        };

        watcher.stop(); // plasmashell exits; a new process takes the name
        await watcherGone();
        const restarted = await newWatcher();
        await until(registered(restarted), 'registered with the new watcher');
        assertDeepEqual(restarted.registrations, [expected], 'new owner: ');

        restarted.stop(); // the same connection releases and takes the name again
        await watcherGone();
        await restarted.start();
        await until(() => restarted.registrations.length === 2, 'registered after the restart');
        await sleep(QUIET_MS);
        assertDeepEqual(restarted.registrations, [expected, expected], 'same owner: ');
        assertEqual(watcher.registrations.length, 1, 'first watcher: ');
    });
});

test('without a watcher the item logs one line, waits, and registers when one appears', async () => {
    await withFixture(async ({newWatcher, connection, host, logged}) => {
        await until(() => logged.length > 0, 'a log line');
        const ownsName = async () =>
            await nameOwner(host, ITEM_BUS_NAME) === connection.get_unique_name();
        await until(ownsName, 'the item owns its name');
        await sleep(QUIET_MS);
        assertEqual(logged.length, 1, 'log lines while waiting: ');
        assertEqual(logged[0].includes(WATCHER_NAME), true,
            `log line names the watcher: ${logged[0]}: `);

        const watcher = await newWatcher();
        await until(registered(watcher), 'registered with the watcher that appeared');
        assertEqual(watcher.registrations[0].service, ITEM_BUS_NAME, 'service: ');
        assertEqual(watcher.registrations[0].ownedAtRegistration, true, 'owned: ');
        assertEqual(logged.length, 1, 'log lines after registering: ');
    }, {watcher: false});
});

test('a failed registration is retried with the same watcher and logs one line', async () => {
    await withFixture(async ({watcher, logged}) => {
        await until(registered(watcher), 'registered after the watcher stopped failing');
        assertEqual(watcher.failed, 2, 'failed attempts: ');
        assertEqual(watcher.registrations.length, 1, 'successful registrations: ');
        assertEqual(watcher.registrations[0].service, ITEM_BUS_NAME, 'service: ');
        assertEqual(logged.length, 1, `log lines: ${JSON.stringify(logged)}: `);
        assertEqual(logged[0].includes('could not register'), true, `log line: ${logged[0]}: `);
    }, {failRegistrations: 2, itemOptions: {retryDelayMs: 50}});
});

test('an item that cannot own its bus name logs one line and never registers a name it does not own', async () => {
    const takeName = async bus => {
        const [reply] = (await callBusDaemon(await bus.connect(), 'RequestName',
            new GLib.Variant('(su)', [ITEM_BUS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE]),
            '(u)')).deepUnpack();
        assertEqual(reply, 1, 'the other connection is the primary owner: ');
    };
    await withFixture(async ({watcher, logged}) => {
        await until(() => logged.length > 0, 'a log line');
        await sleep(QUIET_MS);
        assertEqual(logged.length, 1, 'log lines: ');
        assertEqual(logged[0].includes(ITEM_BUS_NAME), true,
            `log line names the bus name: ${logged[0]}: `);
        assertDeepEqual(watcher.registrations, [], 'registrations: ');
    }, {beforeStart: takeName});
});

test('destroy releases the name, unexports the item and stops watching; it is idempotent', async () => {
    await withFixture(async ({item, watcher, newWatcher, watcherGone, connection, host}) => {
        await until(registered(watcher), 'the item registered');
        item.destroy();
        item.destroy();
        await until(async () => await nameOwner(host, ITEM_BUS_NAME) === '',
            'the name was released');

        const error = await getAll(host, connection.get_unique_name()).then(() => null, e => e);
        assertEqual(error === null, false, 'GetAll on the destroyed item: ');
        const unexported = [
            'org.freedesktop.DBus.Error.UnknownMethod',
            'org.freedesktop.DBus.Error.UnknownObject',
        ];
        assertEqual(unexported.includes(Gio.DBusError.get_remote_error(error)), true,
            `error: ${error?.message}: `);

        watcher.stop();
        await watcherGone();
        const restarted = await newWatcher();
        await sleep(QUIET_MS);
        assertDeepEqual(restarted.registrations, [], 'registrations after destroy: ');
    });
});
