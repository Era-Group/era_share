/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";
import { Registerer } from "@voip/core/registerer";
import { UserAgent } from "@voip/core/user_agent_service";

const originalInit = UserAgent.prototype.init;
const originalMakeCall = UserAgent.prototype.makeCall;
const originalAttemptReconnection = UserAgent.prototype.attemptReconnection;
const originalOnTransportDisconnected = UserAgent.prototype._onTransportDisconnected;
const originalRegister = Registerer.prototype.register;

const TAB_ID_KEY = "odoo_voip_one_tab_id";
const OWNER_KEY = "odoo_voip_one_tab_owner";
const HEARTBEAT_KEY = "odoo_voip_one_tab_hb";
const CHANNEL_NAME = "odoo_voip_one_tab_channel";
const CHANNEL_STORAGE_KEY = "odoo_voip_one_tab_channel_message";

const HEARTBEAT_MS = 2000;
const HEARTBEAT_STALE_MS = 6000;
const ACK_TIMEOUT_MS = 2000;
const TABID_CHECK_TIMEOUT_MS = 800;
const SERVER_HEARTBEAT_MS = 4000;

const randomId = () => Math.random().toString(16).slice(2);
const DEBUG_QUERY = "voip_one_tab_debug";
const IS_DEBUG = new URLSearchParams(window.location.search).has(DEBUG_QUERY);
const debugLog = (...args) => {
    if (IS_DEBUG) {
        console.warn(...args);
    }
};
const guardState =
    window.__voipOneTabGuardState ||
    (window.__voipOneTabGuardState = { enabled: true, allow: false });
const sipPatchState =
    window.__voipOneTabSipPatchState ||
    (window.__voipOneTabSipPatchState = { patched: false });
let sipPatchWarned = false;

window.__voipOneTabLoaded = true;
debugLog("VoIP One Tab: asset loaded");

function patchSipUserAgent() {
    const SIP = window.SIP;
    if (!SIP?.UserAgent?.prototype) {
        if (IS_DEBUG && !sipPatchWarned) {
            sipPatchWarned = true;
            debugLog("VoIP One Tab: SIP not available yet");
        }
        return false;
    }
    if (SIP.UserAgent.prototype.__voipOneTabPatched) {
        sipPatchState.patched = true;
        return true;
    }
    const originalStart = SIP.UserAgent.prototype.start;
    const originalReconnect = SIP.UserAgent.prototype.reconnect;
    SIP.UserAgent.prototype.start = async function () {
        const enabled = guardState.enabled;
        const allow = guardState.allow;
        if (enabled && !allow) {
            debugLog("VoIP One Tab: blocked SIP.UserAgent.start()", { enabled, allow });
            return;
        }
        debugLog("VoIP One Tab: allowed SIP.UserAgent.start()", { enabled, allow });
        return typeof originalStart === "function" ? originalStart.apply(this, arguments) : undefined;
    };
    SIP.UserAgent.prototype.reconnect = async function () {
        const enabled = guardState.enabled;
        const allow = guardState.allow;
        if (enabled && !allow) {
            debugLog("VoIP One Tab: blocked SIP.UserAgent.reconnect()", { enabled, allow });
            return;
        }
        debugLog("VoIP One Tab: allowed SIP.UserAgent.reconnect()", { enabled, allow });
        return typeof originalReconnect === "function"
            ? originalReconnect.apply(this, arguments)
            : undefined;
    };
    SIP.UserAgent.prototype.__voipOneTabPatched = true;
    sipPatchState.patched = true;
    debugLog("VoIP One Tab: patched SIP.UserAgent");
    return true;
}

if (!window.__voipOneTabSipPatchInterval) {
    window.__voipOneTabSipPatchInterval = setInterval(() => {
        if (patchSipUserAgent()) {
            clearInterval(window.__voipOneTabSipPatchInterval);
            window.__voipOneTabSipPatchInterval = null;
        }
    }, 500);
}
patchSipUserAgent();

