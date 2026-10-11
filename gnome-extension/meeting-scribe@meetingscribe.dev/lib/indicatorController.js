import {viewModel, errorMessage, launchFailureMessage} from './indicatorModel.js';

const TICK_SECONDS = 1;

/**
 * Container between the D-Bus client and the panel view. Pure logic: the Shell, the clock and
 * the notification API are injected, so it runs (and is tested) under plain gjs.
 */
export class IndicatorController {
    /**
     * @param {object} options
     * @param {object} options.client - `snapshot`, `start()` and `stop()` (a RecordingClient)
     * @param {object} [options.meetings] - `snapshot` and `reprocess(id)` (a MeetingsClient);
     *     omit it and the menu simply shows no processing state
     * @param {{render: Function}} options.view - receives the view model on every change
     * @param {{every: Function, cancel: Function}} options.scheduler - `every(seconds, fn)`
     *     returns a handle that `cancel(handle)` releases
     * @param {Function} options.notify - `notify(title, body)` shows an error to the user
     * @param {Function} [options.launchReview] - opens the review app (no argument, no meeting
     *     data); returns false or throws when it cannot. Omit it and the action does nothing
     * @param {Function} [options._] - translation function (identity by default)
     */
    constructor({client, meetings = null, view, scheduler, notify, launchReview = null, _ = s => s}) {
        this._client = client;
        this._meetings = meetings;
        this._view = view;
        this._scheduler = scheduler;
        this._notify = notify;
        this._launchReview = launchReview;
        this._ = _;
        this._tick = null;
        this._destroyed = false;
    }

    /** Render the client's current snapshot (call once after wiring). */
    refresh() {
        this.handleChange(this._client.snapshot);
    }

    /** Meetings client `onChange` hook: the pipeline changed, render again. */
    handleProcessingChange() {
        if (!this._destroyed)
            this._view.render(this._model(this._client.snapshot));
    }

    /** Client `onChange` hook: render and keep the tick in line with the state. */
    handleChange(snapshot) {
        if (this._destroyed)
            return;
        const recording = snapshot.available && snapshot.state === 'RECORDING';
        if (recording && this._tick === null)
            this._tick = this._scheduler.every(TICK_SECONDS, () => this._onTick());
        else if (!recording)
            this._stopTick();
        this._view.render(this._model(snapshot));
    }

    /**
     * Handle the primary click. Starts a recording when the model says so. A click while the
     * menu is open only closes it: the state may have changed since it opened, and closing a
     * menu must never start a recording.
     *
     * @param {object} [options]
     * @param {boolean} [options.menuOpen] - whether the view's menu is currently open
     * @returns {'start'|'open-menu'|'close-menu'|'none'} what the view must still do
     */
    onPrimaryClick({menuOpen = false} = {}) {
        if (this._destroyed)
            return 'none';
        if (menuOpen)
            return 'close-menu';
        const {clickAction} = this._model(this._client.snapshot);
        if (clickAction === 'start')
            this._run(() => this._client.start());
        return clickAction;
    }

    /**
     * Handle a menu action item.
     *
     * @param {string} id - `start`, `stop`, `reprocess` (the failed meeting shown last) or `review`
     * @returns {Promise<void>} settles once the call (and any notification) is done
     */
    onMenuAction(id) {
        if (this._destroyed)
            return Promise.resolve();
        if (id === 'start')
            return this._run(() => this._client.start());
        if (id === 'stop')
            return this._run(() => this._client.stop());
        if (id === 'reprocess')
            return this._reprocess();
        if (id === 'review')
            return this._review();
        return Promise.resolve();
    }

    /** Cancel the tick and ignore everything afterwards. Idempotent. */
    destroy() {
        this._destroyed = true;
        this._stopTick();
    }

    _onTick() {
        if (!this._destroyed)
            this._view.render(this._model(this._client.snapshot));
    }

    _model(snapshot) {
        return viewModel({...snapshot, processing: this._meetings?.snapshot}, this._);
    }

    _reprocess() {
        const last = this._meetings?.snapshot.last;
        if (last?.state !== 'FAILED')
            return Promise.resolve();
        return this._run(() => this._meetings.reprocess(last.meetingId));
    }

    // The failure text is fixed: a launch error may carry private paths or text and is never shown.
    async _review() {
        if (this._launchReview === null)
            return;
        let launched = false;
        try {
            launched = await this._launchReview() !== false;
        } catch (_e) {
            launched = false;
        }
        if (!launched && !this._destroyed)
            this._notify(this._('Meeting Scribe'), launchFailureMessage(this._));
    }

    _stopTick() {
        if (this._tick === null)
            return;
        const handle = this._tick;
        this._tick = null;
        this._scheduler.cancel(handle);
    }

    // A client call resolves with null when another call is in flight: nothing to report.
    async _run(call) {
        try {
            await call();
        } catch (e) {
            if (!this._destroyed)
                this._notify(this._('Meeting Scribe'), errorMessage(e?.name, e?.message, this._));
        }
    }
}
