import {
    ICON_FAILED, ICON_RECORDING, ICON_UNAVAILABLE, PROCESSING_HINT,
} from '../../gnome-extension/meeting-scribe@meetingscribe.dev/lib/indicatorModel.js';

/**
 * Installed status icon names. They do not end in `-symbolic`: Plasma recolors symbolic icons to
 * the theme color, and the recording icon must stay red (spec desktop-tray section 4).
 */
export const TRAY_ICONS = Object.freeze({
    idle: 'dev.meetingscribe.Tray-idle',
    processing: 'dev.meetingscribe.Tray-processing',
    recording: 'dev.meetingscribe.Tray-recording',
    failed: 'dev.meetingscribe.Tray-failed',
    unavailable: 'dev.meetingscribe.Tray-unavailable',
});

const ELAPSED_SEPARATOR = ' · ';

function iconName({icon, label}) {
    switch (icon) {
    case ICON_RECORDING:
        return TRAY_ICONS.recording;
    case ICON_FAILED:
        return TRAY_ICONS.failed;
    case ICON_UNAVAILABLE:
        return TRAY_ICONS.unavailable;
    default:
        return label === PROCESSING_HINT ? TRAY_ICONS.processing : TRAY_ICONS.idle;
    }
}

// The panel label holds the elapsed time only while recording; a tray icon cannot show text
// (the Plasma 6 tray reads no label), so the time moves to the tooltip and the first status row.
function elapsedOf({icon, label}) {
    return icon === ICON_RECORDING && label !== '' && label !== PROCESSING_HINT ? label : '';
}

function withElapsedOnFirstStatus(menuItems, elapsed) {
    const index = menuItems.findIndex(item => item.kind === 'status');
    if (index < 0)
        return menuItems;
    return menuItems.map((item, i) =>
        i === index ? {...item, text: `${item.text}${ELAPSED_SEPARATOR}${elapsed}`} : item);
}

/**
 * Turn the extension's view model into what the tray shows. Pure: the view model is not modified.
 *
 * @param {{icon: string, label: string, menuItems: object[], tooltip: string}} model - from
 *     `viewModel()` in the extension's `indicatorModel.js`
 * @returns {{iconName: string, tooltip: string, menuItems: object[]}} the tray view
 */
export function trayModel(model) {
    const elapsed = elapsedOf(model);
    if (elapsed === '')
        return {iconName: iconName(model), tooltip: model.tooltip, menuItems: model.menuItems};
    return {
        iconName: iconName(model),
        tooltip: `${model.tooltip}${ELAPSED_SEPARATOR}${elapsed}`,
        menuItems: withElapsedOnFirstStatus(model.menuItems, elapsed),
    };
}
