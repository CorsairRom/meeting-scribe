// Static checks for the files that only run inside gnome-shell (never executed here):
// syntax via `node --check` on stdin, import sanity, and metadata.json fields.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {test, assertEqual, assertDeepEqual} from './harness.js';

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async', 'communicate_utf8_finish');

const EXTENSION_DIR = GLib.build_filenamev([
    GLib.path_get_dirname(GLib.filename_from_uri(import.meta.url)[0]),
    'meeting-scribe@meetingscribe.dev',
].map((part, i) => i === 0 ? GLib.path_get_dirname(part) : part));
const GLUE_FILES = ['extension.js', 'lib/panelIndicator.js', 'lib/glibScheduler.js'];
const PREFS_FILE = 'prefs.js';
const PREFS_RESOURCE = 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';
const PURE_FILES = ['lib/indicatorModel.js', 'lib/indicatorController.js',
    'lib/preferencesLauncher.js'];
const SHELL_PREFIX = 'resource:///org/gnome/shell/';

const path = relative => GLib.build_filenamev([EXTENSION_DIR, relative]);

function read(relative) {
    const [, bytes] = GLib.file_get_contents(path(relative));
    return new TextDecoder().decode(bytes);
}

/**
 * Parse (without executing) ES module source with `node --check`, fed through stdin so no file
 * is written. Resolves with {ok, stderr}.
 */
async function checkSyntax(source) {
    const proc = new Gio.Subprocess({
        argv: ['node', '--check', '--input-type=module', '-'],
        flags: Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDERR_PIPE |
            Gio.SubprocessFlags.STDOUT_PIPE,
    });
    proc.init(null);
    const [, , stderr] = await proc.communicate_utf8_async(source, null);
    return {ok: proc.get_exit_status() === 0, stderr};
}

function importSpecifiers(source) {
    return [...source.matchAll(/^\s*(?:import|export)\b[^;]*?\bfrom\s+['"]([^'"]+)['"]/gm)]
        .map(match => match[1])
        .concat([...source.matchAll(/^\s*import\s+['"]([^'"]+)['"]/gm)].map(match => match[1]));
}

test('syntax check accepts valid module source and rejects a broken one', async () => {
    const valid = await checkSyntax("import X from 'y';\nexport class A { f() { return 1; } }\n");
    assertEqual(valid.ok, true, `valid source: ${valid.stderr}`);
    const broken = await checkSyntax("import X from 'y';\nexport class A { f() { return 1; }\n");
    assertEqual(broken.ok, false, 'unterminated class must fail: ');
    const bad = await checkSyntax('export const = 1;\n');
    assertEqual(bad.ok, false, 'invalid declaration must fail: ');
});

for (const file of [...GLUE_FILES, ...PURE_FILES]) {
    test(`${file} parses as an ES module`, async () => {
        const result = await checkSyntax(read(file));
        assertEqual(result.ok, true, `${result.stderr}\n`);
    });
}

for (const file of GLUE_FILES) {
    test(`${file} imports only existing files, gi:// and Shell modules`, () => {
        const specifiers = importSpecifiers(read(file));
        assertEqual(specifiers.length > 0, true, 'no imports found: ');
        for (const specifier of specifiers) {
            if (specifier.startsWith('.')) {
                const base = GLib.path_get_dirname(path(file));
                const target = GLib.build_filenamev([base, specifier]);
                assertEqual(GLib.file_test(target, GLib.FileTest.EXISTS), true,
                    `${file} imports missing ${specifier}: `);
            } else if (!specifier.startsWith('gi://') && !specifier.startsWith(SHELL_PREFIX)) {
                throw new Error(`${file} imports unexpected module ${specifier}`);
            }
        }
    });
}

for (const file of PURE_FILES) {
    test(`${file} imports nothing from gi:// or the Shell (CR-012)`, () => {
        for (const specifier of importSpecifiers(read(file))) {
            assertEqual(specifier.startsWith('.'), true, `${file} imports ${specifier}: `);
        }
    });
}

test('extension.js has no work at module scope beyond imports and the class', () => {
    const source = read('extension.js');
    assertEqual(/export default class \w+ extends Extension/.test(source), true);
    assertEqual(/\benable\(\)/.test(source) && /\bdisable\(\)/.test(source), true);
});

test('extension.js passes the click wrapper parameter through to the controller', () => {
    // Dropping {menuOpen} would let a closing click start a recording: the wrapper's parameter
    // must be the argument of the controller call.
    const source = read('extension.js');
    // Expected shape: `onPrimaryClick: options => this._controller?.onPrimaryClick(options) ...`
    const match = source.match(/onPrimaryClick:\s*\(?\s*(\w+)\s*\)?\s*=>[^,]*onPrimaryClick\((\w*)\)/);
    assertEqual(match !== null, true, 'onPrimaryClick wrapper not found');
    assertEqual(match[2], match[1], 'onPrimaryClick wrapper must forward its parameter');
});

test('metadata.json declares the required fields for GNOME Shell 50', () => {
    const metadata = JSON.parse(read('metadata.json'));
    assertEqual(metadata.uuid, 'meeting-scribe@meetingscribe.dev');
    assertEqual(metadata.name, 'Meeting Scribe');
    assertDeepEqual(metadata['shell-version'], ['50']);
    assertEqual(metadata['gettext-domain'], 'meeting-scribe');
    assertEqual(typeof metadata.url, 'string');
    assertEqual(metadata.url.startsWith('https://'), true);
    assertEqual(metadata.description.includes('meeting-scribe service'), true);
    assertEqual(GLib.path_get_basename(EXTENSION_DIR), metadata.uuid, 'directory is the uuid: ');
});

