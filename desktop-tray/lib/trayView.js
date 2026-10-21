import {trayModel} from './trayModel.js';

/**
 * The `view` the extension's IndicatorController renders into: shows each view model on the
 * StatusNotifierItem (icon and tooltip) and on the dbusmenu menu (rows).
 */
export class TrayView {
    /**
     * @param {object} options
     * @param {{update: Function}} options.item - a StatusNotifierItem
     * @param {{setItems: Function}} options.menu - a DBusMenu
     */
    constructor({item, menu}) {
        this._item = item;
        this._menu = menu;
    }

    /**
     * Show a view model. Unchanged values send nothing; the elapsed time while recording is a
     * tooltip and one row label, so an open menu stays open.
     *
     * @param {object} model - from `viewModel()` in the extension's `indicatorModel.js`
     */
    render(model) {
        const {iconName, tooltip, menuItems} = trayModel(model);
        this._item.update({iconName, tooltip});
        this._menu.setItems(menuItems);
    }
}
