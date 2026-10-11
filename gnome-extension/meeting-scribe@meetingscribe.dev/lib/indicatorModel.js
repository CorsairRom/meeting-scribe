const pad2 = n => String(n).padStart(2, '0');

/**
 * Format a duration as HH:MM:SS. Fractions are floored, negative or invalid
 * input is treated as zero, and hours keep growing past 99.
 *
 * @param {number} seconds - elapsed seconds
 * @returns {string} the formatted duration
 */
export function formatElapsed(seconds) {
    const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return `${pad2(hours)}:${pad2(minutes)}:${pad2(total % 60)}`;
}

const ERROR_PREFIX = 'dev.meetingscribe.Error.';

/**
 * Map a D-Bus error name to a short user message. Literal `_()` calls keep
 * the strings extractable by xgettext.
 *
 * @param {string} dbusErrorName - remote error name, e.g. dev.meetingscribe.Error.X
 * @param {string} fallbackMessage - server message used for unknown names
 * @param {Function} [_] - translation function (identity by default)
 * @returns {string} the message to show
 */
export function errorMessage(dbusErrorName, fallbackMessage, _ = s => s) {
    switch (dbusErrorName) {
    case `${ERROR_PREFIX}RECORDING_ALREADY_ACTIVE`:
        return _('A recording is already in progress.');
    case `${ERROR_PREFIX}NO_ACTIVE_RECORDING`:
        return _('There is no recording in progress.');
    case `${ERROR_PREFIX}AUDIO_DEVICE_NOT_FOUND`:
        return _('No default microphone found. Choose an input device in Settings.');
    case `${ERROR_PREFIX}AUDIO_CAPTURE_FAILED`:
        return _('Audio capture failed. Check the service log.');
    case `${ERROR_PREFIX}RECORDING_STORAGE_FAILED`:
        return _('Could not create the recording folder.');
    case `${ERROR_PREFIX}ENTITY_NOT_FOUND_MEETING`:
        return _('That meeting no longer exists.');
    case `${ERROR_PREFIX}MEETING_PROCESSING_ACTIVE`:
        return _('That meeting is already queued or being processed.');
    case `${ERROR_PREFIX}MEETING_STORAGE_FAILED`:
        return _('Could not read the meetings folder.');
    case 'org.freedesktop.DBus.Error.ServiceUnknown':
        return _('The Meeting Scribe service is not running. Start it with: systemctl --user start meeting-scribe.service');
    case 'org.freedesktop.DBus.Error.NoReply':
        return _('The Meeting Scribe service is not responding.');
    default:
        return fallbackMessage || _('Unexpected error.');
    }
}

/**
 * The notification shown when the review app cannot be launched (its desktop entry is missing).
 *
 * @param {Function} [_] - translation function (identity by default)
 * @returns {string} the message to show
 */
export function launchFailureMessage(_ = s => s) {
    return _('Could not open the review app. Run `make install` to install it.');
}

export const ICON_IDLE = '●';
export const ICON_RECORDING = '■';
export const ICON_FAILED = '⚠';
export const ICON_UNAVAILABLE = '○';
export const PROCESSING_HINT = '…'; // panel label while a meeting is being processed

const status = text => ({kind: 'status', text});
const action = (text, id) => ({kind: 'action', text, action: id});
const SEPARATOR = {kind: 'separator'};

// Replace the first %s; keeps whole sentences translatable. A replacer function keeps
// service text literal (a string replacement would expand $&, $$, $` and $').
const fill = (template, value) => template.replace('%s', () => String(value));

function unavailableModel(_) {
    return {
        icon: ICON_UNAVAILABLE,
        label: '',
        clickAction: 'open-menu',
        menuItems: [
            status(_('The Meeting Scribe service is not running.')),
            status(_('Start it with `systemctl --user start meeting-scribe.service` (or run `make install`).')),
        ],
        tooltip: _('The Meeting Scribe service is not running.'),
        dimmed: true,
    };
}

