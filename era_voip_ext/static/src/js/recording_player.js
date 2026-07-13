/** @odoo-module **/

import { Component, reactive, useState, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

// A single shared audio element so only one recording plays at a time across
// rows. `playingId` is reactive and drives every row's icon; the actual
// HTMLAudioElement is kept out of the reactive graph (it is a DOM object).
const playerState = reactive({ playingId: null });
let currentAudio = null;

function stopPlayback() {
    if (currentAudio) {
        currentAudio.pause();
        currentAudio.removeAttribute("src");
        currentAudio.load();
        currentAudio = null;
    }
    playerState.playingId = null;
}

function startPlayback(id, url) {
    stopPlayback();
    const audio = new Audio(url);
    currentAudio = audio;
    playerState.playingId = id;
    audio.addEventListener("ended", stopPlayback);
    audio.addEventListener("error", stopPlayback);
    return audio.play(); // Promise; rejects if the browser blocks playback
}

class VoipRecordingPlayer extends Component {
    static template = xml`
        <button type="button"
                t-att-class="'btn btn-link p-0 ' + (isPlaying ? 'text-danger' : 'text-primary')"
                t-att-title="title"
                t-att-disabled="state.loading"
                t-on-click.stop.prevent="onClick">
            <i t-att-class="'fa ' + iconClass" style="font-size: 1.7rem;"/>
        </button>`;
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ loading: false });
        // Subscribe this row to the shared player so its icon reflects
        // start/stop triggered from any row (or when playback ends).
        this.player = useState(playerState);
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
            await startPlayback(id, url);
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
