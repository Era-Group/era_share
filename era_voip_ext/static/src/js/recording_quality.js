/** @odoo-module **/

import { SessionRecorder } from "@voip/core/session_recorder";
import { patch } from "@web/core/utils/patch";

// Odoo records call audio at 8 kbps mono (G.729 emulation), which sounds
// muffled. Raise it to a clearer bitrate. Both the chatter recorder
// (Session.record) and the transcription recorder
// (Session.startTranscriptionRecording) build a `new SessionRecorder`, so
// patching the recorder here upgrades both without touching either flow.
const ERA_RECORDING_BITRATE = 48000; // bits/s, opus mono — clear speech

patch(SessionRecorder.prototype, {
    /** @override */
    start() {
        // The base constructor already built a MediaRecorder at 8 kbps but has
        // not started it yet. Rebuild it from the very same merged stream at a
        // higher bitrate, then let the original start() run it. On any failure
        // we leave the base recorder untouched so recording still works.
        try {
            const base = this._recorder;
            const stream = base && base.stream;
            if (stream && base.state === "inactive") {
                const hd = new MediaRecorder(stream, {
                    audioBitsPerSecond: ERA_RECORDING_BITRATE,
                    mimeType: base.mimeType || this.outputMimeType,
                });
                hd.addEventListener("stop", (event) => this._onStop(event));
                hd.addEventListener("dataavailable", (event) => this._onDataAvailable(event));
                hd.addEventListener("error", (event) => this._onError(event));
                this._recorder = hd;
            }
        } catch (err) {
            console.warn(
                "era_voip_ext: could not raise recording bitrate; using default.",
                err
            );
        }
        return super.start();
    },
});