function stateModel({state, sessionId, elapsedSeconds, failureReason}, _) {
    switch (state) {
    case 'IDLE':
    case 'STOPPED':
        // A click only opens the menu: starting is always an explicit menu action, so a
        // stray click on the top bar can never record (user decision, 2026-10-09).
        return {
            icon: ICON_IDLE,
            label: '',
            clickAction: 'open-menu',
            menuItems: [
                ...(state === 'STOPPED' && sessionId
                    ? [status(fill(_('Last recording: %s'), sessionId)), SEPARATOR]
                    : []),
                action(_('Start recording'), 'start'),
            ],
            tooltip: _('Start recording'),
            dimmed: false,
        };
    case 'RECORDING':
        return {
            icon: ICON_RECORDING,
            label: formatElapsed(elapsedSeconds),
            clickAction: 'open-menu',
            menuItems: [
                status(sessionId
                    ? fill(_('Recording in progress (%s)'), sessionId)
                    : _('Recording in progress')),
                SEPARATOR,
                action(_('Stop recording'), 'stop'),
            ],
            tooltip: _('Recording in progress'),
            dimmed: false,
        };
    case 'FAILED':
        return {
            icon: ICON_FAILED,
            label: '',
            clickAction: 'open-menu',
            menuItems: [
                status(failureReason
                    ? fill(_('Recording failed: %s'), failureReason)
                    : _('Recording failed.')),
                SEPARATOR,
                action(_('Start recording'), 'start'),
            ],
            tooltip: _('Recording failed'),
            dimmed: false,
        };
    default:
        return {
            icon: ICON_UNAVAILABLE,
            label: '',
            clickAction: 'none',
            menuItems: [status(fill(_('Unknown recording state: %s'), state))],
            tooltip: _('Unknown recording state'),
            dimmed: true,
        };
    }
}

// The stage words keep literal `_()` calls so they stay extractable.
function stageText(activeState, _) {
    switch (activeState) {
    case 'TRANSCRIBING':
        return fill(_('Processing meeting… (%s)'), _('transcribing'));
    case 'DIARIZING':
        return fill(_('Processing meeting… (%s)'), _('diarizing'));
    case 'EXTRACTING_SAMPLES':
        return fill(_('Processing meeting… (%s)'), _('extracting samples'));
    default:
        return _('Processing meeting…');
    }
}

function speakersText(count, _) {
    if (count === 0)
        return _('Last meeting processed: no speech detected');
    return count === 1
        ? _('Last meeting processed: 1 speaker')
        : fill(_('Last meeting processed: %s speakers'), count);
}

// What the pipeline is doing, as menu items (ids and counts only, never meeting content).
function processingItems(processing, _) {
    const items = [];
    const active = processing.activeState !== 'IDLE';
    if (active) {
        items.push(status(stageText(processing.activeState, _)));
    } else if (processing.last?.state === 'DONE') {
        items.push(status(speakersText(processing.last.speakerCount, _)));
    } else if (processing.last?.state === 'FAILED') {
        items.push(action(_('Processing failed — Reprocess'), 'reprocess'));
    }
    if (processing.queuedCount > 0) {
        items.push(status(processing.queuedCount === 1
            ? _('1 meeting queued')
            : fill(_('%s meetings queued'), processing.queuedCount)));
    }
    return items;
}

const REVIEWABLE_STATES = ['IDLE', 'STOPPED', 'FAILED'];

// "Review last meeting" opens the review app, which resolves "last" itself: the extension never
// lists meetings. It is offered when the service is available and the state is IDLE, STOPPED or
// FAILED (never while recording or in an unknown state).
function withReview(model, input, _) {
    // Inert in every other state: recording, an unknown state, and while a call is in flight.
    if (!REVIEWABLE_STATES.includes(input.state) || input.busy)
        return model;
    return {...model, menuItems: [...model.menuItems, action(_('Review last meeting'), 'review')]};
}

function withProcessing(model, processing, _) {
    if (!processing?.available)
        return model;
    const items = processingItems(processing, _);
    const active = processing.activeState !== 'IDLE';
    if (items.length === 0)
        return model;
    return {
        ...model,
        label: active && model.label === '' ? PROCESSING_HINT : model.label,
        tooltip: active && model.icon === ICON_IDLE ? _('Processing meeting…') : model.tooltip,
        menuItems: [...model.menuItems, SEPARATOR, ...items],
    };
}

/**
 * Describe what the panel indicator should show.
 *
 * @param {object} input
 * @param {boolean} input.available - the service owns its bus name
 * @param {boolean} input.busy - a Start/Stop call is in flight
 * @param {string} input.state - IDLE, RECORDING, STOPPED or FAILED
 * @param {string} input.sessionId - current or last session id
 * @param {number} input.elapsedSeconds - seconds since the recording started
 * @param {string} input.failureReason - why the session failed
 * @param {object} [input.processing] - Meetings1 snapshot: `available`, `activeState`,
 *     `queuedCount` and `last` ({meetingId, state, speakerCount}); omitted when unknown
 * @param {Function} [_] - translation function (identity by default)
 * @returns {{icon: string, label: string, clickAction: 'start'|'open-menu'|'none',
 *     menuItems: object[], tooltip: string, dimmed: boolean}} the view model
 */
export function viewModel(input, _ = s => s) {
    if (!input.available)
        return unavailableModel(_);

    const model = withProcessing(withReview(stateModel(input, _), input, _), input.processing, _);
    if (input.busy && model.clickAction !== 'none') {
        return {
            ...model,
            clickAction: 'none',
            menuItems: [status(_('Working…'))],
            tooltip: _('Working…'),
        };
    }
    return model;
}
