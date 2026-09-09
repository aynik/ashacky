#!/usr/bin/env python3
"""Wi-Fi radio synchronization, driven by rfkill and host events."""
from concurrent.futures import ThreadPoolExecutor
import glob
import os
from pathlib import Path
import signal
import struct
from bluetooth_management import request
from wifi_events import WiFiEvents

SOCKET = '/run/ashacky-control/wifi.sock'
RFKILL = struct.Struct('<IBBBB')


def switch():
    entries = glob.glob('/sys/class/net/lhwifi0/phy80211/rfkill*')
    if len(entries) != 1:
        raise RuntimeError('Wi-Fi radio interface unavailable')
    path = Path(entries[0])
    return int(path.name[6:]), int((path / 'soft').read_text())


class RadioIntent:
    """Separate guest choices from initial state and our own rfkill echoes."""
    def __init__(self, index, soft):
        self.index, self.soft = index, soft
        self.initialized = False
        self.expected = None
        self.serial = 0
        self.pending = None

    def observe(self, soft):
        previous, self.soft = self.soft, soft
        expected, self.expected = self.expected, None
        if soft == expected or soft == previous or not self.initialized:
            return False
        self.serial += 1
        self.pending = (self.serial, not bool(soft))
        return True

    def attempted(self, attempt):
        # A newer user choice survives completion of an older operation. An
        # uncertain operation is never automatically submitted a second time.
        if attempt is not None and self.pending == attempt:
            self.pending = None

    def apply(self, powered, write):
        self.initialized = True
        if self.pending:
            if self.pending[1] != powered:
                return
            self.pending = None  # The requested state is already satisfied.
        desired = int(not powered)
        if desired != self.soft:
            write(self.index, desired)
            self.expected, self.soft = desired, desired

    def inactive(self):
        self.initialized = False
        self.pending = None


class WiFiPower:
    def __init__(self, path=SOCKET):
        from gi.repository import GLib
        self.GLib = GLib
        try:
            from gi.repository import GLibUnix
            self.add_signal = GLibUnix.signal_add
        except ImportError:
            self.add_signal = GLib.unix_signal_add
        self.path = path
        self.loop = GLib.MainLoop()
        self.events = WiFiEvents(self.host_changed)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='wifi-host')
        self.radio = None
        self.rfkill = None
        self.sources = []
        self.closed = False
        self.busy = False
        self.dirty = False
        self.failed = False
        self.error = None

    def notify(self):
        if not self.closed and self.events.active:
            self.dirty = True
            self.events.later('refresh', 40, self.refresh)

    def host_changed(self):
        if not self.events.active:
            self.radio.inactive()
        self.notify()

    def read_radio(self):
        while True:
            try:
                data = os.read(self.rfkill, RFKILL.size)
            except BlockingIOError:
                return
            if len(data) != RFKILL.size:
                raise RuntimeError('Incomplete rfkill event')
            index, kind, operation, soft, hard = RFKILL.unpack(data)
            if kind != 1 or index != self.radio.index:
                continue
            if operation == 1:
                raise RuntimeError('Wi-Fi radio removed')
            if operation not in (0, 2) or soft not in (0, 1) or hard not in (0, 1):
                raise ValueError('Invalid Wi-Fi rfkill event')
            if self.radio.observe(soft):
                self.notify()

    def radio_ready(self, fd, condition):
        try:
            if condition & (self.GLib.IO_ERR | self.GLib.IO_HUP):
                raise RuntimeError('Wi-Fi rfkill channel closed')
            self.read_radio()
        except (OSError, ValueError, RuntimeError) as error:
            self.error = error; self.loop.quit()
        return True

    def write_radio(self, index, soft):
        if os.write(self.rfkill, RFKILL.pack(index, 1, 2, soft, 0)) != RFKILL.size:
            raise RuntimeError('Incomplete radio state write')

    def fetch(self, attempt):
        problem = None
        if attempt is not None:
            try:
                if self.closed:
                    return None, 'Stopped'
                request({'action': 'wifi-power', 'enabled': attempt[1]}, self.path)
            except (OSError, ValueError, RuntimeError) as error:
                problem = type(error).__name__
        try:
            state = request({'action': 'wifi-status'}, self.path)
            if type(state.get('powered')) is not bool:
                raise ValueError('Missing host radio state')
            return state['powered'], problem
        except (OSError, ValueError, RuntimeError) as error:
            return None, type(error).__name__

    def refresh(self):
        if self.closed or self.busy or not self.dirty or not self.events.active:
            return
        try:
            # Drain choices queued before taking the work snapshot.
            self.read_radio()
        except Exception as error:
            self.error = error; self.loop.quit(); return
        self.dirty = False; self.busy = True
        key = self.events.key
        attempt = self.radio.pending if self.radio.initialized else None
        future = self.executor.submit(self.fetch, attempt)
        def completed(future):
            if not self.closed:
                self.GLib.idle_add(self.finish, key, attempt, future)
        future.add_done_callback(completed)

    def finish(self, key, attempt, future):
        if self.closed:
            return False
        self.busy = False
        try:
            # Do not overwrite a newer, not-yet-dispatched guest toggle.
            self.read_radio()
            self.radio.attempted(attempt)
            powered, problem = future.result()
            if key != self.events.key or not self.events.active:
                self.notify()
                return False
            if problem and not self.failed:
                print('Wi-Fi radio synchronization unavailable:', problem, flush=True)
            if not problem and self.failed:
                print('Wi-Fi radio synchronization recovered', flush=True)
            self.failed = bool(problem)
            if powered is None:
                self.radio.initialized = False
                self.events.later('retry', 1000, self.notify)
            else:
                self.radio.apply(powered, self.write_radio)
                if self.radio.pending:
                    self.dirty = True
            if self.dirty:
                self.notify()
        except Exception as error:
            self.error = error; self.loop.quit()
        return False

    def quit(self, *_):
        self.loop.quit()
        return True  # Keep the signal source registered until close() removes it.

    def close(self):
        self.closed = True; self.events.close()
        for source in self.sources:
            self.GLib.source_remove(source)
        self.sources.clear()
        if self.rfkill is not None:
            os.close(self.rfkill); self.rfkill = None
        self.executor.shutdown(wait=True, cancel_futures=True)

    def run(self):
        try:
            self.rfkill = os.open('/dev/rfkill', os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
            self.radio = RadioIntent(*switch())
            self.read_radio()
            self.sources.append(self.GLib.io_add_watch(self.rfkill, self.GLib.PRIORITY_DEFAULT,
                self.GLib.IO_IN | self.GLib.IO_ERR | self.GLib.IO_HUP, self.radio_ready))
            for sig in (signal.SIGTERM, signal.SIGINT):
                self.sources.append(self.add_signal(self.GLib.PRIORITY_DEFAULT, sig, self.quit))
            self.events.watch()
            self.loop.run()
            if self.error:
                raise RuntimeError('Wi-Fi event service failed') from self.error
        finally:
            self.close()


if __name__ == '__main__':
    WiFiPower().run()
