// The whole tray, wired like the extension, exercised the way a tray host does: over D-Bus on a
// private bus, with the fake service, a fake StatusNotifierWatcher and a fake notification server.
// The fake service only changes its state: nothing opens an audio device.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {test, assertEqual, assertDeepEqual} from '../../gnome-extension/tests/harness.js';
import {
    PrivateBus, FakeRecordingService, FakeMeetingsService,
} from '../../gnome-extension/tests/privateBus.js';
import {
    INTERFACE_XML as MEETINGS_XML, SUMMARIES_XML,
} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/meetingsClient.js';
import {
    viewModel, errorMessage, launchFailureMessage, summaryOpenFailureMessage,
} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/indicatorModel.js';
import {FakeStatusNotifierWatcher} from './fakeStatusNotifierWatcher.js';
import {FakeNotifications} from './fakeNotifications.js';
import {TRAY_ICONS} from '../lib/trayModel.js';
import {Notifier} from '../lib/notifier.js';
import {Tray, SERVICE_BUS_NAME, SERVICE_OBJECT_PATH} from '../lib/tray.js';

Gio._promisify(Gio.DBusConnection.prototype, 'call', 'call_finish');

const ITEM_PATH = '/StatusNotifierItem';
const ITEM_INTERFACE = 'org.kde.StatusNotifierItem';
const MENU_INTERFACE = 'com.canonical.dbusmenu';
const ICON_THEME_PATH = '/nonexistent/meeting-scribe/icons';
// Synthetic summary: never a real meeting (AGENTS.md rule 2).
const SUMMARY_PATH = '/nonexistent/meetings/synthetic-meeting/summary.md';
const SUMMARY = {
    meeting_id: 'synthetic-meeting', started_at: '2026-01-05T10:00:00Z',
    generated_at: '2026-01-05T11:00:00Z', path: SUMMARY_PATH,
};
const CALL_TIMEOUT_MS = 1000;
const WAIT_LIMIT_MS = 3000; // below the harness timeout so the fixture can still clean up
const QUIET_MS = 100; // long enough for a stray call or signal to show up
const START_CLOCK_US = 1_000_000_000;

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

/** Counts the Start calls that reach the service. */
class CountingRecordingService extends FakeRecordingService {
    constructor(...args) {
        super(...args);
        this.startCalls = 0;
    }

    StartAsync(params, invocation) {
        this.startCalls++;
        super.StartAsync(params, invocation);
    }
}

/**
 * Recording1, Meetings1 and Summaries1 share the service's bus name and object path. Each fake
 * of the extension owns the name itself, and a second request from the same connection is
 * refused, so this one only exports its interfaces next to the recording fake's.
 */
class SharedNameMeetingsService extends FakeMeetingsService {
    async appear() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(MEETINGS_XML, this);
        this._impl.export(this._connection, this._objectPath);
        this._summaries = Gio.DBusExportedObject.wrapJSObject(SUMMARIES_XML, this);
        this._summaries.export(this._connection, this._objectPath);
    }
}

/** The controller's scheduler, ticked by hand (as in indicatorController.test.js). */
class ManualScheduler {
    constructor() {
        this.active = new Map();
        this._next = 1;
    }

    every(seconds, fn) {
        const handle = this._next++;
        this.active.set(handle, {seconds, fn});
        return handle;
    }

    cancel(handle) {
        this.active.delete(handle);
    }

    tick() {
        for (const {fn} of [...this.active.values()])
            fn();
    }
}

// Reads a dbusmenu child back as the extension's menu item kind and text.
function asMenuItem([id, properties]) {
    if (properties.type === 'separator')
        return {id, kind: 'separator'};
    return {
        id, kind: properties.enabled ? 'action' : 'status',
        text: properties.label.replaceAll('__', '_'),
    };
}

const withoutIds = items => items.map(({kind, text}) => (kind === 'separator' ? {kind} : {kind, text}));

