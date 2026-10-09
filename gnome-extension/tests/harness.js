// Minimal test harness for gjs ES modules (no npm, no Shell imports).
import GLib from 'gi://GLib';

const TEST_TIMEOUT_SECONDS = 5;

const tests = [];

export function test(name, fn) {
    tests.push({name, fn});
}

function show(value) {
    return JSON.stringify(value) ?? String(value);
}

export function assertEqual(actual, expected, message = '') {
    if (actual !== expected)
        throw new Error(`${message}expected ${show(expected)}, got ${show(actual)}`);
}

export function assertDeepEqual(actual, expected, message = '') {
    if (show(actual) !== show(expected))
        throw new Error(`${message}expected ${show(expected)}, got ${show(actual)}`);
}

export function assertThrows(fn, message = '') {
    try {
        fn();
    } catch (_e) {
        return;
    }
    throw new Error(`${message}expected the function to throw`);
}

// Rejects after the timeout; the returned cancel() must run once the test settles so
// the GLib source does not outlive it.
function timeoutAfter(seconds, name) {
    let sourceId = 0;
    const promise = new Promise((_resolve, reject) => {
        sourceId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, seconds, () => {
            sourceId = 0;
            reject(new Error(`timed out after ${seconds} s: ${name}`));
            return GLib.SOURCE_REMOVE;
        });
    });
    const cancel = () => {
        if (sourceId)
            GLib.source_remove(sourceId);
    };
    return {promise, cancel};
}

/**
 * Run every registered test (sync or async, one at a time, each with a timeout) and print
 * one line per failure plus a summary.
 *
 * @returns {Promise<number>} number of failed tests
 */
export async function runAll() {
    let passed = 0;
    let failed = 0;
    for (const {name, fn} of tests) {
        const timeout = timeoutAfter(TEST_TIMEOUT_SECONDS, name);
        try {
            await Promise.race([fn(), timeout.promise]);
            passed++;
        } catch (e) {
            failed++;
            print(`FAIL ${name}\n     ${e.message}`);
        } finally {
            timeout.cancel();
        }
    }
    if (tests.length === 0) {
        // A dropped import in run.js must not turn the suite silently green.
        print('0 passed, 0 failed: no tests registered');
        return 1;
    }
    print(`${passed} passed, ${failed} failed`);
    return failed;
}
