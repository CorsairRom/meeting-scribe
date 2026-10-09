import {test, assertEqual, assertDeepEqual} from './harness.js';
import {IndicatorController} from '../meeting-scribe@meetingscribe.dev/lib/indicatorController.js';
import {ICON_IDLE, ICON_RECORDING} from '../meeting-scribe@meetingscribe.dev/lib/indicatorModel.js';

const flush = async () => {
    for (let i = 0; i < 5; i++)
        await null;
};

const snap = (overrides = {}) => ({
    available: true, busy: false, state: 'IDLE', sessionId: '', elapsedSeconds: 0,
    failureReason: '', ...overrides,
});

function fixture(initial = snap(), {startResult = 'sid', stopResult = 'sid'} = {}) {
    const client = {
        snapshot: initial,
        calls: [],
        start() {
            this.calls.push('start');
            return typeof startResult === 'function' ? startResult() : Promise.resolve(startResult);
        },
        stop() {
            this.calls.push('stop');
            return typeof stopResult === 'function' ? stopResult() : Promise.resolve(stopResult);
        },
    };
    const rendered = [];
    const view = {render: model => rendered.push(model)};
    const scheduler = {
        active: new Map(), next: 1, cancelled: 0,
        every(seconds, fn) {
            const handle = this.next++;
            this.active.set(handle, {seconds, fn});
            return handle;
        },
        cancel(handle) {
            this.cancelled++;
            this.active.delete(handle);
        },
        tick() {
            for (const {fn} of [...this.active.values()])
                fn();
        },
    };
    const notes = [];
    const controller = new IndicatorController({
        client, view, scheduler, notify: (title, body) => notes.push([title, body]), _: s => s,
    });
    const emit = snapshot => {
        client.snapshot = snapshot;
        controller.handleChange(snapshot);
    };
    return {client, view, rendered, scheduler, notes, controller, emit};
}

test('controller renders the current snapshot on refresh and on every change', () => {
    const f = fixture();
    f.controller.refresh();
    assertEqual(f.rendered.length, 1);
    assertEqual(f.rendered[0].icon, ICON_IDLE);
    f.emit(snap({state: 'RECORDING', elapsedSeconds: 3}));
    assertEqual(f.rendered.length, 2);
    assertEqual(f.rendered[1].icon, ICON_RECORDING);
    assertEqual(f.rendered[1].label, '00:00:03');
});

test('controller runs no tick while idle, unavailable or failed', () => {
    const f = fixture();
    f.controller.refresh();
    f.emit(snap({available: false}));
    f.emit(snap({state: 'FAILED', failureReason: 'x'}));
    f.emit(snap({state: 'STOPPED'}));
    assertEqual(f.scheduler.next, 1, 'nothing was ever scheduled: ');
});

test('controller starts one 1 s tick on entering RECORDING and re-renders fresh elapsed', () => {
    const f = fixture();
    f.emit(snap({state: 'RECORDING'}));
    f.emit(snap({state: 'RECORDING', sessionId: 's1'})); // still recording: no second tick
    assertEqual(f.scheduler.active.size, 1);
    assertEqual([...f.scheduler.active.values()][0].seconds, 1);
    const before = f.rendered.length;
    f.client.snapshot = snap({state: 'RECORDING', elapsedSeconds: 7});
    f.scheduler.tick();
    assertEqual(f.rendered.length, before + 1);
    assertEqual(f.rendered.at(-1).label, '00:00:07');
});

test('controller cancels the tick on stop, crash and service vanish', () => {
    for (const next of [
        snap({state: 'STOPPED'}), snap({state: 'FAILED', failureReason: 'x'}),
        snap({available: false}),
    ]) {
        const f = fixture();
        f.emit(snap({state: 'RECORDING'}));
        assertEqual(f.scheduler.active.size, 1);
        f.emit(next);
        assertEqual(f.scheduler.active.size, 0, `${next.state}: `);
    }
});

test('controller restarts the tick on a second recording', () => {
    const f = fixture();
    f.emit(snap({state: 'RECORDING'}));
    f.emit(snap({state: 'STOPPED'}));
    f.emit(snap({state: 'RECORDING'}));
    assertEqual(f.scheduler.active.size, 1);
});

