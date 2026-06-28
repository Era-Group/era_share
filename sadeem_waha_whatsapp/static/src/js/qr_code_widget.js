/** @odoo-module **/

import { registry } from '@web/core/registry';
import { Component, onMounted, onWillUnmount, useState } from '@odoo/owl';
import { useService } from '@web/core/utils/hooks';

class QRCodeWidget extends Component {
    static template = 'sadeem_waha_whatsapp.QRCodeWidget';

    setup() {
        this.rpc = useService('rpc');
        this.state = useState({
            qrCodeImage: null,
            isLoading: false,
            error: null
        });

        onMounted(() => {
            this.startPolling();
        });

        onWillUnmount(() => {
            this.stopPolling();
        });
    }

    startPolling() {
        this.pollInterval = setInterval(() => {
            this.refreshQRCode();
        }, 10000); // Poll every 10 seconds
    }

    stopPolling() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
        }
    }

    async refreshQRCode() {
        if (this.props.record.data.status !== 'scan_qr_code') {
            return;
        }

        this.state.isLoading = true;
        try {
            await this.rpc('/web/dataset/call_kw', {
                model: 'sadeem.waha.session',
                method: 'action_get_qr_code',
                args: [[this.props.record.data.id]],
                kwargs: {}
            });

            // Refresh the form view to get updated QR code
            await this.props.record.load();
            this.state.qrCodeImage = this.props.record.data.qr_code_image;

        } catch (error) {
            this.state.error = error.message;
        } finally {
            this.state.isLoading = false;
        }
    }

    async manualRefresh() {
        await this.refreshQRCode();
    }
}

QRCodeWidget.template = 'sadeem_waha_whatsapp.QRCodeWidget';
QRCodeWidget.props = ['record'];

registry.category('fields').add('qr_code_widget', QRCodeWidget);