class VoipOneTabCoordinator {
    constructor(userAgent) {
        this.userAgent = userAgent;
        this.instanceId = this._getOrCreateInstanceId();
        this.tabId = this._getOrCreateTabId();
        this.serverId = this.instanceId;
        this.tabIdVerified = false;
        this.pendingAcks = new Map();
        this.pendingTabChecks = new Map();
        this.channel = this._createChannel();
        this.heartbeatId = null;
        this.serverHeartbeatId = null;
        this.serverLockActive = false;
        this._onChannelMessage = this._onChannelMessage.bind(this);
        this._onStorageMessage = this._onStorageMessage.bind(this);
        if (this.channel) {
            this.channel.addEventListener("message", this._onChannelMessage);
        }
        window.addEventListener("storage", this._onStorageMessage);
    }

    destroy() {
        if (this.channel) {
            this.channel.removeEventListener("message", this._onChannelMessage);
            this.channel.close();
        }
        window.removeEventListener("storage", this._onStorageMessage);
        this._stopHeartbeat();
    }

    async acquireOwnership() {
        await this._verifyTabIdUniqueness();
        const owner = this._getOwner();
        const heartbeat = this._getHeartbeat();
        const now = Date.now();
        const heartbeatFresh =
            owner &&
            heartbeat &&
            owner.tabId === heartbeat.tabId &&
            owner.instanceId === heartbeat.instanceId &&
            now - heartbeat.ts <= HEARTBEAT_STALE_MS;

        debugLog("VoIP One Tab: ownership check", {
            tabId: this.tabId,
            instanceId: this.instanceId,
            owner,
            heartbeat,
            heartbeatFresh,
        });

        if (
            !owner ||
            (owner.tabId === this.tabId && owner.instanceId === this.instanceId) ||
            !heartbeatFresh
        ) {
            return this._claimOwnership();
        }

        const requestId = `${this.tabId}-${now}`;
        debugLog("VoIP One Tab: force logout request", {
            owner: owner?.tabId,
            requestId,
        });
        const ackPromise = this._waitForAck(requestId);
        this._broadcast({
            type: "FORCE_LOGOUT",
            targetTabId: owner.tabId,
            targetInstanceId: owner.instanceId,
            requestId,
        });
        const ack = await ackPromise;
        if (!ack) {
            debugLog("VoIP One Tab: force logout timeout", {
                owner: owner?.tabId,
                requestId,
            });
            return false;
        }
        return this._claimOwnership();
    }

    releaseOwnership({ keepalive = false } = {}) {
        this._stopHeartbeat();
        this._stopServerHeartbeat();
        const owner = this._getOwner();
        if (owner?.tabId === this.tabId) {
            this._removeSafe(OWNER_KEY);
            this._removeSafe(HEARTBEAT_KEY);
        }
        this._serverRelease({ keepalive });
    }

    _createChannel() {
        if (!("BroadcastChannel" in window)) {
            return null;
        }
        try {
            return new BroadcastChannel(CHANNEL_NAME);
        } catch (error) {
            console.warn("VoIP One Tab: BroadcastChannel failed", error);
            return null;
        }
    }

    _getOrCreateTabId() {
        const existing = window.sessionStorage.getItem(TAB_ID_KEY);
        if (existing) {
            return existing;
        }
        const id = window.crypto?.randomUUID?.() || `voip-tab-${Date.now()}-${randomId()}`;
        window.sessionStorage.setItem(TAB_ID_KEY, id);
        return id;
    }

    _getOrCreateInstanceId() {
        if (window.__voipOneTabInstanceId) {
            return window.__voipOneTabInstanceId;
        }
        const id = window.crypto?.randomUUID?.() || `voip-inst-${Date.now()}-${randomId()}`;
        window.__voipOneTabInstanceId = id;
        return id;
    }

