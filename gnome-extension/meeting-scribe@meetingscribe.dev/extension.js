import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {GLibScheduler} from './lib/glibScheduler.js';
import {IndicatorController} from './lib/indicatorController.js';
import {MeetingsClient} from './lib/meetingsClient.js';
import {PanelIndicator} from './lib/panelIndicator.js';
import {RecordingClient} from './lib/recordingClient.js';

const BUS_NAME = 'dev.meetingscribe.Service';
const OBJECT_PATH = '/dev/meetingscribe/Service';

export default class MeetingScribeExtension extends Extension {
    enable() {
        const _ = text => this.gettext(text);
        this._scheduler = new GLibScheduler();
        this._indicator = new PanelIndicator({
            name: _('Meeting Scribe'),
            onPrimaryClick: options => this._controller?.onPrimaryClick(options) ?? 'none',
            onMenuAction: id => this._controller?.onMenuAction(id),
        });
        this._client = new RecordingClient({
            busName: BUS_NAME,
            objectPath: OBJECT_PATH,
            onChange: snapshot => this._controller?.handleChange(snapshot),
        });
        this._meetings = new MeetingsClient({
            busName: BUS_NAME,
            objectPath: OBJECT_PATH,
            onChange: () => this._controller?.handleProcessingChange(),
        });
        this._controller = new IndicatorController({
            client: this._client,
            meetings: this._meetings,
            view: this._indicator,
            scheduler: this._scheduler,
            notify: (title, body) => Main.notify(title, body),
            _,
        });
        Main.panel.addToStatusArea(this.uuid, this._indicator);
        this._controller.refresh();
        this._client.connect();
        this._meetings.connect();
    }

    disable() {
        this._client?.destroy();
        this._client = null;
        this._meetings?.destroy();
        this._meetings = null;
        this._controller?.destroy();
        this._controller = null;
        this._indicator?.destroy();
        this._indicator = null;
        this._scheduler?.destroy();
        this._scheduler = null;
    }
}
