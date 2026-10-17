// The menu is tested through com.canonical.dbusmenu on a private bus: a second connection plays
// the tray host (Plasma's importer), so every assertion goes through the public D-Bus interface.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {test, assertEqual, assertDeepEqual} from '../../gnome-extension/tests/harness.js';
import {PrivateBus, FIXTURE_WAIT_MS} from '../../gnome-extension/tests/privateBus.js';
import {viewModel} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/indicatorModel.js';
import {DBusMenu} from '../lib/dbusMenu.js';

Gio._promisify(Gio.DBusConnection.prototype, 'call', 'call_finish');

const INTERFACE = 'com.canonical.dbusmenu';
const OBJECT_PATH = '/MenuBar';
const INVALID_ARGS = 'org.freedesktop.DBus.Error.InvalidArgs';
const POLL_MS = 10;

const status = text => ({kind: 'status', text});
const action = (text, id) => ({kind: 'action', text, action: id});
const SEPARATOR = {kind: 'separator'};

const recordingItems = elapsed => [
    status(`Recording in progress (session_1) · ${elapsed}`),
    SEPARATOR,
    action('Stop recording', 'stop'),
    SEPARATOR,
    action('Preferences…', 'preferences'),
];
const RECORDING_ITEMS = recordingItems('00:12:34');

const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));

// Polls until `condition()` holds; bounded below the harness timeout so the fixture still cleans up.
async function waitFor(condition, description) {
    for (let waited = 0; !condition(); waited += POLL_MS) {
        if (waited >= FIXTURE_WAIT_MS)
            throw new Error(`condition never held: ${description}`);
        await sleep(POLL_MS);
    }
}

// The host's view of the menu: a D-Bus call to the exporter, unpacked to plain values.
function callerFor(host, sender) {
    return async (method, signature = null, args = [], iface = INTERFACE) => {
        const reply = await host.call(sender, OBJECT_PATH, iface, method,
            signature ? new GLib.Variant(signature, args) : null, null,
            Gio.DBusCallFlags.NO_AUTO_START, FIXTURE_WAIT_MS, null);
        return reply.recursiveUnpack();
    };
}

async function remoteError(promise) {
    try {
        await promise;
    } catch (e) {
        return Gio.DBusError.get_remote_error(e);
    }
    throw new Error('expected the call to fail');
}

// A round trip to the daemon: the host's AddMatch rules are in place once it returns.
async function syncWithBus(host) {
    await host.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        'GetId', null, null, Gio.DBusCallFlags.NONE, FIXTURE_WAIT_MS, null);
}

/** Private bus, an exported menu and a host connection that records the menu's signals. */
async function withMenu(body, items = RECORDING_ITEMS) {
    const bus = await PrivateBus.start();
    let menu = null;
    let host = null;
    let subscription = 0;
    try {
        const exporter = await bus.connect();
        host = await bus.connect();
        const hooks = {activated: [], opened: 0};
        menu = new DBusMenu({
            connection: exporter,
            onActivate: id => hooks.activated.push(id),
            onAboutToShow: () => hooks.opened++,
        });
        menu.setItems(items);
        menu.export();
        const sender = exporter.get_unique_name();
        const signals = [];
        subscription = host.signal_subscribe(sender, INTERFACE, null, OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE, (_connection, _sender, _path, _iface, name, parameters) =>
                signals.push({name, args: parameters.recursiveUnpack()}));
        await syncWithBus(host);
        const call = callerFor(host, sender);
        const layout = async () => {
            const [revision, [id, properties, children]] =
                await call('GetLayout', '(iias)', [0, -1, []]);
            return {revision, id, properties, children};
        };
        await body({menu, hooks, signals, call, layout});
    } finally {
        if (subscription)
            host.signal_unsubscribe(subscription);
        menu?.destroy();
        await bus.stop();
    }
}

// Reads a dbusmenu child back as the extension's menu item kind and text.
function asMenuItem([_id, properties]) {
    if (properties.type === 'separator')
        return {kind: 'separator'};
    return {kind: properties.enabled ? 'action' : 'status', text: properties.label.replaceAll('__', '_')};
}

test('GetLayout returns the root and one child per item, in order and mapped by kind', async () => {
    await withMenu(async ({layout}) => {
        const {id, properties, children} = await layout();
        assertEqual(id, 0, 'root id: ');
        assertDeepEqual(properties, {'children-display': 'submenu'}, 'root: ');
        assertDeepEqual(children, [
            [1, {label: 'Recording in progress (session__1) · 00:12:34', enabled: false}, []],
            [2, {type: 'separator'}, []],
            [3, {label: 'Stop recording', enabled: true}, []],
            [4, {type: 'separator'}, []],
            [5, {label: 'Preferences…', enabled: true}, []],
        ]);
    });
});