    async _verifyTabIdUniqueness() {
        if (this.tabIdVerified) {
            return true;
        }
        const requestId = `${this.instanceId}-${Date.now()}`;
        const checkPromise = this._waitForTabCheck(requestId);
        this._broadcast({ type: "CHECK_TAB_ID", requestId, tabId: this.tabId });
        const conflict = await checkPromise;
        const owner = this._getOwner();
        const heartbeat = this._getHeartbeat();
        const now = Date.now();
        const heartbeatFresh =
            owner &&
            heartbeat &&
            owner.tabId === heartbeat.tabId &&
            owner.instanceId === heartbeat.instanceId &&
            now - heartbeat.ts <= HEARTBEAT_STALE_MS;
        const duplicateOwner =
            owner &&
            owner.tabId === this.tabId &&
            owner.instanceId &&
            owner.instanceId !== this.instanceId &&
            heartbeatFresh;
        const legacyDuplicate =
            owner &&
            owner.tabId === this.tabId &&
            !owner.instanceId &&
            heartbeatFresh;
        if (conflict || duplicateOwner || legacyDuplicate) {
            const newId = window.crypto?.randomUUID?.() || `voip-tab-${Date.now()}-${randomId()}`;
            debugLog("VoIP One Tab: duplicate tabId detected, regenerating", {
                old: this.tabId,
                new: newId,
            });
            this.tabId = newId;
            window.sessionStorage.setItem(TAB_ID_KEY, newId);
        }
        this.tabIdVerified = true;
        return true;
    }

    _broadcast(message) {
        const payload = {
            ...message,
            tabId: this.tabId,
            ts: Date.now(),
            instanceId: this.instanceId,
        };
        if (this.channel) {
            this.channel.postMessage(payload);
        }
        this._setSafe(CHANNEL_STORAGE_KEY, payload);
    }

    _onChannelMessage(event) {
        this._handleMessage(event.data);
    }

    _onStorageMessage(event) {
        if (event.key !== CHANNEL_STORAGE_KEY || !event.newValue) {
            return;
        }
        try {
            const message = JSON.parse(event.newValue);
            this._handleMessage(message);
        } catch (error) {
            console.warn("VoIP One Tab: storage message parse failed", error);
        }
    }

    async _handleMessage(message) {
        if (!message || message.instanceId === this.instanceId) {
            return;
        }
        switch (message.type) {
            case "FORCE_LOGOUT":
                await this._handleForceLogout(message);
                break;
            case "ACK_LOGOUT":
                this._notifyAck(message);
                break;
            case "CHECK_TAB_ID":
                this._handleTabIdCheck(message);
                break;
            case "TAB_ID_IN_USE":
                this._notifyTabCheck(message);
                break;
        }
    }

    async _handleForceLogout(message) {
        if (message.targetTabId && message.targetTabId !== this.tabId) {
            return;
        }
        if (message.targetInstanceId && message.targetInstanceId !== this.instanceId) {
            return;
        }
        await this.userAgent._voipOneTabDisconnect("force_logout");
        this._broadcast({
            type: "ACK_LOGOUT",
            requestId: message.requestId,
        });
    }

    _handleTabIdCheck(message) {
        if (message.tabId !== this.tabId) {
            return;
        }
        this._broadcast({
            type: "TAB_ID_IN_USE",
            requestId: message.requestId,
            tabId: this.tabId,
        });
    }

    _notifyAck(message) {
        const handler = this.pendingAcks.get(message.requestId);
        if (!handler) {
            return;
        }
        this.pendingAcks.delete(message.requestId);
        handler(message);
    }

    _notifyTabCheck(message) {
        const handler = this.pendingTabChecks.get(message.requestId);
        if (!handler) {
            return;
        }
        this.pendingTabChecks.delete(message.requestId);
        handler(message);
    }

    _waitForAck(requestId) {
        return new Promise((resolve) => {
            const timeoutId = setTimeout(() => {
                this.pendingAcks.delete(requestId);
                resolve(null);
            }, ACK_TIMEOUT_MS);
            this.pendingAcks.set(requestId, (message) => {
                clearTimeout(timeoutId);
                resolve(message);
            });
        });
    }

    _waitForTabCheck(requestId) {
        return new Promise((resolve) => {
            const timeoutId = setTimeout(() => {
                this.pendingTabChecks.delete(requestId);
                resolve(null);
            }, TABID_CHECK_TIMEOUT_MS);
            this.pendingTabChecks.set(requestId, (message) => {
                clearTimeout(timeoutId);
                resolve(message);
            });
        });
    }

    _startHeartbeat() {
        if (this.heartbeatId) {
            return;
        }
        this._writeHeartbeat();
        this.heartbeatId = setInterval(() => this._writeHeartbeat(), HEARTBEAT_MS);
        this._startServerHeartbeat();
    }

