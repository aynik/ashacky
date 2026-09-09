"""Output master gain proxy; input gain and per-application volumes stay local."""
import math
import subprocess
import time


def properties(node):
    return next(iter(node.get('info', {}).get('params', {}).get('Props', [])), {})


def level_state(props):
    levels = props.get('channelVolumes', [])
    if not 1 <= len(levels) <= 64 or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in levels):
        raise ValueError('Invalid PipeWire channel volume')
    return tuple(levels), bool(props.get('mute', False))


def equal(a, b):
    return (a is not None and b is not None and a[1] == b[1] and len(a[0]) == len(b[0])
            and all(abs(x - y) < 0.00001 for x, y in zip(a[0], b[0])))


def set_props(ident, levels, muted, software):
    # PipeWire distinguishes the displayed channel volume from actual software
    # gain. Both must be present in the same Props write; otherwise channel
    # volume becomes the software gain again. pwctl/wpctl alone cannot do this.
    array = lambda values: '[ ' + ' '.join(format(v, '.9g') for v in values) + ' ]'
    flag = 'true' if muted else 'false'
    value = ('{ channelVolumes: ' + array(levels) + ' mute: ' + flag
             + ' softVolumes: ' + array(software) + ' softMute: ' + flag + ' }')
    subprocess.run(['pw-cli', 'set-param', str(ident), 'Props', value],
                   check=True, stdout=subprocess.DEVNULL, timeout=5)


class OutputVolume:
    def __init__(self, write=set_props, clock=time.monotonic):
        self.write, self.clock = write, clock
        self.key = None
        self.host = None
        self.observed = None
        self.pending = None
        self.balance = None

    def reset(self):
        self.key = self.host = self.observed = self.pending = self.balance = None

    def fallback(self, node):
        """Keep the public endpoint and honor its mute if host control fails."""
        if self.key is not None and node is not None:
            levels, muted = level_state(properties(node))
            self.write(node['id'], levels, muted, levels)
        self.reset()

    def reconcile(self, device, node, change, later):
        profile = device.get('outputVolume')
        if profile is None:
            self.fallback(node)
            return None
        host = (float(profile['level']), bool(profile['muted']))
        if not math.isfinite(host[0]) or not 0 <= host[0] <= 1:
            raise ValueError('Invalid host output volume')
        props = properties(node)
        actual = level_state(props)
        key = (device['uid'], node['id'])
        fresh = key != self.key
        if fresh:
            self.reset()
            self.key = key
            maximum = max(actual[0])
            self.balance = tuple(v / maximum if maximum else 1.0 for v in actual[0])
        stale_echo = False
        pending = self.pending
        if pending:
            before, wanted, deadline = pending
            if equal(actual, wanted):
                self.pending = None
            elif equal(actual, before):
                if host == self.host and self.clock() < deadline:
                    return None  # Our own Props write, not user input.
                # A missing echo is not evidence of a new user choice. Retry
                # the display write; never push the old level back to the host.
                stale_echo = True
                self.pending = None
            else:
                self.pending = None
        result = None
        if not fresh and not stale_echo and host == self.host and not equal(actual, self.observed):
            maximum = max(actual[0])
            self.balance = tuple(v / maximum if maximum else b for v, b in zip(actual[0], self.balance))
            # WirePlumber/Pulse present a cubic volume scale. Keep macOS's
            # scalar and the desktop slider on the same 0..100% scale.
            desired = {'level': min(1.0, maximum ** (1 / 3)), 'muted': actual[1]}
            result = change({'action': 'audio-volume', 'uid': device['uid'],
                             'desired': desired,
                             'expected': {'level': host[0], 'muted': host[1]}})
            active = next((d for d in result['devices'] if d['defaultOutput']), None)
            if active is None or active['uid'] != device['uid']:
                self.fallback(node)
                return result
            profile = active.get('outputVolume')
            if profile is None:
                self.fallback(node)
                return result
            host = (float(profile['level']), bool(profile['muted']))
        wanted = (tuple(host[0] ** 3 * b for b in self.balance), host[1])
        software = tuple(self.balance)
        soft = (tuple(props.get('softVolumes', [])), bool(props.get('softMute', False)))
        if not equal(actual, wanted) or not equal(soft, (software, wanted[1])):
            self.write(node['id'], wanted[0], wanted[1], software)
            self.pending = (actual, wanted, self.clock() + 2)
            later('volume-deadline', 2100)
        self.host, self.observed = host, wanted
        return result
