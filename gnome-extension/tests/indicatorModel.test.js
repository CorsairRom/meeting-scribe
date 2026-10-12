import {test, assertEqual, assertDeepEqual} from './harness.js';
import {formatElapsed, errorMessage, launchFailureMessage, summaryOpenFailureMessage, formatStartedAt, viewModel, ICON_IDLE, ICON_RECORDING, ICON_FAILED, ICON_UNAVAILABLE} from '../meeting-scribe@meetingscribe.dev/lib/indicatorModel.js';

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
const full = (overrides, tr) => viewModel({...base, ...overrides}, tr);
// The Preferences item ends every menu that can open (tested in its own section below): the
// other tests describe the items above it, so they compare the model without that tail.
const withoutPreferences = model => model.menuItems.at(-1)?.action === 'preferences'
    ? {...model, menuItems: model.menuItems.slice(0, -2)}
    : model;
const vm = (overrides, tr) => withoutPreferences(full(overrides, tr));
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
    assertDeepEqual(m.menuItems, [
        {kind: 'action', text: 'Start recording', action: 'start'},
        {kind: 'action', text: 'Review last meeting', action: 'review'},
    ]);
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
        {kind: 'action', text: 'Review last meeting', action: 'review'},
    ]);
});

test('STOPPED without a session id offers only Start', () => {
    assertDeepEqual(vm({state: 'STOPPED'}).menuItems,
        [
            {kind: 'action', text: 'Start recording', action: 'start'},
            {kind: 'action', text: 'Review last meeting', action: 'review'},
        ]);
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
        {kind: 'action', text: 'Review last meeting', action: 'review'},
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
    assertEqual(m.menuItems[1].text.includes('systemctl'), true);
    assertEqual(m.menuItems[1].text.includes('make install'), true);
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

// -- meeting processing (Meetings1) -----------------------------------------------------------

const idle = {available: true, activeMeetingId: '', activeState: 'IDLE', queuedCount: 0, last: null};
const proc = overrides => ({...idle, ...overrides});
const withProcessing = (processing, overrides = {}) => vm({...overrides, processing});
const texts = items => items.filter(i => i.kind !== 'separator').map(i => i.text);

test('without processing input the model is unchanged', () => {
    assertDeepEqual(vm({}).menuItems, [
        {kind: 'action', text: 'Start recording', action: 'start'},
        {kind: 'action', text: 'Review last meeting', action: 'review'},
    ]);
    assertEqual(vm({}).label, '');
});

test('an idle pipeline adds nothing to the menu', () => {
    assertDeepEqual(withProcessing(proc({})).menuItems, vm({}).menuItems);
});

test('a processing job shows its stage under the recording items', () => {
    for (const [state, text] of [
        ['TRANSCRIBING', 'Processing meeting… (transcribing)'],
        ['DIARIZING', 'Processing meeting… (diarizing)'],
        ['EXTRACTING_SAMPLES', 'Processing meeting… (extracting samples)'],
        ['QUEUED', 'Processing meeting…'],
    ]) {
        const m = withProcessing(proc({activeMeetingId: 'm1', activeState: state}));
        assertDeepEqual(m.menuItems, [
            {kind: 'action', text: 'Start recording', action: 'start'},
            {kind: 'action', text: 'Review last meeting', action: 'review'},
            {kind: 'separator'},
            {kind: 'status', text},
        ], `${state}: `);
    }
});

test('queued jobs are counted, singular and plural, with or without an active job', () => {
    const one = withProcessing(proc({activeMeetingId: 'm1', activeState: 'DIARIZING', queuedCount: 1}));
    assertDeepEqual(texts(one.menuItems).slice(2), ['Processing meeting… (diarizing)', '1 meeting queued']);
    const many = withProcessing(proc({queuedCount: 3}));
    assertDeepEqual(texts(many.menuItems).slice(2), ['3 meetings queued']);
});

test('the panel shows a processing hint only while a job runs, never replacing the timer', () => {
    assertEqual(withProcessing(proc({activeMeetingId: 'm1', activeState: 'DIARIZING'})).label, '…');
    assertEqual(withProcessing(proc({queuedCount: 2})).label, '');
    const recording = withProcessing(proc({activeMeetingId: 'm1', activeState: 'DIARIZING'}),
        {state: 'RECORDING', elapsedSeconds: 5});
    assertEqual(recording.label, '00:00:05');
    assertEqual(withProcessing(proc({activeMeetingId: 'm1', activeState: 'DIARIZING'})).tooltip,
        'Processing meeting…');
});

test('the click rule is unchanged while processing: every click opens the menu', () => {
    for (const p of [proc({activeMeetingId: 'm1', activeState: 'DIARIZING'}),
        proc({last: {meetingId: 'm1', state: 'FAILED', speakerCount: 0}})])
        assertEqual(withProcessing(p).clickAction, 'open-menu');
});

test('a finished meeting reports the speaker count and nothing else', () => {
    const done = n => withProcessing(proc({last: {meetingId: 'm1', state: 'DONE', speakerCount: n}}));
    assertEqual(texts(done(3).menuItems).at(-1), 'Last meeting processed: 3 speakers');
    assertEqual(texts(done(1).menuItems).at(-1), 'Last meeting processed: 1 speaker');
    assertEqual(texts(done(0).menuItems).at(-1), 'Last meeting processed: no speech detected');
    assertEqual(JSON.stringify(done(3)).includes('m1'), false, 'no meeting id in the view: ');
});

test('a failed meeting offers one Reprocess action', () => {
    const m = withProcessing(proc({last: {meetingId: 'm1', state: 'FAILED', speakerCount: 0}}));
    assertDeepEqual(m.menuItems.slice(-1), [
        {kind: 'action', text: 'Processing failed — Reprocess', action: 'reprocess'},
    ]);
    assertDeepEqual(ids(m.menuItems), ['start', 'review', 'reprocess']);
});

test('an active job hides the previous result', () => {
    const m = withProcessing(proc({
        activeMeetingId: 'm2', activeState: 'TRANSCRIBING',
        last: {meetingId: 'm1', state: 'FAILED', speakerCount: 0},
    }));
    assertDeepEqual(texts(m.menuItems).slice(2), ['Processing meeting… (transcribing)']);
});

test('processing is not shown when the service is unavailable or a call is in flight', () => {
    const active = proc({activeMeetingId: 'm1', activeState: 'DIARIZING'});
    assertEqual(withProcessing(active, {available: false}).menuItems.length, 2);
    assertEqual(withProcessing(active, {available: false}).label, '');
    assertDeepEqual(texts(withProcessing(active, {busy: true}).menuItems), ['Working…']);
    assertEqual(withProcessing({...active, available: false}).menuItems.length, 2);
});

test('processing lines translate through the injected function', () => {
    const up = s => s.split('%s').map(part => part.toUpperCase()).join('%s');
    const m = viewModel({...base, processing: proc({activeMeetingId: 'm1',
        activeState: 'DIARIZING', queuedCount: 2})}, up);
    assertDeepEqual(texts(withoutPreferences(m).menuItems).slice(2),
        ['PROCESSING MEETING… (DIARIZING)', '2 MEETINGS QUEUED']);
});

test('errorMessage maps the Meetings1 errors', () => {
    assertEqual(errorMessage(`${E}ENTITY_NOT_FOUND_MEETING`, ''), 'That meeting no longer exists.');
    assertEqual(errorMessage(`${E}MEETING_PROCESSING_ACTIVE`, ''),
        'That meeting is already queued or being processed.');
    assertEqual(errorMessage(`${E}MEETING_STORAGE_FAILED`, ''), 'Could not read the meetings folder.');
});

// -- Review last meeting (F4.7, AC-16) ---------------------------------------------------------

const REVIEW = {kind: 'action', text: 'Review last meeting', action: 'review'};
const reviewItems = model => model.menuItems.filter(item => item.action === 'review');

test('the review item is offered whenever the service is available and nothing records', () => {
    for (const state of ['IDLE', 'STOPPED', 'FAILED']) {
        const items = reviewItems(vm({state}));
        assertDeepEqual(items, [REVIEW], `${state}: `);
    }
});

test('the review item comes after the recording items and before the processing lines', () => {
    const processing = {available: true, activeState: 'IDLE', queuedCount: 0,
        last: {meetingId: 'm', state: 'DONE', speakerCount: 2}};
    const items = vm({state: 'IDLE', processing}).menuItems;
    assertDeepEqual(items.map(i => i.action ?? i.kind), ['start', 'review', 'separator', 'status']);
});

test('the review item stays hidden in an unknown state and while a call is in flight', () => {
    assertDeepEqual(reviewItems(vm({state: 'WEIRD'})), []);
    assertDeepEqual(reviewItems(vm({state: 'WEIRD', busy: true})), []);
    for (const state of ['IDLE', 'STOPPED', 'FAILED', 'RECORDING'])
        assertDeepEqual(reviewItems(vm({state, busy: true})), [], `${state} busy: `);
});

test('the review item is hidden while recording, unavailable or busy', () => {
    assertDeepEqual(reviewItems(vm({state: 'RECORDING'})), []);
    assertDeepEqual(reviewItems(vm({available: false})), []);
    assertDeepEqual(reviewItems(vm({busy: true})), []);
});

test('the review item is translated and needs no meeting data', () => {
    const tr = s => `<${s}>`;
    const item = reviewItems(vm({state: 'IDLE'}, tr))[0];
    assertEqual(item.text, '<Review last meeting>');
    assertDeepEqual(Object.keys(item).sort(), ['action', 'kind', 'text']);
});

test('launchFailureMessage tells the user to run make install', () => {
    const message = launchFailureMessage(s => s);
    assertEqual(message.includes('make install'), true);
    assertEqual(launchFailureMessage(s => `<${s}>`).startsWith('<'), true);
});

// -- Open last summary (F5.7, AC-19) -----------------------------------------------------------

const STARTED = '2026-10-09T14:30:00+00:00';
const SUMMARY_PATH = '/private/place/m1/summary.md';
const withSummary = (extra = {}) => ({
    available: true, activeState: 'IDLE', queuedCount: 0, last: null,
    lastSummary: {startedAt: STARTED, path: SUMMARY_PATH}, ...extra,
});
const summaryItems = model => model.menuItems.filter(item => item.action === 'summary');

test('formatStartedAt gives a readable weekday, date and 24-hour time in the given locale', () => {
    const text = formatStartedAt(STARTED, {locale: 'en-US', timeZone: 'UTC'});
    for (const part of ['Fri', 'Oct', '9', '14:30'])
        assertEqual(text.includes(part), true, `${text}: missing ${part}: `);
});

test('formatStartedAt returns an empty string for an invalid or missing date', () => {
    for (const value of ['', 'not a date', undefined, null, 42])
        assertEqual(formatStartedAt(value), '', `${value}: `);
});

test('the summary item shows the readable date, no title, no path', () => {
    const model = vm({state: 'IDLE', processing: withSummary()});
    const [item] = summaryItems(model);
    assertEqual(item.text, `Open last summary (${formatStartedAt(STARTED)})`);
    assertDeepEqual(Object.keys(item).sort(), ['action', 'kind', 'text']);
    assertEqual(JSON.stringify(model).includes(SUMMARY_PATH), false);
});

test('the summary item follows the review item, before the processing lines', () => {
    const items = vm({state: 'IDLE', processing: withSummary({
        last: {meetingId: 'm', state: 'DONE', speakerCount: 2}})}).menuItems;
    assertDeepEqual(items.map(i => i.action ?? i.kind),
        ['start', 'review', 'summary', 'separator', 'status']);
});

test('the summary item is hidden without a summary, with an invalid date or an older service', () => {
    assertDeepEqual(summaryItems(vm({state: 'IDLE', processing: withSummary({lastSummary: null})})), []);
    assertDeepEqual(summaryItems(vm({state: 'IDLE', processing: withSummary({lastSummary: undefined})})), []);
    assertDeepEqual(summaryItems(vm({state: 'IDLE', processing: withSummary({
        lastSummary: {startedAt: 'garbage', path: SUMMARY_PATH}})})), []);
    assertDeepEqual(summaryItems(vm({state: 'IDLE', processing: {available: true,
        activeState: 'IDLE', queuedCount: 0, last: null}})), []);
});

test('the summary item follows the review visibility: not recording, busy, unknown or unavailable', () => {
    const processing = withSummary();
    for (const state of ['IDLE', 'STOPPED', 'FAILED'])
        assertEqual(summaryItems(vm({state, processing})).length, 1, `${state}: `);
    assertDeepEqual(summaryItems(vm({state: 'RECORDING', processing})), []);
    assertDeepEqual(summaryItems(vm({state: 'IDLE', busy: true, processing})), []);
    assertDeepEqual(summaryItems(vm({state: 'WEIRD', processing})), []);
    assertDeepEqual(summaryItems(vm({available: false, processing})), []);
    assertDeepEqual(summaryItems(vm({state: 'IDLE',
        processing: {...processing, available: false}})), []);
});

test('the summary item text is translated through the template', () => {
    const tr = s => `<${s}>`;
    const [item] = summaryItems(vm({state: 'IDLE', processing: withSummary()}, tr));
    assertEqual(item.text.startsWith('<Open last summary ('), true);
});

test('summaryOpenFailureMessage is short, fixed and translated', () => {
    assertEqual(summaryOpenFailureMessage(s => s).includes('summary'), true);
    assertEqual(summaryOpenFailureMessage(s => `<${s}>`).startsWith('<'), true);
});

// -- Preferences... (F5.10, AC-12): always offered where the menu opens, last, after a separator ---

const PREFERENCES = {kind: 'action', text: 'Preferences…', action: 'preferences'};
const preferencesItems = model => model.menuItems.filter(item => item.action === 'preferences');

test('the preferences item ends the menu after a separator in every state that opens it', () => {
    for (const overrides of [{state: 'IDLE'}, {state: 'STOPPED', sessionId: 's1'},
        {state: 'RECORDING'}, {state: 'FAILED'}, {available: false}]) {
        const items = full(overrides).menuItems;
        assertDeepEqual(items.slice(-2), [{kind: 'separator'}, PREFERENCES], `${JSON.stringify(overrides)}: `);
        assertEqual(preferencesItems(full(overrides)).length, 1);
    }
});

test('the preferences item does not depend on the recording, the review or the processing state', () => {
    const processing = {available: true, activeState: 'DIARIZING', queuedCount: 2, last: null};
    assertDeepEqual(full({state: 'IDLE', processing}).menuItems.at(-1), PREFERENCES);
    assertDeepEqual(full({state: 'RECORDING', processing}).menuItems.at(-1), PREFERENCES);
});

test('the preferences item is absent where the menu cannot open: busy and unknown states', () => {
    assertDeepEqual(preferencesItems(full({state: 'IDLE', busy: true})), []);
    assertDeepEqual(preferencesItems(full({state: 'WEIRD'})), []);
});

test('the preferences item is translated', () => {
    const [item] = preferencesItems(full({state: 'IDLE'}, s => `<${s}>`));
    assertEqual(item.text, '<Preferences…>');
});
