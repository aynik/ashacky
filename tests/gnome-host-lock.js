import GLib from 'gi://GLib';
import {redirectHostLock} from '../guest/gnome/linuxhost@local/hostLock.js';

function assert(value, message) {
    if (!value) throw new Error(message);
}

async function checks() {
    let nativeCalls = 0;
    let hostCalls = 0;
    let complete;
    const original = () => { nativeCalls++; };
    const shield = {lock: original};
    const restore = redirectHostLock(shield, () => {
        hostCalls++;
        return new Promise(resolve => { complete = resolve; });
    });
    const first = shield.lock(true);
    const repeated = shield.lock(false);
    await Promise.resolve();
    assert(first === repeated && hostCalls === 1, 'Repeated locks must share one outstanding request');
    assert(nativeCalls === 0, 'Guest lock screen must not open');
    complete(false); await first;
    const retry = shield.lock(true); await Promise.resolve();
    assert(hostCalls === 2, 'A failed host request must allow a later explicit retry');
    complete(true); await retry;
    restore(); shield.lock(true);
    assert(nativeCalls === 1, 'Disabling the extension must restore the original method');

    const cancel = redirectHostLock(shield, () => { hostCalls++; });
    const queued = shield.lock(true); cancel(); await queued;
    assert(hostCalls === 2, 'Disable must cancel an undispatched host lock');
    const preserve = redirectHostLock(shield, () => {});
    const other = () => {};
    shield.lock = other; preserve();
    assert(shield.lock === other, 'Cleanup must preserve a later extension override');
    print('GNOME host-lock routing, duplicate suppression, failure retry and cleanup passed');
}

const loop = GLib.MainLoop.new(null, false);
let error;
checks().catch(e => { error = e; }).finally(() => loop.quit());
loop.run();
if (error) throw error;
