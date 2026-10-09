import GLib from 'gi://GLib';

import {test, assertEqual} from './harness.js';
import {GLibScheduler} from '../meeting-scribe@meetingscribe.dev/lib/glibScheduler.js';

// Spin the default main context until `done()` or the deadline (ms). Iterations block (no
// busy-wait); a guard source wakes the loop at the deadline, so the test still ends if every
// other source is gone (exactly the regression these tests guard against).
function spin(done, deadlineMs) {
    let expired = false;
    const guard = GLib.timeout_add(GLib.PRIORITY_DEFAULT, deadlineMs, () => {
        expired = true;
        return GLib.SOURCE_REMOVE;
    });
    const ctx = GLib.MainContext.default();
    while (!done() && !expired)
        ctx.iteration(true);
    if (!expired)
        GLib.source_remove(guard);
}

// Replace the global logError for one test and return the recorded calls.
function captureLogError(body) {
    const calls = [];
    const original = globalThis.logError;
    globalThis.logError = (...args) => calls.push(args);
    try {
        body();
    } finally {
        globalThis.logError = original;
    }
    return calls;
}

test('a tick that throws does not kill the timer, and cancel stays clean', () => {
    const scheduler = new GLibScheduler();
    let ticks = 0;
    captureLogError(() => {
        const id = scheduler.every(1, () => {
            ticks++;
            throw new Error('tick failed');
        });
        // timeout_add_seconds aligns to whole seconds: 2 ticks land within ~3 s; allow 4.5 s.
        spin(() => ticks >= 2, 4500);
        scheduler.cancel(id);
        scheduler.destroy();
    });
    assertEqual(ticks, 2);
});

test('a repeated failure is logged once, and again after the callback recovered', () => {
    const scheduler = new GLibScheduler();
    let ticks = 0;
    const calls = captureLogError(() => {
        // Ticks 1-2 fail, tick 3 succeeds, tick 4 fails again: expect two log calls.
        const id = scheduler.every(1, () => {
            ticks++;
            if (ticks !== 3)
                throw new Error(`tick ${ticks} failed`);
        });
        spin(() => ticks >= 4, 6500);
        scheduler.cancel(id);
        scheduler.destroy();
    });
    assertEqual(ticks, 4);
    assertEqual(calls.length, 2);
});
