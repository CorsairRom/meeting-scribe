// One tray per session and none on GNOME Shell (AC-7). The in-process tests drive lib/tray.js;
// the entry tests run `gjs -m desktop-tray/main.js` as a child process whose session bus is the
// private bus (DBUS_SESSION_BUS_ADDRESS), never the user's.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {test, assertEqual, assertDeepEqual, registerCleanup} from '../../gnome-extension/tests/harness.js';
import {PrivateBus, withTimeout} from '../../gnome-extension/tests/privateBus.js';
import {FakeStatusNotifierWatcher} from './fakeStatusNotifierWatcher.js';
import {GNOME_SHELL_BUS_NAME, TRAY_BUS_NAME, Tray} from '../lib/tray.js';

Gio._promisify(Gio.DBusConnection.prototype, 'call', 'call_finish');
Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async', 'communicate_utf8_finish');

const ITEM_PATH = '/StatusNotifierItem';
const ITEM_INTERFACE = 'org.kde.StatusNotifierItem';
const MENU_PATH = '/MenuBar';
const MENU_INTERFACE = 'com.canonical.dbusmenu';
const CALL_TIMEOUT_MS = 1000;
const WAIT_LIMIT_MS = 3000; // below the harness timeout so the fixture can still clean up
const QUIET_MS = 100; // long enough for a stray registration to show up
const SIGINT = 2;
const SIGTERM = 15;
const MAIN_JS = Gio.File.new_for_uri(import.meta.url).get_parent().get_parent()
    .get_child('main.js').get_path();
const UNEXPORTED = [
    'org.freedesktop.DBus.Error.UnknownMethod',
    'org.freedesktop.DBus.Error.UnknownObject',
];

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

async function callBusDaemon(connection, method, signature, args) {
    const reply = await connection.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', method, new GLib.Variant(signature, args), null,
        Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, null);
    return reply.deepUnpack()[0];
}

const hasOwner = (connection, name) => callBusDaemon(connection, 'NameHasOwner', '(s)', [name]);

/** Another client takes `name`, as GNOME Shell or a first tray would. */
async function takeName(bus, name) {
    const connection = await bus.connect();
    const reply = await callBusDaemon(connection, 'RequestName', '(su)',
        [name, Gio.BusNameOwnerFlags.DO_NOT_QUEUE]);
    assertEqual(reply, 1, `the other client is the primary owner of ${name}: `);
    return connection;
}

/** Resolves with the remote error name of a call that must fail. */
async function remoteError(promise) {
    try {
        await promise;
    } catch (e) {
        return Gio.DBusError.get_remote_error(e);
    }
    throw new Error('expected the call to fail');
}

function getItem(connection, destination) {
    return connection.call(destination, ITEM_PATH, 'org.freedesktop.DBus.Properties', 'GetAll',
        new GLib.Variant('(s)', [ITEM_INTERFACE]), null, Gio.DBusCallFlags.NO_AUTO_START,
        CALL_TIMEOUT_MS, null);
}

function getLayout(connection, destination) {
    return connection.call(destination, MENU_PATH, MENU_INTERFACE, 'GetLayout',
        new GLib.Variant('(iias)', [0, -1, []]), null, Gio.DBusCallFlags.NO_AUTO_START,
        CALL_TIMEOUT_MS, null);
}

/** Private bus with a fake watcher and a host connection; always cleaned up. */
async function withBus(body) {
    const bus = await PrivateBus.start();
    const trays = [];
    let watcher = null;
    try {
        watcher = new FakeStatusNotifierWatcher(await bus.connect());
        await watcher.start();
        const host = await bus.connect();
        const newTray = async () => {
            const connection = await bus.connect();
            const tray = new Tray({
                connection,
                scheduler: {every: () => 0, cancel: () => {}},
                logError: () => {},
            });
            trays.push(tray);
            return {tray, connection};
        };
        await body({bus, watcher, host, newTray});
    } finally {
        trays.forEach(tray => tray.destroy());
        watcher?.stop();
        await bus.stop();
    }
}

test('a second tray does not start while dev.meetingscribe.Tray is owned and registers no item (AC-7)', async () => {
    await withBus(async ({watcher, host, newTray}) => {
        const first = await newTray();
        assertDeepEqual(await first.tray.start(), {started: true}, 'first: ');
        await until(() => watcher.registrations.length > 0, 'the first tray registered');

        const second = await newTray();
        assertDeepEqual(await second.tray.start(), {started: false, reason: 'already-running'},
            'second: ');
        await sleep(QUIET_MS);
        assertDeepEqual(watcher.registrations.map(({sender}) => sender),
            [first.connection.get_unique_name()], 'registrations: ');
        const destination = second.connection.get_unique_name();
        assertEqual(UNEXPORTED.includes(await remoteError(getItem(host, destination))), true,
            'the second tray exports no item: ');
        assertEqual(UNEXPORTED.includes(await remoteError(getLayout(host, destination))), true,
            'the second tray exports no menu: ');
    });
});

