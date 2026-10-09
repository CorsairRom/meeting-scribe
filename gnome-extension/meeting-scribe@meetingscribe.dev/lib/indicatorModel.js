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
    case 'org.freedesktop.DBus.Error.ServiceUnknown':
        return _('The Meeting Scribe service is not running. Start it with: meeting-scribe');
    case 'org.freedesktop.DBus.Error.NoReply':
        return _('The Meeting Scribe service is not responding.');
    default:
        return fallbackMessage || _('Unexpected error.');
    }
}

export const ICON_IDLE = '●';
export const ICON_RECORDING = '■';
export const ICON_FAILED = '⚠';
export const ICON_UNAVAILABLE = '○';

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
            status(_('Start it with `meeting-scribe` (or `make run` from the repository).')),
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
 * @param {Function} [_] - translation function (identity by default)
 * @returns {{icon: string, label: string, clickAction: 'start'|'open-menu'|'none',
 *     menuItems: object[], tooltip: string, dimmed: boolean}} the view model
 */
export function viewModel(input, _ = s => s) {
    if (!input.available)
        return unavailableModel(_);

    const model = stateModel(input, _);
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