test('stylesheet.css defines the three indicator states', () => {
    const css = read('stylesheet.css');
    for (const name of ['meeting-scribe-recording', 'meeting-scribe-dimmed',
        'meeting-scribe-failed'])
        assertEqual(css.includes(`.${name}`), true, `${name}: `);
});

test('extension.js launches the review app through its desktop entry and hands it to the controller', () => {
    const source = read('extension.js');
    assertEqual(source.includes('Gio.DesktopAppInfo.new(REVIEW_DESKTOP_ENTRY)'), true);
    assertEqual(source.includes('dev.meetingscribe.Review.desktop'), false, 'one constant for the id: ');
    assertEqual(/\.launch\(\[\], null\)/.test(source), true, 'launch([], null): ');
    assertEqual(/\blaunchReview,/.test(source), true, 'the controller gets launchReview: ');
});

test('the desktop entry id in extension.js is the one packaging installs', () => {
    const id = read('lib/preferencesLauncher.js').match(/REVIEW_DESKTOP_ENTRY = '([^']+)'/)?.[1];
    const template = GLib.build_filenamev([EXTENSION_DIR, '..', '..', 'packaging', 'applications', `${id}.in`]);
    assertEqual(GLib.file_test(template, GLib.FileTest.EXISTS), true, `no template for ${id}: `);
});

test('extension.js opens the summary with the default handler through a non-blocking Gio call', () => {
    const source = read('extension.js');
    assertEqual(source.includes('Gio.AppInfo.launch_default_for_uri_async'), true);
    assertEqual(source.includes('.get_uri()'), true, 'the URI comes from the returned path: ');
    assertEqual(/launch_default_for_uri\(/.test(source), false, 'no blocking launch: ');
    assertEqual(/\bopenSummary,/.test(source), true, 'the controller gets openSummary: ');
});

test('the extension never reads the content of a summary or builds its path', () => {
    for (const file of [...GLUE_FILES, 'lib/meetingsClient.js']) {
        const source = read(file);
        for (const forbidden of ['load_contents', 'file_get_contents', 'read_bytes', 'summary.md'])
            assertEqual(source.includes(forbidden), false, `${file} uses ${forbidden}: `);
    }
});

// -- prefs.js (F5.10, AC-15): a launcher that runs in the Extensions app's own process ------------

test('prefs.js parses as an ES module and exports the preferences class', async () => {
    const source = read(PREFS_FILE);
    const result = await checkSyntax(source);
    assertEqual(result.ok, true, `${result.stderr}\n`);
    assertEqual(/export default class \w+ extends ExtensionPreferences/.test(source), true);
    assertEqual(/\bfillPreferencesWindow\(window\)/.test(source), true);
});

test('prefs.js imports only gi://, its own lib and the Extensions app module, never a Shell module', () => {
    const specifiers = importSpecifiers(read(PREFS_FILE));
    assertEqual(specifiers.length > 0, true);
    for (const specifier of specifiers) {
        if (specifier.startsWith('.')) {
            const target = GLib.build_filenamev([GLib.path_get_dirname(path(PREFS_FILE)), specifier]);
            assertEqual(GLib.file_test(target, GLib.FileTest.EXISTS), true, `missing ${specifier}: `);
        } else if (specifier !== PREFS_RESOURCE && !specifier.startsWith('gi://')) {
            throw new Error(`prefs.js imports unexpected module ${specifier}`);
        }
    }
    for (const forbidden of ['gi://Meta', 'gi://Clutter', 'gi://Shell', 'gi://St', SHELL_PREFIX])
        assertEqual(read(PREFS_FILE).includes(forbidden), false, `${forbidden}: `);
});

test('prefs.js contains no settings logic: no D-Bus, no file, no key', () => {
    const source = read(PREFS_FILE);
    for (const forbidden of ['Settings1', 'DBus', 'settings.json', 'load_contents', 'file_get_contents',
        'Secret', 'api key', 'API key', 'GetAll'])
        assertEqual(source.includes(forbidden), false, `prefs.js uses ${forbidden}: `);
});

test('prefs.js always adds a page (the host rejects an empty window) and closes after a launch', () => {
    const source = read(PREFS_FILE);
    assertEqual(source.includes('window.add('), true, 'a page is added: ');
    assertEqual(source.includes('Adw.StatusPage'), true);
    assertEqual(source.includes('make install'), true, 'the failure page shows the command: ');
    assertEqual(source.includes('Could not open Preferences'), true, 'the failure title: ');
    assertEqual(/idle_add_once[\s\S]*window\.close\(\)/.test(source), true, 'closes after fill: ');
    assertEqual(source.includes('launchPreferencesAction'), true);
});

test('extension.js hands the preferences launcher to the controller through the same helper', () => {
    const source = read('extension.js');
    assertEqual(source.includes('launchPreferencesAction'), true);
    assertEqual(/\blaunchPreferences,/.test(source), true, 'the controller gets launchPreferences: ');
});

test('the desktop entry template declares the preferences action the launcher asks for', () => {
    const template = GLib.build_filenamev([EXTENSION_DIR, '..', '..', 'packaging', 'applications',
        'dev.meetingscribe.Review.desktop.in']);
    const [, bytes] = GLib.file_get_contents(template);
    const text = new TextDecoder().decode(bytes);
    assertEqual(/^Actions=preferences;$/m.test(text), true, 'Actions key: ');
    assertEqual(/\[Desktop Action preferences\][\s\S]*^Exec=@EXEC@ --preferences$/m.test(text), true,
        'the action runs the launcher with --preferences: ');
});
