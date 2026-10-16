import Gio from 'gi://Gio';

import {test, assertEqual, assertDeepEqual} from '../../gnome-extension/tests/harness.js';
import {viewModel} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/indicatorModel.js';
import {TRAY_ICONS, trayModel} from '../lib/trayModel.js';

const IDLE_PROCESSING = {available: true, activeState: 'IDLE', queuedCount: 0, last: null, lastSummary: null};
const base = {available: true, busy: false, state: 'IDLE', sessionId: '', elapsedSeconds: 0, failureReason: ''};

const STATES = {
    idle: {...base, processing: IDLE_PROCESSING},
    recording: {...base, state: 'RECORDING', sessionId: 's1', elapsedSeconds: 754, processing: IDLE_PROCESSING},
    processing: {...base, processing: {...IDLE_PROCESSING, activeState: 'DIARIZING', queuedCount: 2}},
    failed: {...base, state: 'FAILED', failureReason: 'AUDIO_CAPTURE_FAILED', processing: IDLE_PROCESSING},
    unavailable: {...base, available: false},
    processed: {...base, processing: {...IDLE_PROCESSING, last: {meetingId: 'm1', state: 'DONE', speakerCount: 3}}},
    reprocess: {...base, processing: {...IDLE_PROCESSING, last: {meetingId: 'm1', state: 'FAILED', speakerCount: 0}}},
};

test('each state maps to its status icon', () => {
    const icons = Object.fromEntries(Object.entries(STATES).map(([name, input]) =>
        [name, trayModel(viewModel(input)).iconName]));
    assertDeepEqual(icons, {
        idle: TRAY_ICONS.idle,
        recording: TRAY_ICONS.recording,
        processing: TRAY_ICONS.processing,
        failed: TRAY_ICONS.failed,
        unavailable: TRAY_ICONS.unavailable,
        processed: TRAY_ICONS.idle,
        reprocess: TRAY_ICONS.idle,
    });
});

test('icon names are the installed names and never symbolic (Plasma would recolor them)', () => {
    assertDeepEqual(Object.values(TRAY_ICONS).sort(), [
        'dev.meetingscribe.Tray-failed',
        'dev.meetingscribe.Tray-idle',
        'dev.meetingscribe.Tray-processing',
        'dev.meetingscribe.Tray-recording',
        'dev.meetingscribe.Tray-unavailable',
    ]);
});

test('every icon name has its SVG in desktop-tray/icons', () => {
    const icons = Gio.File.new_for_uri(import.meta.url).get_parent().get_parent().get_child('icons');
    for (const name of Object.values(TRAY_ICONS))
        assertEqual(icons.get_child(`${name}.svg`).query_exists(null), true, name);
});

test('outside a recording the menu and tooltip are the extension\'s, unchanged', () => {
    for (const name of ['idle', 'processing', 'failed', 'unavailable', 'processed', 'reprocess']) {
        const model = viewModel(STATES[name]);
        const tray = trayModel(model);
        assertDeepEqual(tray.menuItems, model.menuItems, `${name} menu`);
        assertEqual(tray.tooltip, model.tooltip, `${name} tooltip`);
    }
});

test('while recording the tooltip and the first status row end with the elapsed time', () => {
    const model = viewModel(STATES.recording);
    const tray = trayModel(model);
    assertEqual(tray.tooltip, 'Recording in progress · 00:12:34');
    assertDeepEqual(tray.menuItems, [
        {kind: 'status', text: 'Recording in progress (s1) · 00:12:34'},
        ...model.menuItems.slice(1),
    ]);
});

test('one second later only the elapsed time changes', () => {
    const before = trayModel(viewModel(STATES.recording));
    const after = trayModel(viewModel({...STATES.recording, elapsedSeconds: 755}));
    assertEqual(after.tooltip, 'Recording in progress · 00:12:35');
    assertEqual(after.menuItems[0].text, 'Recording in progress (s1) · 00:12:35');
    assertDeepEqual(after.menuItems.slice(1), before.menuItems.slice(1));
});

test('the view model is not modified', () => {
    const model = viewModel(STATES.recording);
    const copy = JSON.parse(JSON.stringify(model));
    trayModel(model);
    assertDeepEqual(model, copy);
});