test('the layout has the texts, order and kinds of the viewModel menu in every state', async () => {
    const idle = {available: true, busy: false, state: 'IDLE', sessionId: '', elapsedSeconds: 0,
        failureReason: ''};
    const states = {
        unavailable: {available: false},
        idle,
        recording: {...idle, state: 'RECORDING', sessionId: 'session_1', elapsedSeconds: 754},
        processing: {...idle, processing: {available: true, activeState: 'TRANSCRIBING', queuedCount: 2}},
        failed: {...idle, state: 'FAILED', failureReason: 'capture stopped'},
        processed: {...idle, state: 'STOPPED', sessionId: 'session_1', processing: {
            available: true, activeState: 'IDLE', queuedCount: 0,
            last: {meetingId: 'm1', state: 'DONE', speakerCount: 2},
            lastSummary: {startedAt: '2026-10-16T09:30:00Z'},
        }},
    };
    await withMenu(async ({menu, layout}) => {
        for (const [name, input] of Object.entries(states)) {
            const {menuItems} = viewModel(input);
            menu.setItems(menuItems);
            const {children} = await layout();
            assertDeepEqual(children.map(asMenuItem),
                menuItems.map(({kind, text}) => (kind === 'separator' ? {kind} : {kind, text})),
                `${name}: `);
        }
    });
});

test('every underscore in a label is escaped as a double underscore', async () => {
    await withMenu(async ({layout}) => {
        const {children} = await layout();
        assertEqual(children[0][1].label, 'Last recording: a__b__c');
        assertEqual(children[1][1].label, 'Open __mnemonic__');
    }, [status('Last recording: a_b_c'), action('Open _mnemonic_', 'review')]);
});

test('the menu properties are version 3, left to right, normal and no icon theme path', async () => {
    await withMenu(async ({call}) => {
        const [properties] = await call('GetAll', '(s)', [INTERFACE], 'org.freedesktop.DBus.Properties');
        assertDeepEqual(properties,
            {Version: 3, TextDirection: 'ltr', Status: 'normal', IconThemePath: []});
    });
});

test('GetLayout honours the recursion depth, the property names and the parent id', async () => {
    await withMenu(async ({call}) => {
        const [, root] = await call('GetLayout', '(iias)', [0, 0, []]);
        assertDeepEqual(root, [0, {'children-display': 'submenu'}, []], 'depth 0: ');
        const [, filtered] = await call('GetLayout', '(iias)', [0, 1, ['label']]);
        assertDeepEqual(filtered[2].map(([id, properties]) => [id, properties]),
            [[1, {label: 'Recording in progress (session__1) · 00:12:34'}], [2, {}],
                [3, {label: 'Stop recording'}], [4, {}], [5, {label: 'Preferences…'}]],
            'only label: ');
        const [, item] = await call('GetLayout', '(iias)', [3, -1, []]);
        assertDeepEqual(item, [3, {label: 'Stop recording', enabled: true}, []], 'one item: ');
        assertEqual(await remoteError(call('GetLayout', '(iias)', [99, -1, []])), INVALID_ARGS);
    });
});

test('a label change keeps the ids and the revision and sends only that label', async () => {
    await withMenu(async ({menu, signals, layout}) => {
        const before = await layout();
        menu.setItems(recordingItems('00:12:35'));
        await waitFor(() => signals.length > 0, 'ItemsPropertiesUpdated');
        const after = await layout(); // a reply comes after every signal sent before it
        assertDeepEqual(signals, [{name: 'ItemsPropertiesUpdated', args: [
            [[1, {label: 'Recording in progress (session__1) · 00:12:35'}]], []]}]);
        assertEqual(after.revision, before.revision, 'revision: ');
        assertDeepEqual(after.children.map(([id]) => id), [1, 2, 3, 4, 5], 'ids: ');
        assertEqual(after.children[0][1].label, 'Recording in progress (session__1) · 00:12:35');
    });
});

test('the same items again send no signal', async () => {
    await withMenu(async ({menu, signals, layout}) => {
        const before = await layout();
        menu.setItems(recordingItems('00:12:34'));
        const after = await layout();
        assertDeepEqual(signals, []);
        assertEqual(after.revision, before.revision);
    });
});

test('a change of kinds increments the revision, emits LayoutUpdated and never reuses an id', async () => {
    await withMenu(async ({menu, signals, layout}) => {
        const before = await layout();
        menu.setItems([action('Start recording', 'start'), SEPARATOR, action('Preferences…', 'preferences')]);
        await waitFor(() => signals.length > 0, 'LayoutUpdated');
        const after = await layout();
        assertDeepEqual(signals, [{name: 'LayoutUpdated', args: [before.revision + 1, 0]}]);
        assertEqual(after.revision, before.revision + 1, 'revision: ');
        assertDeepEqual(after.children, [
            [6, {label: 'Start recording', enabled: true}, []],
            [7, {type: 'separator'}, []],
            [8, {label: 'Preferences…', enabled: true}, []],
        ]);
    });
});