    _stopHeartbeat() {
        if (this.heartbeatId) {
            clearInterval(this.heartbeatId);
            this.heartbeatId = null;
        }
    }

    async _claimOwnership() {
        const claimId = `${this.tabId}-${Date.now()}-${randomId()}`;
        this._setSafe(OWNER_KEY, {
            tabId: this.tabId,
            instanceId: this.instanceId,
            ts: Date.now(),
            claimId,
        });
        this._writeHeartbeat();
        await new Promise((resolve) => setTimeout(resolve, 50));
        const current = this._getOwner();
        if (
            current?.tabId !== this.tabId ||
            current?.instanceId !== this.instanceId ||
            current?.claimId !== claimId
        ) {
            debugLog("VoIP One Tab: ownership claim lost", {
                current,
                claimId,
            });
            return false;
        }
        this._startHeartbeat();
        let serverGranted = await this._serverAcquire();
        if (!serverGranted) {
            serverGranted = await this._attemptForceTakeover();
        }
        if (!serverGranted) {
            debugLog("VoIP One Tab: server lock denied");
            this.releaseOwnership();
            return false;
        }
        return true;
    }

    _writeHeartbeat() {
        this._setSafe(HEARTBEAT_KEY, {
            tabId: this.tabId,
            instanceId: this.instanceId,
            ts: Date.now(),
        });
    }

    _getOwner() {
        return this._getSafe(OWNER_KEY);
    }

    _getHeartbeat() {
        return this._getSafe(HEARTBEAT_KEY);
    }

    async _serverAcquire() {
        return this._serverAcquireWithOptions();
    }

    async _serverAcquireWithOptions({ force = false } = {}) {
        try {
            const result = await rpc("/voip/one_tab/acquire", {
                tab_id: this.serverId,
                force,
            });
            this.serverLockActive = result?.status === "granted" || result?.status === "forced";
            debugLog("VoIP One Tab: server acquire", {
                status: result?.status,
                serverId: this.serverId,
                force,
            });
            return this.serverLockActive;
        } catch (error) {
            console.warn("VoIP One Tab: server acquire failed", error);
            this.serverLockActive = false;
            return false;
        }
    }

    async _serverHeartbeat() {
        if (!this.serverLockActive) {
            return;
        }
        try {
            const result = await rpc("/voip/one_tab/heartbeat", {
                tab_id: this.serverId,
            });
            if (result?.status !== "ok") {
                this.serverLockActive = false;
                debugLog("VoIP One Tab: server heartbeat denied");
                await this.userAgent._voipOneTabDisconnect("server_lock_lost");
            }
        } catch (error) {
            console.warn("VoIP One Tab: server heartbeat failed", error);
        }
    }

    _startServerHeartbeat() {
        if (this.serverHeartbeatId) {
            return;
        }
        this._serverHeartbeat();
        this.serverHeartbeatId = setInterval(
            () => this._serverHeartbeat(),
            SERVER_HEARTBEAT_MS
        );
    }

    _stopServerHeartbeat() {
        if (this.serverHeartbeatId) {
            clearInterval(this.serverHeartbeatId);
            this.serverHeartbeatId = null;
        }
    }

    async _serverRelease({ keepalive = false } = {}) {
        if (!this.serverLockActive) {
            return;
        }
        this.serverLockActive = false;
        try {
            if (keepalive && navigator.sendBeacon) {
                const payload = JSON.stringify({
                    jsonrpc: "2.0",
                    method: "call",
                    params: { tab_id: this.serverId },
                    id: Date.now(),
                });
                navigator.sendBeacon(
                    "/voip/one_tab/release",
                    new Blob([payload], { type: "application/json" })
                );
                debugLog("VoIP One Tab: server release beacon sent");
                return;
            }
            await rpc("/voip/one_tab/release", {
                tab_id: this.serverId,
            });
            debugLog("VoIP One Tab: server release ok");
        } catch (error) {
            console.warn("VoIP One Tab: server release failed", error);
        }
    }

