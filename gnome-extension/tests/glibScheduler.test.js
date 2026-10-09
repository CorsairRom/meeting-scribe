import GLib from 'gi://GLib';

import {test, assertEqual} from './harness.js';
import {GLibScheduler} from '../meeting-scribe@meetingscribe.dev/lib/glibScheduler.js';

// Spin the default main context until `done()` or the deadline (ms).
function spin(done, deadlineMs) {
    const until = GLib.get_monotonic_time() + deadlineMs * 1000;
    const ctx = GLib.MainContext.default();
    // Blocking iterations: no busy-wait; each returns after the next dispatched source.
    while (!done() && GLib.get_monotonic_time() < until)
        ctx.iteration(true);
}

test('a tick that throws does not kill the timer, and cancel stays clean', () => {
    const scheduler = new GLibScheduler();
    let ticks = 0;
    const id = scheduler.every(1, () => {
        ticks++;
        throw new Error('tick failed');
    });
    // timeout_add_seconds aligns to whole seconds: 2 ticks land within ~3 s; allow 4.5 s.
    spin(() => ticks >= 2, 4500);
    scheduler.cancel(id);
    scheduler.destroy();
    assertEqual(ticks, 2);
});
