// Minimal test harness for gjs ES modules (no npm, no Shell imports).

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

/**
 * Run every registered test and print one line per failure plus a summary.
 *
 * @returns {number} number of failed tests
 */
export function runAll() {
    let passed = 0;
    let failed = 0;
    for (const {name, fn} of tests) {
        try {
            fn();
            passed++;
        } catch (e) {
            failed++;
            print(`FAIL ${name}\n     ${e.message}`);
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
