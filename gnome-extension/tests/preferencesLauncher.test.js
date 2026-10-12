import {test, assertEqual, assertDeepEqual} from './harness.js';
import {
    PREFERENCES_ACTION, REVIEW_DESKTOP_ENTRY, launchPreferencesAction,
} from '../meeting-scribe@meetingscribe.dev/lib/preferencesLauncher.js';

const app = (actions = [PREFERENCES_ACTION]) => ({
    launched: [],
    list_actions: () => actions,
    launch_action(name, context) {
        this.launched.push([name, context]);
    },
});

test('the preferences action is launched on the review desktop entry', () => {
    const entry = app();
    const asked = [];
    const ok = launchPreferencesAction(id => {
        asked.push(id);
        return entry;
    });
    assertEqual(ok, true);
    assertDeepEqual(asked, [REVIEW_DESKTOP_ENTRY]);
    assertDeepEqual(entry.launched, [['preferences', null]]);
    assertEqual(REVIEW_DESKTOP_ENTRY, 'dev.meetingscribe.Review.desktop');
});

test('a missing desktop entry is a failure, not an exception', () => {
    assertEqual(launchPreferencesAction(() => null), false);
});

test('an entry installed before the action existed is a failure and launches nothing', () => {
    const old = app([]);
    assertEqual(launchPreferencesAction(() => old), false);
    assertDeepEqual(old.launched, []);
});
