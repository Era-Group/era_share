import { expect, test } from "@odoo/hoot";

import { CallService } from "@voip/core/call_service";
import { Session } from "@voip/core/session";

import "@era_voip_ext/js/call_termination";

test("duplicate call endings share one request", async () => {
    let requestCount = 0;
    const service = Object.assign(Object.create(CallService.prototype), {
        orm: {
            async call() {
                requestCount++;
                return {};
            },
        },
        store: {
            insert() {},
        },
    });
    const call = { id: 1, timer: null };

    await Promise.all([service.end(call), service.end(call)]);

    expect(requestCount).toBe(1);
});

test("SIP termination ends an ongoing call", () => {
    let endedCall;
    const call = { state: "ongoing" };
    const session = Object.assign(Object.create(Session.prototype), {
        _call: call,
        callService: {
            end(value) {
                endedCall = value;
                return Promise.resolve();
            },
        },
        remoteAudio: null,
    });

    session._onSessionTerminated();

    expect(endedCall).toBe(call);
});