test('primary click only opens the menu when idle or stopped; Start comes from the menu', async () => {
    for (const s of [snap({}), snap({state: 'STOPPED'})]) {
        const f = fixture(s);
        f.controller.refresh();
        assertEqual(f.controller.onPrimaryClick(), 'open-menu');
        await flush();
        assertDeepEqual(f.client.calls, [], 'a click alone never records: ');
        f.controller.onMenuAction('start');
        await flush();
        assertDeepEqual(f.client.calls, ['start']);
    }
});

test('primary click asks to open the menu while recording, failed or unavailable', () => {
    for (const s of [
        snap({state: 'RECORDING'}), snap({state: 'FAILED'}), snap({available: false}),
    ]) {
        const f = fixture(s);
        f.controller.refresh();
        assertEqual(f.controller.onPrimaryClick(), 'open-menu');
        assertDeepEqual(f.client.calls, []);
    }
});

test('a click on an open menu only closes it, even if the state became idle meanwhile', async () => {
    const f = fixture(snap({state: 'STOPPED'}));
    f.controller.refresh();
    assertEqual(f.controller.onPrimaryClick({menuOpen: true}), 'close-menu');
    await flush();
    assertDeepEqual(f.client.calls, []);
});

test('primary click does nothing while a call is in flight or the state is unknown', () => {
    for (const s of [snap({busy: true}), snap({state: 'WEIRD'})]) {
        const f = fixture(s);
        f.controller.refresh();
        assertEqual(f.controller.onPrimaryClick(), 'none');
        assertDeepEqual(f.client.calls, []);
    }
});

test('menu actions call stop and start', async () => {
    const f = fixture(snap({state: 'RECORDING'}));
    await f.controller.onMenuAction('stop');
    await f.controller.onMenuAction('start');
    assertDeepEqual(f.client.calls, ['stop', 'start']);
});

test('a failed call notifies with the mapped message', async () => {
    const f = fixture(snap(), {startResult: () => Promise.reject({
        name: 'dev.meetingscribe.Error.AUDIO_DEVICE_NOT_FOUND', message: 'raw',
    })});
    f.controller.refresh();
    f.controller.onMenuAction('start');
    await flush();
    assertEqual(f.notes.length, 1);
    assertEqual(f.notes[0][0], 'Meeting Scribe');
    assertEqual(f.notes[0][1].includes('microphone'), true);
});

test('a failure with an unknown name shows the service message', async () => {
    const f = fixture(snap({state: 'RECORDING'}), {stopResult: () => Promise.reject({
        name: '', message: 'Something odd.',
    })});
    await f.controller.onMenuAction('stop');
    assertDeepEqual(f.notes, [['Meeting Scribe', 'Something odd.']]);
});

test('a null (re-entrant) resolution is ignored without a notification', async () => {
    const f = fixture(snap(), {startResult: null});
    await f.controller.onMenuAction('start');
    assertDeepEqual(f.notes, []);
});

test('an unknown menu action is ignored', async () => {
    const f = fixture();
    await f.controller.onMenuAction('explode');
    assertDeepEqual(f.client.calls, []);
});

test('destroy cancels the tick, stops rendering and is idempotent', async () => {
    const f = fixture();
    f.emit(snap({state: 'RECORDING'}));
    f.controller.destroy();
    f.controller.destroy();
    assertEqual(f.scheduler.active.size, 0);
    assertEqual(f.scheduler.cancelled, 1, 'cancelled once: ');
    const rendered = f.rendered.length;
    f.controller.handleChange(snap({state: 'RECORDING'}));
    f.controller.refresh();
    assertEqual(f.rendered.length, rendered);
    assertEqual(f.scheduler.active.size, 0);
    assertEqual(f.controller.onPrimaryClick(), 'none');
    await f.controller.onMenuAction('start');
    assertDeepEqual(f.client.calls, []);
});

test('an error that arrives after destroy is not notified', async () => {
    let reject;
    const f = fixture(snap(), {startResult: () => new Promise((_res, rej) => {
        reject = rej;
    })});
    f.controller.refresh();
    f.controller.onMenuAction('start');
    f.controller.destroy();
    reject({name: '', message: 'late'});
    await flush();
    assertDeepEqual(f.notes, []);
});