test('the tray does not start while org.gnome.Shell is owned and registers no item (AC-7)', async () => {
    await withBus(async ({bus, watcher, host, newTray}) => {
        await takeName(bus, GNOME_SHELL_BUS_NAME);
        const {tray, connection} = await newTray();
        assertDeepEqual(await tray.start(), {started: false, reason: 'gnome-shell'});
        await sleep(QUIET_MS);
        assertDeepEqual(watcher.registrations, [], 'registrations: ');
        assertEqual(await hasOwner(host, TRAY_BUS_NAME), false, 'the tray name was never taken: ');
        assertEqual(UNEXPORTED.includes(await remoteError(
            getItem(host, connection.get_unique_name()))), true, 'no item: ');
    });
});

test('destroy releases dev.meetingscribe.Tray and unexports the item and the menu; it is idempotent', async () => {
    await withBus(async ({watcher, host, newTray}) => {
        const {tray, connection} = await newTray();
        assertDeepEqual(await tray.start(), {started: true});
        await until(() => watcher.registrations.length > 0, 'the tray registered');
        const destination = connection.get_unique_name();
        assertEqual(await hasOwner(host, TRAY_BUS_NAME), true, 'owned while running: ');
        await getItem(host, destination);
        await getLayout(host, destination);

        tray.destroy();
        tray.destroy();
        await until(async () => !await hasOwner(host, TRAY_BUS_NAME), 'the tray name released');
        assertEqual(UNEXPORTED.includes(await remoteError(getItem(host, destination))), true,
            'item: ');
        assertEqual(UNEXPORTED.includes(await remoteError(getLayout(host, destination))), true,
            'menu: ');
    });
});

test('destroy during start ends it as stopped, with no item and no name (a signal at startup)', async () => {
    await withBus(async ({watcher, host, newTray}) => {
        const {tray} = await newTray();
        const starting = tray.start();
        tray.destroy();
        assertDeepEqual(await starting, {started: false, reason: 'stopped'});
        await sleep(QUIET_MS);
        assertDeepEqual(watcher.registrations, [], 'registrations: ');
        assertEqual(await hasOwner(host, TRAY_BUS_NAME), false, 'the tray name: ');
    });
});

/** Run `gjs -m desktop-tray/main.js` on the private bus; killed by PID after the test. */
function spawnMain(bus) {
    const launcher = new Gio.SubprocessLauncher({
        flags: Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE,
    });
    launcher.setenv('DBUS_SESSION_BUS_ADDRESS', bus.address, true);
    const child = launcher.spawnv(['gjs', '-m', MAIN_JS]);
    registerCleanup(() => child.force_exit());
    return child;
}

/** Wait (bounded) for the child to exit; resolves with its exit code and stderr. */
async function exitOf(child) {
    const cancellable = new Gio.Cancellable();
    // The promisified call resolves with [stdout, stderr].
    const [, stderr] = await withTimeout(child.communicate_utf8_async(null, cancellable),
        'main.js exit', WAIT_LIMIT_MS, () => cancellable.cancel());
    assertEqual(child.get_if_exited(), true, `main.js exited normally (stderr: ${stderr}): `);
    return {code: child.get_exit_status(), stderr};
}

for (const [name, what] of [[GNOME_SHELL_BUS_NAME, 'GNOME Shell'], [TRAY_BUS_NAME, 'another tray']]) {
    test(`main.js exits 0 without registering an item while ${what} owns ${name} (AC-7)`, async () => {
        await withBus(async ({bus, watcher}) => {
            await takeName(bus, name);
            const {code, stderr} = await exitOf(spawnMain(bus));
            assertEqual(code, 0, `exit code (stderr: ${stderr}): `);
            assertDeepEqual(watcher.registrations, [], 'registrations: ');
        });
    });
}

for (const [signal, signalName] of [[SIGTERM, 'SIGTERM'], [SIGINT, 'SIGINT']]) {
    test(`main.js registers its item, then on ${signalName} releases its name and exits 0`, async () => {
        await withBus(async ({bus, watcher, host}) => {
            const child = spawnMain(bus);
            await until(() => watcher.registrations.length > 0, 'main.js registered its item');
            assertEqual(watcher.registrations[0].service.endsWith(`-${child.get_identifier()}-1`),
                true, `the item belongs to main.js: ${watcher.registrations[0].service}: `);
            assertEqual(await hasOwner(host, TRAY_BUS_NAME), true, 'the tray name is owned: ');

            child.send_signal(signal);
            const {code, stderr} = await exitOf(child);
            assertEqual(code, 0, `exit code (stderr: ${stderr}): `);
            assertEqual(await hasOwner(host, TRAY_BUS_NAME), false, 'the tray name is released: ');
            assertEqual(watcher.registrations.length, 1, 'registrations: ');
        });
    });
}
