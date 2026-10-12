import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {ICON_RECORDING, ICON_FAILED} from './indicatorModel.js';

const STYLE_RECORDING = 'meeting-scribe-recording';
const STYLE_DIMMED = 'meeting-scribe-dimmed';
const STYLE_FAILED = 'meeting-scribe-failed';

// PanelMenu.Button (GNOME Shell 50) toggles its menu from a Clutter.ClickGesture that calls
// `this.menu.toggle()`. Replacing the menu with this subclass lets the controller decide
// what a primary activation does: start recording (no menu) or open the menu.
class IndicatorMenu extends PopupMenu.PopupMenu {
    constructor(sourceActor, onPrimaryClick) {
        super(sourceActor, 0.5, St.Side.TOP);
        this._onPrimaryClick = onPrimaryClick;
    }

    toggle() {
        const action = this._onPrimaryClick({menuOpen: this.isOpen});
        if (action === 'open-menu' || action === 'close-menu')
            super.toggle();
    }
}

/** Top-bar button that renders the indicator view model. Holds no logic of its own. */
export const PanelIndicator = GObject.registerClass(
class PanelIndicator extends PanelMenu.Button {
    /**
     * @param {object} options
     * @param {string} options.name - accessible name of the button
     * @param {Function} options.onPrimaryClick - takes {menuOpen}; returns 'start', 'open-menu', 'close-menu' or 'none'
     * @param {Function} options.onMenuAction - called with 'start', 'stop', 'reprocess', 'review', 'summary' or 'preferences'
     * @param {Function} [options.onMenuOpen] - called each time the menu opens
     */
    _init({name, onPrimaryClick, onMenuAction, onMenuOpen = () => {}}) {
        super._init(0.5, name, true);
        this.setMenu(new IndicatorMenu(this, onPrimaryClick));
        this._onMenuAction = onMenuAction;
        this.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                onMenuOpen();
        });
        this._itemsKey = null;

        const box = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        this._glyph = new St.Label({y_align: Clutter.ActorAlign.CENTER});
        this._timer = new St.Label({y_align: Clutter.ActorAlign.CENTER, visible: false});
        box.add_child(this._glyph);
        box.add_child(this._timer);
        this.add_child(box);
    }

    /** @param {object} model - the view model from `viewModel()` */
    render(model) {
        this._glyph.text = model.icon;
        this._timer.text = model.label;
        this._timer.visible = model.label !== '';
        this.accessible_name = model.tooltip;
        this._setStyle(STYLE_DIMMED, model.dimmed);
        this._setStyle(STYLE_RECORDING, model.icon === ICON_RECORDING);
        this._setStyle(STYLE_FAILED, model.icon === ICON_FAILED);
        this._renderItems(model.menuItems);
        // The menu closes by itself when it has nothing left to show.
        if (model.menuItems.length === 0)
            this.menu.close();
    }

    _setStyle(name, on) {
        if (on)
            this.add_style_class_name(name);
        else
            this.remove_style_class_name(name);
    }

    // Rebuilt only when the items change, so the 1 s tick never disturbs an open menu.
    _renderItems(items) {
        const key = JSON.stringify(items);
        if (key === this._itemsKey)
            return;
        this._itemsKey = key;
        this.menu.removeAll();
        for (const item of items) {
            if (item.kind === 'separator') {
                this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            } else if (item.kind === 'action') {
                this.menu.addAction(item.text, () => this._onMenuAction(item.action));
            } else {
                this.menu.addMenuItem(new PopupMenu.PopupMenuItem(item.text,
                    {reactive: false, can_focus: false}));
            }
        }
    }
});
