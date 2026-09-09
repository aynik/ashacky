// One interception point covers GNOME's menu, shortcut and ScreenSaver API.
export function redirectHostLock(screenShield, request) {
    const original = screenShield.lock;
    let active = true;
    let pending = null;
    const redirected = () => {
        if (!pending) {
            pending = Promise.resolve()
                .then(() => active ? request() : false)
                .catch(() => false) // The request adapter reports its own error.
                .finally(() => { pending = null; });
        }
        return pending;
    };
    screenShield.lock = redirected;
    return () => {
        active = false;
        if (screenShield.lock === redirected)
            screenShield.lock = original;
    };
}
