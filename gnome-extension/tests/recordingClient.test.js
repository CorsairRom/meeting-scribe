import GLib from 'gi://GLib';

import {test, assertEqual, assertDeepEqual} from './harness.js';
import {RecordingClient, getProperty as defaultGetProperty} from '../meeting-scribe@meetingscribe.dev/lib/recordingClient.js';
import {PrivateBus, FakeRecordingService} from './privateBus.js';

const BUS_NAME = 'dev.meetingscribe.Service';
const OBJECT_PATH = '/dev/meetingscribe/Service';

const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));

const WAIT_LIMIT_MS = 3000; // below the harness timeout so the fixture can still clean up

// Resolves when `condition()` holds, re-checked after every onChange; rejects on timeout so
// a failing test never leaves the private bus running.
function until(changes, condition, description) {
    return new Promise((resolve, reject) => {
        let timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, WAIT_LIMIT_MS, () => {
            timer = 0;
            changes.listeners.splice(changes.listeners.indexOf(check), 1);
            reject(new Error(`condition never held: ${description ?? condition}`));
            return GLib.SOURCE_REMOVE;
        });
        function check() {
            if (!condition())
                return;
            changes.listeners.splice(changes.listeners.indexOf(check), 1);
            if (timer)
                GLib.source_remove(timer);
            resolve();
        }
        changes.listeners.push(check);
        check();
    });
}

/** Private bus + fake service + client with a controllable clock; always cleaned up. */
async function withFixture(body, {appear = true, clientOptions = {}} = {}) {
    const bus = await PrivateBus.start();
    let client = null;
    let fake = null;
    try {
        const changes = {snapshots: [], listeners: []};
        const clock = {now: 1000 * 1_000_000};
        fake = new FakeRecordingService(await bus.connect(), BUS_NAME, OBJECT_PATH);
        if (appear)
            await fake.appear();
        client = new RecordingClient({
            connection: await bus.connect(),
            busName: BUS_NAME,
            objectPath: OBJECT_PATH,
            onChange: snapshot => {
                changes.snapshots.push(snapshot);
                changes.listeners.slice().forEach(listener => listener());
            },
            now: () => clock.now,
            ...clientOptions,
        });
        await body({bus, client, fake, changes, clock, until: c => until(changes, c)});
    } finally {
        client?.destroy();
        fake?.vanish();
        await bus.stop();
    }
}

const seconds = n => n * 1_000_000;

test('construction is side effect free: nothing is reported before connect()', async () => {
    await withFixture(async ({client, changes}) => {
        assertEqual(client.snapshot.available, false);
        await sleep(50);
        assertEqual(changes.snapshots.length, 0);
    });
});

test('service absent: available is false and nothing is started', async () => {
    await withFixture(async ({client, changes}) => {
        client.connect();
        await sleep(100);
        assertEqual(client.snapshot.available, false);
        assertEqual(changes.snapshots.filter(s => s.available).length, 0);
    }, {appear: false});
});

test('service appears: available with its state', async () => {
    await withFixture(async ({client, fake, until}) => {
        fake.State = 'STOPPED';
        fake.SessionId = 'old-session';
        client.connect();
        await until(() => client.snapshot.available);
        const {state, sessionId, busy, failureReason} = client.snapshot;
        assertDeepEqual({state, sessionId, busy, failureReason},
            {state: 'STOPPED', sessionId: 'old-session', busy: false, failureReason: ''});
    });
});

test('service appears later: available flips to true', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        assertEqual(client.snapshot.available, false);
        await fake.appear();
        await until(() => client.snapshot.available);
        assertEqual(client.snapshot.state, 'IDLE');
    }, {appear: false});
});

