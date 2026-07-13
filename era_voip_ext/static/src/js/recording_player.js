/** @odoo-module **/

import { Component, onWillUnmount, useRef, useState, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

// Floating (draggable) player window. It owns its own <audio> element, so
// closing the dialog — via the X, Escape, the close button, or navigating
// away (which unmounts it) — pauses and releases the audio. Nothing keeps
// playing in the background.
class RecordingPlayerDialog extends Component {
    static components = { Dialog };
    static template = xml`
        <Dialog title="props.title" size="'md'">
            <audio t-ref="audio" t-att-src="props.url" controls="controls"
                   autoplay="autoplay" preload="auto" style="width: 100%;"/>
            <t t-set-slot="footer">
                <button class="btn btn-secondary" t-on-click="onClose">إغلاق</button>
            </t>
        </Dialog>`;
    static props = { url: String, title: String, close: Function };

    setup() {
        this.audioRef = useRef("audio");
        onWillUnmount(() => {
            const audio = this.audioRef.el;
            if (audio) {
                audio.pause();
                audio.removeAttribute("src");
                audio.load();
            }
        });
    }

    onClose() {
        this.props.close();
    }
}

// Keep at most one player window open at a time.
let closeCurrentDialog = null;

class VoipRecordingPlayer extends Component {
    static template = xml`
        <button type="button" class="btn btn-link p-0 text-primary"
                t-att-title="title" t-att-disabled="state.loading"
                t-on-click.stop.prevent="onClick">
            <i t-att-class="'fa ' + (state.loading ? 'fa-spinner fa-spin' : 'fa-play-circle')"/>
        </button>`;
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({ loading: false });
    }

    get resId() {
        return this.props.record.resId;
    }
    get title() {
        return _t("تشغيل التسجيل");
    }

    async onClick() {
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
            if (typeof closeCurrentDialog === "function") {
                closeCurrentDialog();
            }
            const remove = this.dialog.add(
                RecordingPlayerDialog,
                { url, title: _t("تسجيل المكالمة") },
                {
                    onClose: () => {
                        if (closeCurrentDialog === remove) {
                            closeCurrentDialog = null;
                        }
                    },
                }
            );
            closeCurrentDialog = remove;
        } catch (error) {
            this.notification.add(_t("تعذّر تشغيل التسجيل."), { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }
}

registry.category("view_widgets").add("era_voip_recording_player", {
    component: VoipRecordingPlayer,
});