    _setSafe(key, value) {
        try {
            window.localStorage.setItem(key, JSON.stringify(value));
        } catch (error) {
            console.warn("VoIP One Tab: localStorage set failed", error);
        }
    }

    _getSafe(key) {
        try {
            const value = window.localStorage.getItem(key);
            return value ? JSON.parse(value) : null;
        } catch (error) {
            console.warn("VoIP One Tab: localStorage get failed", error);
            return null;
        }
    }

    _removeSafe(key) {
        try {
            window.localStorage.removeItem(key);
        } catch (error) {
            console.warn("VoIP One Tab: localStorage remove failed", error);
        }
    }

    async _attemptForceTakeover() {
        const requestId = `${this.tabId}-force-${Date.now()}`;
        const ackPromise = this._waitForAck(requestId);
        this._broadcast({ type: "FORCE_LOGOUT", requestId });
        const ack = await ackPromise;
        if (!ack) {
            debugLog("VoIP One Tab: force takeover ack timeout", { requestId });
        }
        await new Promise((resolve) => setTimeout(resolve, 200));
        return this._serverAcquireWithOptions({ force: true });
    }
}

patch(UserAgent.prototype, {
    async init() {
        if (this.__voipOneTabInitDone) {
            return;
        }
        this.__voipOneTabEnabled = this.voip?.voip_one_tab_enabled ?? true;
        debugLog("VoIP One Tab: init called", {
            enabled: this.__voipOneTabEnabled,
        });
        if (!this.__voipOneTabEnabled) {
            this.__voipOneTabInitDone = true;
            return originalInit.apply(this, arguments);
        }

        this.__voipOneTabInitDone = true;
        this.__voipOneTabStarted = false;
        this.__voipOneTabConnectPromise = null;
        this.__voipOneTabDisconnecting = false;
        this.__voipOneTabCoordinator = new VoipOneTabCoordinator(this);
        this.voip.__voipOneTabEnabled = this.__voipOneTabEnabled;
        this.voip.__voipOneTabAllowRegister = false;
        guardState.enabled = this.__voipOneTabEnabled;
        guardState.allow = false;
        patchSipUserAgent();
        this._voipOneTabStartCleanup();

        if (IS_DEBUG) {
            debugLog("VoIP One Tab: ids", {
                tabId: this.__voipOneTabCoordinator.tabId,
                instanceId: this.__voipOneTabCoordinator.instanceId,
                serverId: this.__voipOneTabCoordinator.serverId,
            });
            this.voip.triggerError(
                _t("VoIP One Tab patch active. tabId=%s", this.__voipOneTabCoordinator.tabId),
                { isNonBlocking: true }
            );
        }

        window.addEventListener("beforeunload", () => this._voipOneTabHandleUnload());
        window.addEventListener("pagehide", () => this._voipOneTabHandleUnload());
    },

    async makeCall(data, { type = "default" } = {}) {
        if (!this.__voipOneTabEnabled || this.voip.mode !== "prod") {
            return originalMakeCall.apply(this, arguments);
        }

        if (!(await this.voip.willCallUsingVoip())) {
            window.location.assign(`tel:${data.phone_number}`);
            return;
        }

        try {
            await this._voipOneTabEnsureConnected();
        } catch (error) {
            console.warn("VoIP One Tab: connect-on-call failed", error);
            this.voip.triggerError(_t("Unable to start the SIP connection. Please try again."));
            return;
        }

        return this._voipOneTabPlaceCall(data, { type });
    },

    async attemptReconnection() {
        if (!this.__voipOneTabEnabled) {
            return originalAttemptReconnection.apply(this, arguments);
        }
        // Auto-reconnect intentionally disabled.
    },

    async _onTransportDisconnected(error) {
        if (!this.__voipOneTabEnabled) {
            return originalOnTransportDisconnected.call(this, error);
        }
        if (error) {
            console.error(error);
        }
        this.voip.triggerError(
            _t("SIP connection closed. Start a new call to reconnect."),
            { isNonBlocking: true }
        );
        await this._voipOneTabDisconnect("transport_disconnected");
    },

    async _voipOneTabEnsureConnected() {
        if (this.__voipOneTabStarted && this.registerer) {
            return true;
        }
        if (this.__voipOneTabConnectPromise) {
            return this.__voipOneTabConnectPromise;
        }

        this.__voipOneTabConnectPromise = (async () => {
            const acquired = await this.__voipOneTabCoordinator.acquireOwnership();
            if (!acquired) {
                this.voip.triggerError(
                    _t("Another tab is using VoIP. Please retry in a moment."),
                    { isNonBlocking: true }
                );
                throw new Error("voip ownership not acquired");
            }
            try {
                guardState.allow = true;
                this.voip.__voipOneTabAllowRegister = true;
                patchSipUserAgent();
                await originalInit.apply(this, []);
            } catch (error) {
                guardState.allow = false;
                this.voip.__voipOneTabAllowRegister = false;
                this.__voipOneTabCoordinator.releaseOwnership();
                throw error;
            }
            if (!this.registerer) {
                guardState.allow = false;
                this.voip.__voipOneTabAllowRegister = false;
                this.__voipOneTabCoordinator.releaseOwnership();
                throw new Error("voip init failed");
            }
            this.__voipOneTabStarted = true;
            this.__voipOneTabCoordinator._startHeartbeat();
            return true;
        })().finally(() => {
            this.__voipOneTabConnectPromise = null;
        });

        return this.__voipOneTabConnectPromise;
    },

    _voipOneTabStartCleanup() {
        if (this.__voipOneTabCleanupId) {
            return;
        }
        this.__voipOneTabCleanupId = setInterval(() => {
            if (!this.__voipOneTabEnabled) {
                return;
            }
            const allow = this.voip?.__voipOneTabAllowRegister ?? guardState.allow;
            if (!allow && (this.__sipJsUserAgent || this.registerer)) {
                debugLog("VoIP One Tab: cleaning up auto connection");
                this._voipOneTabDisconnect("guard_cleanup");
            }
        }, 2000);
    },

    async _voipOneTabDisconnect(reason, { keepalive = false } = {}) {
        if (!this.__voipOneTabEnabled) {
            return;
        }
        if (this.__voipOneTabDisconnecting) {
            return;
        }
        this.__voipOneTabDisconnecting = true;
        try {
            if (this.activeSession?.call?.isInProgress) {
                await this.hangup();
            }
            try {
                this.registerer?.__sipJsRegisterer?.unregister();
            } catch (error) {
                console.warn("VoIP One Tab: unregister failed", error);
            }
            try {
                await this.__sipJsUserAgent?.stop?.();
            } catch (error) {
                console.warn("VoIP One Tab: stop failed", error);
            }
        } finally {
            this.__voipOneTabStarted = false;
            this.registerer = null;
            this.__sipJsUserAgent = null;
            this.__voipOneTabCoordinator?.releaseOwnership({ keepalive });
            if (this.voip?.__voipOneTabAllowRegister !== undefined) {
                this.voip.__voipOneTabAllowRegister = false;
            }
            guardState.allow = false;
            this.__voipOneTabDisconnecting = false;
        }
    },

    _voipOneTabHandleUnload() {
        if (!this.__voipOneTabEnabled) {
            return;
        }
        this._voipOneTabDisconnect("unload", { keepalive: true });
    },

    async _voipOneTabPlaceCall(data, { type }) {
        // Re-implement original makeCall to avoid double willCallUsingVoip checks.
        const call = await this.callService.create(data);
        try {
            var session = this.invite(call);
        } catch {
            this.callService.abort(call);
            return;
        }
        if (type === "transfer") {
            this.activeSession = this.transferSession = session;
        } else {
            this.activeSession = this.mainSession = session;
        }
        this.softphone.show();
        this.ringtoneService.ringback.play();
    },
});

patch(Registerer.prototype, {
    register() {
        const voip = this.voip;
        const enabled = voip?.__voipOneTabEnabled ?? guardState.enabled;
        const allow = voip?.__voipOneTabAllowRegister ?? guardState.allow;
        if (enabled && !allow) {
            debugLog("VoIP One Tab: blocked Registerer.register()", {
                enabled,
                allow,
            });
            return;
        }
        debugLog("VoIP One Tab: allowed Registerer.register()", { enabled, allow });
        return originalRegister.apply(this, arguments);
    },
});
