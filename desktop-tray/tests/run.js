// Entry point: gjs -m desktop-tray/tests/run.js
// Imports every *.test.js file next to this runner, in name order, so a new test file needs no edit here.
// The harness and the private bus fixtures are shared with the extension tests.
import Gio from 'gi://Gio';
import System from 'system';

import {runAll, runCleanups} from '../../gnome-extension/tests/harness.js';

const TEST_SUFFIX = '.test.js';

function testFileNames(directory) {
    const names = [];
    const children = directory.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
    for (let info = children.next_file(null); info !== null; info = children.next_file(null)) {
        if (info.get_name().endsWith(TEST_SUFFIX))
            names.push(info.get_name());
    }
    children.close(null);
    return names.sort();
}

let failed = 1;
try {
    const directory = Gio.File.new_for_uri(import.meta.url).get_parent();
    for (const name of testFileNames(directory))
        await import(directory.get_child(name).get_uri());
    failed = await runAll();
} finally {
    await runCleanups(); // never leave a private dbus-daemon behind, whatever happened
}
System.exit(failed === 0 ? 0 : 1);
