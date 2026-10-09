import GLib from 'gi://GLib';

/**
 * Scheduler for the indicator controller, backed by GLib timeout sources. Every source it
 * creates is tracked so `destroy()` leaves none behind (EGO review rule).
 */
export class GLibScheduler {
    constructor() {
        this._sources = new Set();
    }

    /**
     * @param {number} seconds - repeat interval
     * @param {Function} fn - called on every tick
     * @returns {number} handle for `cancel()`
     */
    every(seconds, fn) {
        const id = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, seconds, () => {
            // A throwing callback would make GJS drop the source while it stays tracked here.
            try {
                fn();
            } catch (e) {
                logError(e, 'meeting-scribe: indicator tick failed');
            }
            return GLib.SOURCE_CONTINUE;
        });
        this._sources.add(id);
        return id;
    }

    /** @param {number} id - handle returned by `every()` */
    cancel(id) {
        if (this._sources.delete(id))
            GLib.source_remove(id);
    }

    /** Cancel every pending source. */
    destroy() {
        for (const id of [...this._sources])
            this.cancel(id);
    }
}
