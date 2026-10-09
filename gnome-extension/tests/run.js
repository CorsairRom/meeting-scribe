// Entry point: gjs -m gnome-extension/tests/run.js
// gjs has no module-directory discovery, so each test file is imported explicitly.
import System from 'system';

import {runAll} from './harness.js';
import './indicatorModel.test.js';

System.exit(runAll() === 0 ? 0 : 1);
