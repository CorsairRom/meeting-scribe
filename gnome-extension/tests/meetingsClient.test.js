import GLib from 'gi://GLib';

import {test, assertEqual, assertDeepEqual} from './harness.js';
import {MeetingsClient} from '../meeting-scribe@meetingscribe.dev/lib/meetingsClient.js';
import {PrivateBus, FakeMeetingsService} from './privateBus.js';

const BUS_NAME = 'dev.meetingscribe.Service';
const OBJECT_PATH = '/dev/meetingscribe/Service';
const WAIT_LIMIT_MS = 3000; // below the harness timeout so the fixture can still clean up

const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));

// Resolves when `condition()` holds, re-checked after every onChange; rejects on timeout.
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

/** Private bus + fake Meetings1 + client; always cleaned up. */
async function withFixture(body, {appear = true, clientOptions = {}, setup = () => {}} = {}) {
    const bus = await PrivateBus.start();
    let client = null;
    let fake = null;
    try {
        const changes = {snapshots: [], listeners: []};
        fake = new FakeMeetingsService(await bus.connect(), BUS_NAME, OBJECT_PATH);
        setup(fake);
        if (appear)
            await fake.appear();
        client = new MeetingsClient({
            connection: await bus.connect(),
            busName: BUS_NAME,
            objectPath: OBJECT_PATH,
            onChange: snapshot => {
                changes.snapshots.push(snapshot);
                changes.listeners.slice().forEach(listener => listener());
            },
            ...clientOptions,
        });
        await body({bus, client, fake, changes, until: c => until(changes, c)});
    } finally {
        client?.destroy();
        fake?.vanish();
        await bus.stop();
    }
}

test('meetings: construction is side effect free and an absent service stays unavailable', async () => {
    await withFixture(async ({client, changes}) => {
        assertEqual(client.snapshot.available, false);
        client.connect();
        await sleep(100);
        assertEqual(client.snapshot.available, false);
        assertEqual(changes.snapshots.length, 0);
    }, {appear: false});
});

test('meetings: the service appears with its pipeline status', async () => {
    await withFixture(async ({client, fake, until}) => {
        fake.ActiveMeetingId = 'm1';
        fake.ActiveState = 'DIARIZING';
        fake.QueuedCount = 2;
        client.connect();
        await until(() => client.snapshot.available);
        assertDeepEqual(client.snapshot, {
            available: true, activeMeetingId: 'm1', activeState: 'DIARIZING',
            queuedCount: 2, last: null, lastSummary: null,
        });
    });
});

test('meetings: PropertiesChanged updates the status', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.setStatus('m1', 'TRANSCRIBING', 1);
        await until(() => client.snapshot.activeState === 'TRANSCRIBING');
        assertEqual(client.snapshot.activeMeetingId, 'm1');
        assertEqual(client.snapshot.queuedCount, 1);
        fake.setStatus('', 'IDLE', 0);
        await until(() => client.snapshot.activeState === 'IDLE');
        assertEqual(client.snapshot.queuedCount, 0);
    });
});

test('meetings: ProcessingFinished is kept as the last result', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.finish('m1', 'DONE', 3);
        await until(() => client.snapshot.last !== null);
        assertDeepEqual(client.snapshot.last, {meetingId: 'm1', state: 'DONE', speakerCount: 3});
        fake.finish('m2', 'FAILED', 0);
        await until(() => client.snapshot.last.meetingId === 'm2');
        assertEqual(client.snapshot.last.state, 'FAILED');
    });
});

test('meetings: reprocess calls the service and forgets the failed result', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.finish('m2', 'FAILED', 0);
        await until(() => client.snapshot.last !== null);
        assertEqual(await client.reprocess('m2'), 'QUEUED');
        assertDeepEqual(fake.reprocessed, ['m2']);
        assertEqual(client.snapshot.last, null);
    });
});

test('meetings: a refused reprocess rejects with the remote error name', async () => {
    await withFixture(async ({client, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        let error = null;
        try {
            await client.reprocess('gone');
        } catch (e) {
            error = e;
        }
        assertEqual(error?.name, 'dev.meetingscribe.Error.ENTITY_NOT_FOUND_MEETING');
    });
});

test('meetings: reprocess without a service rejects with ServiceUnknown', async () => {
    await withFixture(async ({client}) => {
        client.connect();
        let error = null;
        try {
            await client.reprocess('m1');
        } catch (e) {
            error = e;
        }
        assertEqual(error?.name, 'org.freedesktop.DBus.Error.ServiceUnknown');
    }, {appear: false});
});

test('meetings: the service vanishing resets the snapshot and reports it once', async () => {
    await withFixture(async ({client, fake, changes, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.finish('m0', 'DONE', 2);
        await until(() => client.snapshot.last !== null);
        fake.setStatus('m1', 'DIARIZING', 1);
        await until(() => client.snapshot.activeState === 'DIARIZING');
        fake.finish('m0', 'DONE', 2); // a late signal: the snapshot has both again
        await until(() => client.snapshot.last !== null);
        fake.vanish();
        await until(() => !client.snapshot.available);
        assertDeepEqual(client.snapshot, {
            available: false, activeMeetingId: '', activeState: 'IDLE', queuedCount: 0, last: null,
            lastSummary: null,
        });
        const count = changes.snapshots.length;
        await sleep(100);
        assertEqual(changes.snapshots.length, count);
    });
});

test('meetings: a hung reprocess times out instead of waiting forever', async () => {
    await withFixture(async ({client, fake, until}) => {
        fake.ReprocessAsync = () => {}; // never answers
        client.connect();
        await until(() => client.snapshot.available);
        const startedAt = GLib.get_monotonic_time();
        let error = null;
        try {
            await client.reprocess('m1');
        } catch (e) {
            error = e;
        }
        const waitedMs = (GLib.get_monotonic_time() - startedAt) / 1000;
        assertEqual(error !== null, true, 'the call must fail: ');
        assertEqual(waitedMs < 2500, true, `waited ${waitedMs} ms: `);
        // the client is usable again after the timeout
        fake.ReprocessAsync = FakeMeetingsService.prototype.ReprocessAsync;
        assertEqual(await client.reprocess('m1'), 'QUEUED');
    }, {clientOptions: {callTimeoutMs: 300}});
});

test('meetings: destroy stops all reporting', async () => {
    await withFixture(async ({client, fake, changes, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        client.destroy();
        const count = changes.snapshots.length;
        fake.setStatus('m1', 'DIARIZING', 0);
        fake.finish('m1', 'DONE', 1);
        await sleep(150);
        assertEqual(changes.snapshots.length, count);
        assertEqual(client.snapshot.available, false);
    });
});

test('meetings: a new job forgets the previous result', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.finish('m1', 'FAILED', 0);
        await until(() => client.snapshot.last !== null);
        fake.setStatus('m2', 'TRANSCRIBING', 0); // a job starts; its ProcessingFinished may be missed
        await until(() => client.snapshot.activeState === 'TRANSCRIBING');
        assertEqual(client.snapshot.last, null);
    });
});