test('a change of action with the same kinds is a layout change', async () => {
    await withMenu(async ({menu, signals, layout}) => {
        const before = await layout();
        menu.setItems([action('Start recording', 'start')]);
        await waitFor(() => signals.length > 0, 'first LayoutUpdated');
        menu.setItems([action('Start recording', 'review')]);
        await waitFor(() => signals.length > 1, 'second LayoutUpdated');
        assertDeepEqual(signals.map(({name, args}) => [name, ...args]), [
            ['LayoutUpdated', before.revision + 1, 0],
            ['LayoutUpdated', before.revision + 2, 0],
        ]);
    });
});

test('clicked on an action item calls onActivate with its action', async () => {
    await withMenu(async ({call, hooks}) => {
        await call('Event', '(isvu)', [3, 'clicked', new GLib.Variant('s', ''), 0]);
        assertDeepEqual(hooks.activated, ['stop']);
        await call('Event', '(isvu)', [5, 'clicked', new GLib.Variant('i', 0), 42]);
        assertDeepEqual(hooks.activated, ['stop', 'preferences']);
    });
});

test('events on status, separator, root or unknown ids, and other events, do nothing', async () => {
    await withMenu(async ({call, hooks}) => {
        const empty = new GLib.Variant('s', '');
        for (const id of [0, 1, 2, 99])
            await call('Event', '(isvu)', [id, 'clicked', empty, 0]);
        await call('Event', '(isvu)', [3, 'hovered', empty, 0]);
        await call('Event', '(isvu)', [3, 'opened', empty, 0]);
        assertDeepEqual(hooks.activated, []);
    });
});

test('EventGroup handles every event and reports the unknown ids', async () => {
    await withMenu(async ({call, hooks}) => {
        const empty = new GLib.Variant('s', '');
        const [idErrors] = await call('EventGroup', '(a(isvu))',
            [[[3, 'clicked', empty, 0], [99, 'clicked', empty, 0], [5, 'clicked', empty, 0]]]);
        assertDeepEqual(idErrors, [99]);
        assertDeepEqual(hooks.activated, ['stop', 'preferences']);
    });
});

test('AboutToShow on the root calls the hook and asks for no update', async () => {
    await withMenu(async ({call, hooks}) => {
        assertDeepEqual(await call('AboutToShow', '(i)', [0]), [false], 'root: ');
        assertEqual(hooks.opened, 1);
        assertDeepEqual(await call('AboutToShow', '(i)', [3]), [false], 'item: ');
        assertEqual(hooks.opened, 1, 'an item is not the menu opening: ');
    });
});

test('AboutToShowGroup calls the hook for the root and reports the unknown ids', async () => {
    await withMenu(async ({call, hooks}) => {
        assertDeepEqual(await call('AboutToShowGroup', '(ai)', [[0, 3, 99]]), [[], [99]]);
        assertEqual(hooks.opened, 1);
        assertDeepEqual(await call('AboutToShowGroup', '(ai)', [[3]]), [[], []]);
        assertEqual(hooks.opened, 1);
    });
});

test('GetGroupProperties returns the asked items and properties, all items for no ids', async () => {
    await withMenu(async ({call}) => {
        const [some] = await call('GetGroupProperties', '(aias)', [[2, 3, 99], []]);
        assertDeepEqual(some, [[2, {type: 'separator'}], [3, {label: 'Stop recording', enabled: true}]]);
        const [labels] = await call('GetGroupProperties', '(aias)', [[1, 3], ['enabled']]);
        assertDeepEqual(labels, [[1, {enabled: false}], [3, {enabled: true}]]);
        const [all] = await call('GetGroupProperties', '(aias)', [[], []]);
        assertDeepEqual(all.map(([id]) => id), [0, 1, 2, 3, 4, 5]);
    });
});

test('GetProperty returns one property and fails for an unknown item or name', async () => {
    await withMenu(async ({call}) => {
        assertDeepEqual(await call('GetProperty', '(is)', [3, 'label']), ['Stop recording']);
        assertDeepEqual(await call('GetProperty', '(is)', [1, 'enabled']), [false]);
        assertDeepEqual(await call('GetProperty', '(is)', [0, 'children-display']), ['submenu']);
        assertEqual(await remoteError(call('GetProperty', '(is)', [99, 'label'])), INVALID_ARGS);
        assertEqual(await remoteError(call('GetProperty', '(is)', [2, 'label'])), INVALID_ARGS);
    });
});

test('export twice is harmless; destroy unexports, is idempotent and ends every signal', async () => {
    await withMenu(async ({menu, call, signals}) => {
        menu.export();
        await call('GetLayout', '(iias)', [0, -1, []]);
        menu.destroy();
        menu.destroy();
        assertEqual(await remoteError(call('GetLayout', '(iias)', [0, -1, []])),
            'org.freedesktop.DBus.Error.UnknownMethod');
        menu.setItems([action('Start recording', 'start')]);
        menu.export();
        assertEqual(await remoteError(call('GetLayout', '(iias)', [0, -1, []])),
            'org.freedesktop.DBus.Error.UnknownMethod', 'export after destroy: ');
        assertDeepEqual(signals, []);
    });
});
