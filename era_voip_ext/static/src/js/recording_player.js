/** @odoo-module **/

import { Component, onWillUnmount, reactive, useState, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

// A single shared audio element so only one recording plays at a time across
// rows. `playingId` is reactive and drives every row's icon; the actual
// HTMLAudioElement is kept out of the reactive graph (it is a DOM object).
// `currentToken` identifies the widget instance that started playback, so that
// instance can stop the audio when it is destroyed (e.g. the user navigates
// away from the list) — otherwise playback would keep going across pages.
const playerState = reactive({ playingId: null });
let currentAudio = null;
let currentToken = null;
let tokenSeq = 0;

function stopPlayback() {
    if (currentAudio) {
        currentAudio.pause();
        currentAudio.removeAttribute("src");
        currentAudio.load();
        currentAudio = null;
    }
    currentToken = null;
    playerState.playingId = null;
}

function startPlayback(id, url) {
    stopPlayback();
    const token = ++tokenSeq;
    currentToken = token;
    const audio = new Audio(url);
    currentAudio = audio;
    playerState.playingId = id;
    audio.addEventListener("ended", stopPlayback);
    audio.addEventListener("error", stopPlayback);
    // Promise rejects if the browser blocks playback; caller handles it.
    return { token, promise: audio.play() };
}

class VoipRecordingPlayer extends Component {
    static template = xml`
        <button type="button"
                t-att-class="'btn btn-link p-0 ' + (isPlaying ? 'text-danger' : 'text-primary')"
                t-att-title="title"
                t-att-disabled="state.loading"
                t-on-click.stop.prevent="onClick">
            <i t-att-class="'fa ' + iconClass"/>
        </button>`;
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ loading: false });
        // Subscribe this row to the shared player so its icon reflects
        // start/stop triggered from any row (or when playback ends).
        this.player = useState(playerState);
        this._playToken = null;
        // Stop playback when the widget that started it is destroyed, so the
        // audio doesn't keep playing after leaving the list / changing view.
        onWillUnmount(() => {
            if (this._playToken && this._playToken === currentToken) {
                stopPlayback();
            }
        });
    }

    get resId() {
        return this.props.record.resId;
    }
    get isPlaying() {
        return this.player.playingId === this.resId;
    }
    get title() {
        return this.isPlaying ? _t("إيقاف") : _t("تشغيل التسجيل");
    }
    get iconClass() {
        if (this.state.loading) {
            return "fa-spinner fa-spin";
        }
        return this.isPlaying ? "fa-stop-circle" : "fa-play-circle";
    }

    async onClick() {
        if (this.isPlaying) {
            stopPlayback();
            return;
        }
        const id = this.resId;
        if (!id) {
            return;
        }
        this.state.loading = true;
        try {
            const url = await this.orm.call("voip.call", "get_recording_url", [[id]]);
            if (!url) {
                this.notification.add(_t("لا يوجد تسجيل لهذه المكالمة."), {
                    type: "warning",
                });
                return;
            }
            const { token, promise } = startPlayback(id, url);
            this._playToken = token;
            await promise;
        } catch (error) {
            stopPlayback();
            this.notification.add(_t("تعذّر تشغيل التسجيل."), { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }
}

registry.category("view_widgets").add("era_voip_recording_player", {
    component: VoipRecordingPlayer,
});
