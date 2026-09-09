import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';
import * as SystemActions from 'resource:///org/gnome/shell/misc/systemActions.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {redirectHostLock} from './hostLock.js';

export default class MacIntegration extends Extension {
    enable() {
        // GNOME menu, shortcut and ScreenSaver D-Bus all reach this method.
        this.restoreLock = redirectHostLock(Main.screenShield, () => this.run('lock'));
        // Route GNOME's normal power menu through the same confirmed host actions.
        this.systemActions = SystemActions.getDefault();
        this.originalActions = {};
        for (const [method, action, title] of [
            ['activatePowerOff', 'poweroff', 'Shut Down Mac'],
            ['activateRestart', 'restart', 'Restart Mac'],
            ['activateLogout', 'logout', 'Log Out'],
            ['activateSuspend', 'sleep', null],
        ]) {
            this.originalActions[method] = this.systemActions[method];
            this.systemActions[method] = () => title ? this.confirm(title, action) : this.run(action);
        }
    }
    confirm(title, action) {
        if (this.dialog) return;
        this.dialog = new ModalDialog.ModalDialog();
        this.dialog.contentLayout.add_child(new St.Label({text: action === 'logout' ? 'Log out?\nLinux will shut down and the macOS session will end.' : `${title}?\nLinux will shut down first. This affects the whole Mac.`}));
        this.dialog.setButtons([
            {label: 'Cancel', action: () => {this.dialog.close(); this.dialog = null;}, key: 0xff1b},
            {label: title, action: () => {this.dialog.close(); this.dialog = null; this.run(action);}},
        ]);
        this.dialog.open();
    }
    run(action) {
        return new Promise(resolve => {
            try {
                const child = Gio.Subprocess.new(['/usr/local/bin/hostctl', action], Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
                child.communicate_utf8_async(null, null, (process, result) => {
                    try {
                        const [, stdout] = process.communicate_utf8_finish(result);
                        const response = JSON.parse(stdout);
                        if (!response.ok) Main.notify('Mac integration', response.error || 'Request failed');
                        else if (action === 'authenticate') Main.notify('Mac integration', 'Touch ID authentication succeeded');
                        resolve(response.ok === true);
                    } catch (error) { Main.notify('Mac integration', String(error)); resolve(false); }
                });
            } catch (error) { Main.notify('Mac integration', String(error)); resolve(false); }
        });
    }
    disable() {
        this.restoreLock?.(); this.restoreLock = null;
        if (this.systemActions && this.originalActions) {
            for (const [method, original] of Object.entries(this.originalActions)) this.systemActions[method] = original;
        }
        this.systemActions = null; this.originalActions = null;
        if (this.timer) GLib.source_remove(this.timer);
        this.timer = null;
        this.dialog?.close(); this.dialog = null;
        this.button?.destroy(); this.button = null;
    }
}
