// The tray reuses the extension's Shell-free modules through this relative path (ADR-012); the install copies
// desktop-tray/ and the extension's lib/ with the same layout, so these imports also resolve in versions/<id>/.
import {test, assertEqual} from '../../gnome-extension/tests/harness.js';
import * as model from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/indicatorModel.js';
import {IndicatorController} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/indicatorController.js';
import {GLibScheduler} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/glibScheduler.js';
import {launchPreferencesAction} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/preferencesLauncher.js';
import {MeetingsClient} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/meetingsClient.js';
import {RecordingClient} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/recordingClient.js';

test('the tray reaches every shared module of the extension', () => {
    assertEqual(typeof model.viewModel, 'function', 'viewModel');
    assertEqual(typeof model.formatElapsed, 'function', 'formatElapsed');
    for (const [name, value] of Object.entries({
        IndicatorController, GLibScheduler, launchPreferencesAction, MeetingsClient, RecordingClient,
    }))
        assertEqual(typeof value, 'function', name);
});