test('start() sets busy, then RECORDING with the session id and a locally computed elapsed', async () => {
    await withFixture(async ({client, changes, clock, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        const pending = client.start();
        assertEqual(client.snapshot.busy, true);
        assertEqual(await pending, 'session-1');
        assertEqual(client.snapshot.busy, false);
        await until(() => client.snapshot.state === 'RECORDING');
        assertEqual(client.snapshot.sessionId, 'session-1');
        assertEqual(changes.snapshots.some(s => s.busy), true);
        clock.now += seconds(7);
        assertEqual(Math.floor(client.snapshot.elapsedSeconds), 7);
        clock.now += seconds(3);
        assertEqual(Math.floor(client.snapshot.elapsedSeconds), 10);
    });
});

test('joining a recording in progress seeds elapsed from the service once', async () => {
    await withFixture(async ({client, fake, clock, until}) => {
        fake.State = 'RECORDING';
        fake.SessionId = 'running';
        fake.ElapsedSeconds = 42.0;
        client.connect();
        await until(() => client.snapshot.available);
        await until(() => client.snapshot.elapsedSeconds >= 42);
        clock.now += seconds(5);
        assertEqual(Math.floor(client.snapshot.elapsedSeconds), 47);
    });
});

test('stop() ends in STOPPED and freezes elapsed', async () => {
    await withFixture(async ({client, clock, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        await client.start();
        await until(() => client.snapshot.state === 'RECORDING');
        clock.now += seconds(12);
        assertEqual(await client.stop(), 'session-1');
        await until(() => client.snapshot.state === 'STOPPED');
        const frozen = client.snapshot.elapsedSeconds;
        assertEqual(Math.floor(frozen), 12);
        clock.now += seconds(30);
        assertEqual(client.snapshot.elapsedSeconds, frozen);
    });
});

test('a service error rejects with its D-Bus name and a stripped message', async () => {
    await withFixture(async ({client, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        let error = null;
        try {
            await client.stop();
        } catch (e) {
            error = e;
        }
        assertEqual(error?.name, 'dev.meetingscribe.Error.NO_ACTIVE_RECORDING');
        assertEqual(error?.message, 'nothing recording');
        assertEqual(client.snapshot.busy, false);
    });
});

test('a call without the service rejects with ServiceUnknown', async () => {
    await withFixture(async ({client}) => {
        client.connect();
        let error = null;
        try {
            await client.start();
        } catch (e) {
            error = e;
        }
        assertEqual(error?.name, 'org.freedesktop.DBus.Error.ServiceUnknown');
    }, {appear: false});
});

test('a re-entrant call while one is in flight is ignored', async () => {
    await withFixture(async ({client, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        const first = client.start();
        assertEqual(await client.start(), null);
        assertEqual(await first, 'session-1');
    });
});

test('StateChanged FAILED surfaces the failure reason', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        await client.start();
        fake.crash('capture exited unexpectedly');
        await until(() => client.snapshot.state === 'FAILED' &&
            client.snapshot.failureReason === 'capture exited unexpectedly');
        assertEqual(client.snapshot.sessionId, 'session-1');
    });
});

test('service vanishes: available becomes false', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.vanish();
        await until(() => !client.snapshot.available);
        let error = null;
        try {
            await client.start();
        } catch (e) {
            error = e;
        }
        assertEqual(error?.name, 'org.freedesktop.DBus.Error.ServiceUnknown');
    });
});

test('destroy() before connect() is safe and connect() afterwards does nothing', async () => {
    await withFixture(async ({client, changes}) => {
        client.destroy();
        client.destroy();
        client.connect();
        await sleep(100);
        assertEqual(changes.snapshots.length, 0);
        assertEqual(client.snapshot.available, false);
    });
});

test('destroy() leaves no handlers: no onChange after it, and it is idempotent', async () => {
    await withFixture(async ({client, fake, changes, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        const before = changes.snapshots.length;
        client.destroy();
        client.destroy();
        fake.crash('after destroy');
        fake.vanish();
        await sleep(150);
        assertEqual(changes.snapshots.length, before);
        assertEqual(client.snapshot.available, false);
    });
});

test('a late ElapsedSeconds reply from a previous recording never seeds the next one', async () => {
    // Hold the first ElapsedSeconds reply (recording A) until after B has started.
    let release = () => {};
    const gate = new Promise(resolve => {
        release = resolve;
    });
    let held = 0;
    let reachedResolve = () => {};
    const reached = new Promise(resolve => {
        reachedResolve = resolve;
    });
    const getProperty = async (...args) => {
        const value = await defaultGetProperty(...args);
        if (args[3] === 'ElapsedSeconds' && held++ === 0) {
            reachedResolve();
            await gate;
        }
        return value;
    };
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.ElapsedSeconds = 100.0; // recording A, as the service reports it
        await client.start();
        await reached; // A's reply was produced by the service but is still in flight
        fake.ElapsedSeconds = 5.0; // recording B starts from zero on the service
        await client.stop();
        await until(() => client.snapshot.state === 'STOPPED');
        await client.start();
        await until(() => client.snapshot.state === 'RECORDING' &&
            Math.floor(client.snapshot.elapsedSeconds) === 5);
        release();
        await sleep(150); // let A's stale reply land
        assertEqual(Math.floor(client.snapshot.elapsedSeconds), 5);
    }, {clientOptions: {getProperty}});
});

test('a call the service never answers rejects within the call timeout', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.hangCalls = true;
        const startedAt = GLib.get_monotonic_time();
        let error = null;
        try {
            await client.start();
        } catch (e) {
            error = e;
        }
        const waitedMs = (GLib.get_monotonic_time() - startedAt) / 1000;
        assertEqual(error !== null, true, 'the call must fail: ');
        assertEqual(waitedMs < 2500, true, `waited ${waitedMs} ms: `);
        assertEqual(client.snapshot.busy, false);
    }, {clientOptions: {callTimeoutMs: 400}});
});

test('FAILED is never reported without its failure reason', async () => {
    await withFixture(async ({client, fake, changes, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        await client.start();
        fake.crash('capture exited unexpectedly');
        await until(() => client.snapshot.state === 'FAILED' &&
            client.snapshot.failureReason === 'capture exited unexpectedly');
        await sleep(100);
        const bare = changes.snapshots.filter(s => s.state === 'FAILED' && s.failureReason === '');
        assertEqual(bare.length, 0, 'a FAILED snapshot had no reason: ');
    });
});
