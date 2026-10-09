import {test, assertEqual, assertDeepEqual} from './harness.js';
import {formatElapsed, errorMessage, viewModel, ICON_IDLE, ICON_RECORDING, ICON_FAILED, ICON_UNAVAILABLE} from '../meeting-scribe@meetingscribe.dev/lib/indicatorModel.js';

test('formatElapsed pads to HH:MM:SS', () => {
    assertEqual(formatElapsed(0), '00:00:00');
    assertEqual(formatElapsed(65), '00:01:05');
    assertEqual(formatElapsed(3661), '01:01:01');
});

test('formatElapsed floors fractional seconds', () => {
    assertEqual(formatElapsed(59.99), '00:00:59');
    assertEqual(formatElapsed(0.4), '00:00:00');
});

test('formatElapsed clamps negative and invalid input to zero', () => {
    assertEqual(formatElapsed(-5), '00:00:00');
    assertEqual(formatElapsed(NaN), '00:00:00');
    assertEqual(formatElapsed(undefined), '00:00:00');
});

test('formatElapsed lets hours grow beyond two digits', () => {
    assertEqual(formatElapsed(99 * 3600 + 59 * 60 + 59), '99:59:59');
    assertEqual(formatElapsed(100 * 3600), '100:00:00');
});

const E = 'dev.meetingscribe.Error.';

test('errorMessage maps every known D-Bus error name to a short message', () => {
    const names = [
        `${E}RECORDING_ALREADY_ACTIVE`,
        `${E}NO_ACTIVE_RECORDING`,
        `${E}AUDIO_DEVICE_NOT_FOUND`,
        `${E}AUDIO_CAPTURE_FAILED`,
        `${E}RECORDING_STORAGE_FAILED`,
        'org.freedesktop.DBus.Error.ServiceUnknown',
        'org.freedesktop.DBus.Error.NoReply',
    ];
    const seen = new Set();
    for (const name of names) {
        const message = errorMessage(name, 'server text');
        assertEqual(typeof message, 'string', `${name}: `);
        assertEqual(message !== '' && message !== 'server text', true, `${name}: `);
        seen.add(message);
    }
    assertEqual(seen.size, names.length, 'messages must be distinct: ');
});

test('errorMessage explains the microphone problem and the stopped service', () => {
    assertEqual(
        errorMessage(`${E}AUDIO_DEVICE_NOT_FOUND`, '').includes('microphone'), true);
    const down = errorMessage('org.freedesktop.DBus.Error.ServiceUnknown', '');
    assertEqual(down.includes('not running'), true);
    assertEqual(down.includes('meeting-scribe'), true);
    assertEqual(
        errorMessage('org.freedesktop.DBus.Error.NoReply', '').includes('not responding'),
        true);
});

test('errorMessage falls back to the server message for unknown names', () => {
    assertEqual(errorMessage(`${E}INVALID_VALUE`, 'Field x is required.'), 'Field x is required.');
    assertEqual(errorMessage(undefined, 'boom'), 'boom');
});

test('errorMessage uses a generic text when nothing is known', () => {
    assertEqual(errorMessage('x.Y', ''), 'Unexpected error.');
    assertEqual(errorMessage(undefined, undefined), 'Unexpected error.');
});

test('errorMessage translates through the injected function', () => {
    const up = s => s.toUpperCase();
    assertEqual(errorMessage('x.Y', '', up), 'UNEXPECTED ERROR.');
    assertEqual(
        errorMessage(`${E}NO_ACTIVE_RECORDING`, '', up),
        'THERE IS NO RECORDING IN PROGRESS.');
});

const base = {
    available: true,
    busy: false,
    state: 'IDLE',
    sessionId: '',
    elapsedSeconds: 0,
    failureReason: '',
};
const vm = (overrides, tr) => viewModel({...base, ...overrides}, tr);
const ids = items => items.filter(i => i.kind === 'action').map(i => i.action);

test('icons are distinct named constants', () => {
    const icons = [ICON_IDLE, ICON_RECORDING, ICON_FAILED, ICON_UNAVAILABLE];
    assertEqual(new Set(icons).size, 4);
    assertEqual(ICON_IDLE, '●');
    assertEqual(ICON_RECORDING, '■');
});

test('IDLE shows the idle glyph and a click opens a menu with Start (never starts directly)', () => {
    const m = vm({});
    assertEqual(m.icon, ICON_IDLE);
    assertEqual(m.label, '');
    assertEqual(m.clickAction, 'open-menu');
    assertDeepEqual(m.menuItems, [{kind: 'action', text: 'Start recording', action: 'start'}]);
    assertEqual(m.tooltip, 'Start recording');
    assertEqual(m.dimmed, false);
});

test('RECORDING shows the timer, click opens a menu with Stop', () => {
    const m = vm({state: 'RECORDING', sessionId: 's1', elapsedSeconds: 3725.8});
    assertEqual(m.icon, ICON_RECORDING);
    assertEqual(m.label, '01:02:05');
    assertEqual(m.clickAction, 'open-menu');
    assertDeepEqual(m.menuItems, [
        {kind: 'status', text: 'Recording in progress (s1)'},
        {kind: 'separator'},
        {kind: 'action', text: 'Stop recording', action: 'stop'},
    ]);
    assertEqual(m.tooltip, 'Recording in progress');
});

