import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import {launchPreferencesAction} from './lib/preferencesLauncher.js';

// Runs in the Extensions app's own process, without the Shell. It builds no settings UI: it asks
// the review app to open its Preferences dialog and closes. The host rejects a window without a
// page, so a short status page is always added, and the window closes once it is filled.
export default class MeetingScribePreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const _ = text => this.gettext(text);
        let launched = false;
        try {
            launched = launchPreferencesAction(id => Gio.DesktopAppInfo.new(id));
        } catch (_e) {
            launched = false;
        }

        const status = launched
            ? new Adw.StatusPage({
                title: _('Opening Preferences'),
                description: _('The settings open in Meeting Scribe Review.'),
                icon_name: 'preferences-system-symbolic',
            })
            : new Adw.StatusPage({
                title: _('Could not open Preferences'),
                description: _('Meeting Scribe Review is not installed or is out of date. Run <tt>make install</tt> in the meeting-scribe folder, then try again.'),
                icon_name: 'dialog-warning-symbolic',
            });
        const group = new Adw.PreferencesGroup();
        group.add(status);
        const page = new Adw.PreferencesPage();
        page.add(group);
        window.add(page);

        if (launched)
            GLib.idle_add_once(GLib.PRIORITY_DEFAULT, () => window.close());
    }
}
