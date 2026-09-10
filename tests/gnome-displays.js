import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

function assert(value, message) { if (!value) throw new Error(message); }

// Exercise the actual controller against widget doubles. The real GNOME 50
// widget constructors/signals are checked separately in attended shell testing.
class Widget {
    constructor(text = '') { this.text = text; this.items = []; this.signals = new Map(); this.quickSettingsItems = []; }
    connect(name, handler) { this.signals.set(name, handler); return name; }
    disconnect(name) { this.signals.delete(name); }
    addMenuItem(item) { this.items.push(item); }
    removeAll() { this.items = []; }
    addAction(text, callback) { const item = new Widget(text); item.activate = callback; this.items.push(item); return item; }
    addSettingsAction(text) { return this.addAction(text, () => {}); }
    setHeader() {}
    setSensitive(value) { this.sensitive = value; }
    open() { this.isOpen = true; }
    destroy() { this.destroyed = true; }
}
class Toggle extends Widget { constructor() { super(); this.menu = new Widget(); } }
const notices = [];
const main = {layoutManager: new Widget(), notify: (...args) => notices.push(args),
    panel: {statusArea: {quickSettings: {addExternalIndicator() {}}}}};
const source = Gio.File.new_for_uri(import.meta.url).get_parent().get_parent()
    .get_child('guest/gnome/linuxhost@local/displayMenu.js').load_contents(null)[1];
const body = new TextDecoder().decode(source).replace(/^import .*;\n/gm, '').replace('export class DisplayMenu', 'class DisplayMenu');
const DisplayMenu = new Function('Gio', 'Main', 'PopupMenu', 'QuickMenuToggle', 'SystemIndicator', `${body}\nreturn DisplayMenu;`)
    ({}, main, {PopupMenuSection: Widget, PopupMenuItem: Widget, PopupSeparatorMenuItem: Widget}, Toggle, Widget);

async function checks() {
    const ui = new DisplayMenu(), pending = [];
    ui.request = (...args) => new Promise(resolve => pending.push({args, resolve}));
    assert(pending.length === 0, 'Construction must not start discovery or a polling loop');
    ui.item.menu.isOpen = true;
    const first = ui.refresh();
    assert(pending.length === 1 && pending[0].args[0] === 'display-list', 'Explicit refresh must list destinations');
    const duplicate = ui.refresh(); await duplicate;
    assert(pending.length === 1, 'Concurrent refresh must be coalesced');
    pending.shift().resolve({ok: true, devices: []}); await first;
    assert(pending.length === 1, 'One deferred refresh must follow an invalidation');
    const device = {id: 'sidecar:' + 'a'.repeat(64), name: 'Test iPad', connected: true};
    pending.shift().resolve({ok: true, devices: [device]}); await Promise.resolve();
    assert(ui.section.items[0].text.includes('Disconnect'), 'Connected device must offer disconnect');
    const operation = ui.select(device);
    assert(pending[0].args[0] === 'display-disconnect' && pending[0].args[1] === device.id, 'Use the opaque target as one argument');
    await ui.select(device);
    assert(pending.length === 1, 'Duplicate clicks must not repeat the native action');
    pending.shift().resolve({ok: false, error: 'fixture rejection'}); await operation;
    assert(notices.length === 1 && pending[0].args[0] === 'display-list', 'Failure must notify and reconcile');
    ui.destroy();
    pending.shift().resolve({ok: true, devices: []}); await Promise.resolve();
    assert(ui.item.destroyed && ui.indicator.destroyed, 'Disable must remove both widgets');
    assert(pending.length === 0 && notices.length === 1, 'Late replies after disable must not recreate UI');
    print('GNOME display on-demand refresh, coalescing, connect arguments, duplicate suppression, failure and disable checks passed');
}

const loop = GLib.MainLoop.new(null, false);
let error;
checks().catch(e => { error = e; }).finally(() => loop.quit());
loop.run();
if (error) throw error;
