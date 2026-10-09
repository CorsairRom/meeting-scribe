// Entry point: gjs -m gnome-extension/tests/run.js
// gjs has no module-directory discovery, so each test file is imported explicitly.
import System from 'system';

import {runAll, runCleanups} from './harness.js';
import './indicatorController.test.js';
import './indicatorModel.test.js';
import './privateBus.test.js';
import './recordingClient.test.js';
import './shellGlue.test.js';

let failed = 1;
try {
    failed = await runAll();
} finally {
    await runCleanups(); // never leave a private dbus-daemon behind, whatever happened
}
System.exit(failed === 0 ? 0 : 1);
