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
        let reported = false;
        const id = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, seconds, () => {
            // A throwing callback would make GJS drop the source while it stays tracked here.
            // Log only the first failure so a deterministic error cannot flood the journal.
            try {
                fn();
            } catch (e) {
                if (!reported)
                    logError(e, 'meeting-scribe: scheduled callback failed (further failures not logged)');
                reported = true;
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