/** What a tray host does: reads the registered item and its menu, and sends the user's input. */
function hostFor(connection, service, menuPath) {
    const call = async (path, iface, method, signature = null, args = []) => {
        const reply = await connection.call(service, path, iface, method,
            signature ? new GLib.Variant(signature, args) : null, null,
            Gio.DBusCallFlags.NO_AUTO_START, CALL_TIMEOUT_MS, null);
        return reply.recursiveUnpack();
    };
    const host = {
        async item() {
            const [properties] = await call(ITEM_PATH, 'org.freedesktop.DBus.Properties', 'GetAll',
                '(s)', [ITEM_INTERFACE]);
            return properties;
        },
        async tooltip() {
            return (await host.item()).ToolTip[3];
        },
        callItem(method, signature, args) {
            return call(ITEM_PATH, ITEM_INTERFACE, method, signature, args);
        },
        async layout() {
            const [revision, [, , children]] = await call(menuPath, MENU_INTERFACE, 'GetLayout',
                '(iias)', [0, -1, []]);
            return {revision, items: children.map(asMenuItem)};
        },
        async texts() {
            return (await host.layout()).items.map(item => item.text);
        },
        async showsText(predicate) {
            return (await host.texts()).some(text => text !== undefined && predicate(text));
        },
        async click(predicate) {
            const {items} = await host.layout();
            const item = items.find(entry => entry.kind === 'action' && predicate(entry.text));
            if (!item)
                throw new Error(`no action item matches in ${JSON.stringify(withoutIds(items))}`);
            await call(menuPath, MENU_INTERFACE, 'Event', '(isvu)',
                [item.id, 'clicked', new GLib.Variant('s', ''), 0]);
        },
        aboutToShow() {
            return call(menuPath, MENU_INTERFACE, 'AboutToShow', '(i)', [0]);
        },
    };
    return host;
}

/**
 * Private bus, the fakes, a started tray and a host connection; always cleaned up.
 *
 * @param {Function} body - receives the fixture
 * @param {object} [options]
 * @param {boolean} [options.service] - false: the service name has no owner
 * @param {object} [options.recording] - initial properties of the recording fake
 * @param {object|null} [options.lastSummary] - what LastSummary answers
 * @param {object} [options.launchers] - launchReview, launchPreferences, openSummary overrides
 */
async function withTray(body, {
    service = true, recording: recordingState = {}, lastSummary = null, launchers = {},
} = {}) {
    const bus = await PrivateBus.start();
    let tray = null;
    let recording = null;
    let meetings = null;
    let watcher = null;
    let notifications = null;
    try {
        watcher = new FakeStatusNotifierWatcher(await bus.connect());
        await watcher.start();
        notifications = new FakeNotifications(await bus.connect());
        await notifications.start();
        const serviceConnection = await bus.connect();
        recording = new CountingRecordingService(serviceConnection, SERVICE_BUS_NAME,
            SERVICE_OBJECT_PATH);
        Object.assign(recording, recordingState);
        meetings = new SharedNameMeetingsService(serviceConnection, SERVICE_BUS_NAME,
            SERVICE_OBJECT_PATH);
        meetings.lastSummary = lastSummary;
        if (service) {
            await meetings.appear(); // exported first: the name appears with every interface
            await recording.appear();
        }

        const connection = await bus.connect();
        const hostConnection = await bus.connect();
        const launched = [];
        const scheduler = new ManualScheduler();
        const clock = {now: START_CLOCK_US};
        const logged = [];
        tray = new Tray({
            connection,
            scheduler,
            iconThemePath: ICON_THEME_PATH,
            now: () => clock.now,
            logError: message => logged.push(message),
            launchReview: () => {
                launched.push(['review']);
                return true;
            },
            launchPreferences: () => {
                launched.push(['preferences']);
                return true;
            },
            openSummary: path => {
                launched.push(['summary', path]);
                return Promise.resolve();
            },
            ...launchers,
        });
        assertDeepEqual(await tray.start(), {started: true}, 'start: ');
        await until(() => watcher.registrations.length > 0, 'the item registered');
        const {service: itemService} = watcher.registrations[0];
        const menuPath = (await hostFor(hostConnection, itemService, '').item()).Menu;
        const host = hostFor(hostConnection, itemService, menuPath);
        await body({host, recording, meetings, notifications, scheduler, clock, launched, logged});
    } finally {
        tray?.destroy();
        recording?.vanish();
        meetings?.vanish();
        watcher?.stop();
        notifications?.stop();
        await bus.stop();
    }
}