test('meetings: a result survives status changes inside the same idle period', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.finish('m1', 'DONE', 2);
        await until(() => client.snapshot.last !== null);
        fake.setStatus('', 'IDLE', 0); // still idle: nothing started
        await sleep(100);
        assertEqual(client.snapshot.last?.meetingId, 'm1');
    });
});

test('meetings: a restarted service does not bring the old result back', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.finish('m1', 'FAILED', 0);
        await until(() => client.snapshot.last !== null);
        fake.vanish();
        await until(() => !client.snapshot.available);
        await fake.appear();
        await until(() => client.snapshot.available);
        assertEqual(client.snapshot.last, null);
    });
});

test('meetings: a proxy that cannot be built is logged once and stays unavailable', async () => {
    const logged = [];
    await withFixture(async ({client}) => {
        client.connect();
        await sleep(300); // no change is ever reported, so there is nothing to wait on
        assertEqual(logged.length, 1);
        assertEqual(client.snapshot.available, false);
    }, {clientOptions: {
        logError: message => logged.push(message),
        createProxy: (_connection, _name, _path, callback) =>
            callback(null, new Error('no interface')),
    }});
});

// -- LastSummary (Summaries1, F5.7) ------------------------------------------------------------

const SUMMARY = {
    meeting_id: 'm1', started_at: '2026-10-09T14:30:00+00:00',
    generated_at: '2026-10-09T16:00:00+00:00', path: '/fake/dir/m1/summary.md',
};

test('meetings: the last summary is read when the service appears', async () => {
    await withFixture(async ({client, fake, until}) => {
        fake.lastSummary = SUMMARY;
        client.connect();
        await until(() => client.snapshot.lastSummary !== null);
        assertDeepEqual(client.snapshot.lastSummary,
            {startedAt: SUMMARY.started_at, path: SUMMARY.path});
    });
});

test('meetings: no summary yet, or an older service, hides the summary without failing', async () => {
    await withFixture(async ({client, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        await sleep(150);
        assertEqual(client.snapshot.lastSummary, null);
    });
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        await sleep(150);
        assertEqual(fake.summaryCalls, 0);
        assertEqual(client.snapshot.lastSummary, null);
        assertEqual(client.snapshot.available, true);
    }, {setup: fake => {
        fake.withSummaries = false;
    }});
});

test('meetings: SummaryFinished refreshes the last summary', async () => {
    await withFixture(async ({client, fake, until}) => {
        client.connect();
        await until(() => client.snapshot.available);
        fake.lastSummary = SUMMARY;
        fake.finishSummary('m1', 'DONE');
        await until(() => client.snapshot.lastSummary?.path === SUMMARY.path);
    });
});

test('meetings: refreshLastSummary follows the service and drops a summary that is gone', async () => {
    await withFixture(async ({client, fake, until}) => {
        fake.lastSummary = SUMMARY;
        client.connect();
        await until(() => client.snapshot.lastSummary !== null);
        fake.lastSummary = {...SUMMARY, path: '/fake/dir/m2/summary.md'};
        await client.refreshLastSummary();
        assertEqual(client.snapshot.lastSummary.path, '/fake/dir/m2/summary.md');
        fake.lastSummary = null;
        await client.refreshLastSummary();
        assertEqual(client.snapshot.lastSummary, null);
    });
});

test('meetings: a refresh without a service or after destroy is a quiet no-op', async () => {
    await withFixture(async ({client}) => {
        await client.refreshLastSummary();
        client.connect();
        client.destroy();
        await client.refreshLastSummary();
        assertEqual(client.snapshot.lastSummary, null);
    }, {appear: false});
});

test('meetings: a vanished or restarted service forgets the summary', async () => {
    await withFixture(async ({client, fake, until}) => {
        fake.lastSummary = SUMMARY;
        client.connect();
        await until(() => client.snapshot.lastSummary !== null);
        fake.vanish();
        await until(() => !client.snapshot.available);
        assertEqual(client.snapshot.lastSummary, null);
    });
});

test('meetings: a path that is not absolute is never kept', async () => {
    await withFixture(async ({client, fake, until}) => {
        fake.lastSummary = {...SUMMARY, path: 'relative/summary.md'};
        client.connect();
        await until(() => client.snapshot.available);
        await sleep(150);
        assertEqual(client.snapshot.lastSummary, null);
    });
});