test('RECORDING without a session id omits the id from the status line', () => {
    const m = vm({state: 'RECORDING'});
    assertEqual(m.menuItems[0].text, 'Recording in progress');
});

test('STOPPED looks idle, click opens a menu with the last session and Start', () => {
    const m = vm({state: 'STOPPED', sessionId: 's1'});
    assertEqual(m.icon, ICON_IDLE);
    assertEqual(m.label, '');
    assertEqual(m.clickAction, 'open-menu');
    assertDeepEqual(m.menuItems, [
        {kind: 'status', text: 'Last recording: s1'},
        {kind: 'separator'},
        {kind: 'action', text: 'Start recording', action: 'start'},
    ]);
});

test('STOPPED without a session id offers only Start', () => {
    assertDeepEqual(vm({state: 'STOPPED'}).menuItems,
        [{kind: 'action', text: 'Start recording', action: 'start'}]);
});

test('no state starts a recording on a plain click', () => {
    for (const state of ['IDLE', 'STOPPED', 'RECORDING', 'FAILED']) {
        const m = vm({state, sessionId: 's1'});
        assertEqual(m.clickAction === 'start', false, `${state}: `);
    }
});

test('FAILED shows a warning, click opens a menu with reason and Start', () => {
    const m = vm({state: 'FAILED', failureReason: 'ffmpeg exited with code 1'});
    assertEqual(m.icon, ICON_FAILED);
    assertEqual(m.label, '');
    assertEqual(m.clickAction, 'open-menu');
    assertDeepEqual(m.menuItems, [
        {kind: 'status', text: 'Recording failed: ffmpeg exited with code 1'},
        {kind: 'separator'},
        {kind: 'action', text: 'Start recording', action: 'start'},
    ]);
    assertEqual(m.tooltip, 'Recording failed');
});

test('FAILED inserts service text literally, even with $ replacement patterns', () => {
    const reason = "ffmpeg: cost $& and $$ in '$`' / \"$'\"";
    assertEqual(vm({state: 'FAILED', failureReason: reason}).menuItems[0].text,
        `Recording failed: ${reason}`);
});

test('FAILED without a reason still reads well', () => {
    assertEqual(vm({state: 'FAILED'}).menuItems[0].text, 'Recording failed.');
});

test('service unavailable is dimmed and explains how to start it', () => {
    const m = vm({available: false, state: 'RECORDING', elapsedSeconds: 9});
    assertEqual(m.icon, ICON_UNAVAILABLE);
    assertEqual(m.label, '');
    assertEqual(m.dimmed, true);
    assertEqual(m.clickAction, 'open-menu');
    assertEqual(m.menuItems.length, 2);
    assertEqual(m.menuItems[0].kind, 'status');
    assertEqual(m.menuItems[0].text, 'The Meeting Scribe service is not running.');
    assertEqual(m.menuItems[1].kind, 'status');
    assertEqual(m.menuItems[1].text.includes('meeting-scribe'), true);
    assertEqual(m.menuItems[1].text.includes('make run'), true);
    assertDeepEqual(ids(m.menuItems), []);
});

test('busy ignores clicks and shows a busy hint in every available state', () => {
    for (const state of ['IDLE', 'RECORDING', 'STOPPED', 'FAILED']) {
        const m = vm({busy: true, state, elapsedSeconds: 5});
        assertEqual(m.clickAction, 'none', `${state}: `);
        assertEqual(m.tooltip, 'Working…', `${state}: `);
        assertDeepEqual(m.menuItems, [{kind: 'status', text: 'Working…'}], `${state}: `);
    }
    assertEqual(vm({busy: true, state: 'RECORDING', elapsedSeconds: 5}).label, '00:00:05');
});

test('unavailable wins over busy', () => {
    const m = vm({available: false, busy: true});
    assertEqual(m.clickAction, 'open-menu');
    assertEqual(m.icon, ICON_UNAVAILABLE);
});

test('an unknown state is inert and dimmed', () => {
    const m = vm({state: 'WEIRD'});
    assertEqual(m.clickAction, 'none');
    assertEqual(m.icon, ICON_UNAVAILABLE);
    assertEqual(m.dimmed, true);
    assertDeepEqual(m.menuItems, [{kind: 'status', text: 'Unknown recording state: WEIRD'}]);
});

test('viewModel translates every string through the injected function', () => {
    const tr = s => `<${s}>`;
    assertEqual(vm({}, tr).tooltip, '<Start recording>');
    assertEqual(
        vm({state: 'FAILED', failureReason: 'x'}, tr).menuItems[0].text,
        '<Recording failed: %s>'.replace('%s', 'x'));
    assertEqual(vm({state: 'RECORDING'}, tr).menuItems[2].text, '<Stop recording>');
    assertEqual(vm({available: false}, tr).menuItems[0].text, '<The Meeting Scribe service is not running.>');
});