const isStart = text => text === 'Start recording';

test('Activate, SecondaryActivate and ContextMenu make no Start call; clicking Start makes exactly one (AC-3)', async () => {
    await withTray(async ({host, recording}) => {
        await until(() => host.showsText(isStart), 'the idle menu');
        for (const method of ['Activate', 'SecondaryActivate', 'ContextMenu'])
            await host.callItem(method, '(ii)', [10, 20]);
        await sleep(QUIET_MS);
        assertEqual(recording.startCalls, 0, 'Start calls after the item methods: ');
        assertEqual(recording.State, 'IDLE', 'state after the item methods: ');

        await host.click(isStart);
        await until(() => recording.startCalls > 0, 'a Start call');
        await until(() => host.showsText(text => text === 'Stop recording'), 'the recording menu');
        await sleep(QUIET_MS);
        assertEqual(recording.startCalls, 1, 'Start calls after the click: ');
        assertEqual((await host.item()).IconName, TRAY_ICONS.recording, 'icon: ');
    });
});

test('recording for 754 s shows the red icon and the time in the tooltip and first row; a tick changes only the labels (AC-4)', async () => {
    await withTray(async ({host, scheduler, clock}) => {
        await until(async () => (await host.tooltip()).endsWith('00:12:34'), 'the elapsed time');
        const item = await host.item();
        assertEqual(item.IconName, TRAY_ICONS.recording, 'icon: ');
        assertEqual(item.ToolTip[3], 'Recording in progress · 00:12:34', 'tooltip: ');
        const before = await host.layout();
        assertEqual(before.items[0].text, 'Recording in progress (session-1) · 00:12:34',
            'first row: ');
        assertEqual(scheduler.active.size, 1, 'one tick while recording: ');

        clock.now += 1_000_000;
        scheduler.tick();
        const after = await host.layout();
        assertEqual(await host.tooltip(), 'Recording in progress · 00:12:35', 'tooltip after a tick: ');
        assertEqual(after.items[0].text, 'Recording in progress (session-1) · 00:12:35',
            'first row after a tick: ');
        assertEqual(after.revision, before.revision, 'layout revision: ');
        assertDeepEqual(after.items.map(({id}) => id), before.items.map(({id}) => id), 'ids: ');
        assertDeepEqual(withoutIds(after.items).slice(1), withoutIds(before.items).slice(1),
            'the other rows: ');
    }, {recording: {State: 'RECORDING', SessionId: 'session-1', ElapsedSeconds: 754}});
});

const isReview = text => text === 'Review last meeting';
const isSummary = text => text.startsWith('Open last summary (') && text.endsWith(')');
const isPreferences = text => text === 'Preferences…';

test('Review last meeting, Open last summary and Preferences call their launchers (AC-5)', async () => {
    await withTray(async ({host, launched, notifications}) => {
        await until(() => host.showsText(isSummary), 'the summary item');
        for (const predicate of [isReview, isSummary, isPreferences]) {
            const count = launched.length;
            await host.click(predicate);
            await until(() => launched.length > count, 'a launch');
        }
        assertDeepEqual(launched, [['review'], ['summary', SUMMARY_PATH], ['preferences']]);
        await sleep(QUIET_MS);
        assertDeepEqual(notifications.received, [], 'notifications: ');
    }, {lastSummary: SUMMARY});
});

