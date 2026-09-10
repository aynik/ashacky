import Gio from 'gi://Gio';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';

// Discovery happens on open, explicit refresh and monitor changes while open.
// No idle timer, host token, direct privileged socket or runtime SSH is needed.
export class DisplayMenu {
    constructor() {
        this._alive = true;
        this._busy = false;
        this._refreshAgain = false;
        this._children = new Set();
        this.indicator = new SystemIndicator();
        this.item = new QuickMenuToggle({title: 'Displays', iconName: 'video-display-symbolic', toggleMode: false});
        this.item.menu.setHeader('video-display-symbolic', 'Wireless Displays');
        this.section = new PopupMenu.PopupMenuSection();
        this.item.menu.addMenuItem(this.section);
        this.item.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.refreshItem = this.item.menu.addAction('Refresh', () => this.refresh());
        this.item.menu.addSettingsAction('Display Settings', 'gnome-display-panel.desktop');
        this.item.connect('clicked', () => this.item.menu.open());
        this.item.menu.connect('open-state-changed', (_menu, open) => { if (open) this.refresh(); });
        this.indicator.quickSettingsItems.push(this.item);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this.indicator);
        this._monitorSignal = Main.layoutManager.connect('monitors-changed', () => {
            if (this.item.menu.isOpen) this.refresh();
        });
        this.message('Open to discover displays');
    }

    message(text) {
        this.section.removeAll();
        this.section.addMenuItem(new PopupMenu.PopupMenuItem(text, {reactive: false}));
    }

    request(action, id = null) {
        return new Promise(resolve => {
            let child;
            try {
                const argv = ['/usr/local/bin/hostctl', action];
                if (id !== null) argv.push(id);
                child = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
                this._children.add(child);
                child.communicate_utf8_async(null, null, (process, result) => {
                    this._children.delete(child);
                    try {
                        const [, stdout] = process.communicate_utf8_finish(result);
                        resolve(JSON.parse(stdout));
                    } catch (_error) { resolve({ok: false, error: 'Display controls are unavailable'}); }
                });
            } catch (_error) {
                if (child) this._children.delete(child);
                resolve({ok: false, error: 'Display controls are unavailable'});
            }
        });
    }

    async refresh() {
        if (!this._alive) return;
        if (this._busy) { this._refreshAgain = true; return; }
        this._busy = true;
        this.refreshItem.setSensitive(false);
        this.message('Looking for displays…');
        const response = await this.request('display-list');
        if (!this._alive) return;
        this._busy = false;
        this.refreshItem.setSensitive(true);
        if (!response.ok || !Array.isArray(response.devices)) {
            this.message(response.error || 'Display controls are unavailable');
        } else {
            this.section.removeAll();
            if (response.devices.length === 0) this.message('No iPad found. Wake it, then refresh.');
            for (const device of response.devices.slice(0, 16)) {
                if (!/^sidecar:[0-9a-f]{64}$/.test(device.id) || typeof device.name !== 'string') continue;
                const row = new PopupMenu.PopupMenuItem(`${device.name} — ${device.connected ? 'Disconnect' : 'Connect'}`);
                row.connect('activate', () => this.select(device));
                this.section.addMenuItem(row);
            }
        }
        if (this._refreshAgain && this.item.menu.isOpen) {
            this._refreshAgain = false;
            this.refresh();
        } else { this._refreshAgain = false; }
    }

    async select(device) {
        if (!this._alive || this._busy) return;
        this._busy = true;
        this.refreshItem.setSensitive(false);
        this.message(device.connected ? 'Disconnecting…' : 'Connecting…');
        const response = await this.request(device.connected ? 'display-disconnect' : 'display-connect', device.id);
        if (!this._alive) return;
        this._busy = false;
        this._refreshAgain = false;
        if (!response.ok) Main.notify('Displays', response.error || 'Connection failed');
        this.refresh();
    }

    destroy() {
        this._alive = false;
        Main.layoutManager.disconnect(this._monitorSignal);
        for (const child of this._children) child.force_exit();
        this._children.clear();
        this.item.destroy();
        this.indicator.destroy();
    }
}
