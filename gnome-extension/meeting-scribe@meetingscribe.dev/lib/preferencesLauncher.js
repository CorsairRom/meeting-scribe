export const REVIEW_DESKTOP_ENTRY = 'dev.meetingscribe.Review.desktop';
export const PREFERENCES_ACTION = 'preferences';

/**
 * Ask the review app to open its Preferences dialog, through the desktop action the installer
 * writes (`Exec=<launcher> --preferences`). Shared by the panel menu and `prefs.js`, which run in
 * different processes. Pure: the lookup is injected, so it is tested under plain gjs.
 *
 * @param {Function} lookup - `lookup(desktopId)` returns an app info (`list_actions`,
 *     `launch_action`) or null when the entry is not installed
 * @returns {boolean} false when the entry or its action is missing (an install older than this
 *     feature has the entry but not the action): the caller shows `make install`
 */
export function launchPreferencesAction(lookup) {
    const app = lookup(REVIEW_DESKTOP_ENTRY);
    if (!app || !app.list_actions().includes(PREFERENCES_ACTION))
        return false;
    app.launch_action(PREFERENCES_ACTION, null);
    return true;
}