test('a failed launch shows the extension\'s fixed failure text as a notification (AC-5)', async () => {
    const launchers = {
        launchReview: () => false,
        openSummary: () => Promise.reject(new Error(`cannot open ${SUMMARY_PATH}`)),
        launchPreferences: () => {
            throw new Error('no desktop entry');
        },
    };
    await withTray(async ({host, notifications}) => {
        await until(() => host.showsText(isSummary), 'the summary item');
        for (const predicate of [isReview, isSummary, isPreferences]) {
            const count = notifications.received.length;
            await host.click(predicate);
            await until(() => notifications.received.length > count, 'a notification');
        }
        assertDeepEqual(notifications.received.map(({summary, body}) => [summary, body]), [
            ['Meeting Scribe', launchFailureMessage()],
            ['Meeting Scribe', summaryOpenFailureMessage()],
            ['Meeting Scribe', launchFailureMessage()],
        ]);
    }, {lastSummary: SUMMARY, launchers});
});

test('a Start refused with RECORDING_ALREADY_ACTIVE sends the extension\'s error text as a notification (AC-6)', async () => {
    await withTray(async ({host, recording, notifications}) => {
        await until(() => host.showsText(isStart), 'the idle menu');
        recording.State = 'RECORDING'; // another client started one; the tray has not heard yet
        await host.click(isStart);
        await until(() => notifications.received.length > 0, 'a notification');
        await sleep(QUIET_MS);
        assertDeepEqual(notifications.received, [{
            appName: 'Meeting Scribe',
            replacesId: 0,
            appIcon: '',
            summary: 'Meeting Scribe',
            body: errorMessage('dev.meetingscribe.Error.RECORDING_ALREADY_ACTIVE'),
            actions: [],
            hints: [],
            expireTimeout: -1,
        }]);
        assertEqual(notifications.received[0].body, 'A recording is already in progress.');
    });
});

test('with the service name unowned the icon and the rows are the unavailable ones (AC-6)', async () => {
    await withTray(async ({host, recording}) => {
        const unavailable = viewModel({available: false});
        const item = await host.item();
        assertEqual(item.IconName, TRAY_ICONS.unavailable, 'icon: ');
        assertEqual(item.ToolTip[3], unavailable.tooltip, 'tooltip: ');
        assertDeepEqual(withoutIds((await host.layout()).items), withoutIds(unavailable.menuItems),
            'rows: ');
        assertEqual(recording.startCalls, 0, 'the tray never starts the service: ');
    }, {service: false});
});

test('AboutToShow on the menu root refreshes the last summary, as the extension does on menu open', async () => {
    await withTray(async ({host, meetings}) => {
        await until(() => host.showsText(isStart), 'the idle menu');
        await until(() => meetings.summaryCalls > 0, 'the first LastSummary call');
        await sleep(QUIET_MS);
        assertEqual(await host.showsText(isSummary), false, 'no summary yet: ');

        meetings.lastSummary = SUMMARY;
        await host.aboutToShow();
        await until(() => host.showsText(isSummary), 'the summary item after AboutToShow');
        assertEqual(meetings.summaryCalls, 2, 'LastSummary calls: ');
    });
});

test('the notifier resolves and logs one line without the text when no notification server runs', async () => {
    const bus = await PrivateBus.start();
    try {
        const logged = [];
        const notifier = new Notifier({
            connection: await bus.connect(),
            logError: message => logged.push(message),
        });
        const title = 'Meeting Scribe';
        const body = 'synthetic body text';
        assertEqual(await notifier.notify(title, body), undefined, 'result: ');
        assertEqual(logged.length, 1, 'log lines: ');
        assertEqual(logged[0].includes(body) || logged[0].includes(title), false,
            `the log line carries no notification text: ${logged[0]}: `);
    } finally {
        await bus.stop();
    }
});
