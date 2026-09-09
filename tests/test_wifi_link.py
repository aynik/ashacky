import base64
from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import select
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'guest/devices'))
import wifi_bridge as bridge
import wifi_link as link
from wifi_scan import BSS, parse_bss

SSID = b'Example'
FIRST, SECOND = bytes.fromhex('020000000001'), bytes.fromhex('020000000002')
RSN = b'\x01\x00' + link.CCMP + b'\x01\x00' + link.CCMP + b'\x01\x00' + link.PSK + b'\x00\x00'
IES = bytes([0, len(SSID)]) + SSID + bytes([48, len(RSN)]) + RSN
A = BSS('00000000-0000-4000-8000-000000000001', SSID, FIRST, 2412, -6000, IES, False)
B = replace(A, network_id='00000000-0000-4000-8000-000000000002', bssid=SECOND, signal_mbm=-4000)


def status(record=A):
    return {'ok': True, 'locationAuthorized': True, 'networkIdentityAvailable': True,
        'ssidBase64': base64.b64encode(record.ssid).decode(), 'bssid': record.bssid.hex(':')}


class WiFiLinkTests(unittest.TestCase):
    def test_security_contract_rejects_downgrades_and_malformed_records(self):
        self.assertTrue(link.compatible(A, True))
        self.assertFalse(link.compatible(A, False))
        open_ap = replace(A, open=True, information_elements=bytes([0, len(SSID)]) + SSID)
        self.assertTrue(link.compatible(open_ap, False))
        self.assertFalse(link.compatible(open_ap, True))
        for ies in [IES[:-1], IES + bytes([48, len(RSN)]) + RSN,
                    IES.replace(link.PSK, b'\x00\x0f\xac\x01'),  # Enterprise only.
                    IES[:-2] + b'\x40\x00',  # Required PMF.
                    IES[:11] + b'\xff\xff' + IES[13:],
                    IES.replace(link.CCMP, b'\x00\x0f\xac\x02')]:
            self.assertFalse(link.compatible(replace(A, information_elements=ies), True))
        self.assertFalse(link.compatible(replace(open_ap, information_elements=open_ap.information_elements + b'\xdd\x04\x00\x50\xf2\x01'), False))
        entry = {'networkID': A.network_id, 'ssidBase64': base64.b64encode(SSID).decode(),
            'bssid': FIRST.hex(':'), 'signalDBm': -60, 'channelBand': 1, 'channel': 1,
            'informationElementsBase64': base64.b64encode(IES).decode()}
        with self.assertRaises(ValueError): parse_bss(entry)
        self.assertEqual(parse_bss(dict(entry, open=False)), A)

    def test_automatic_selection_reports_actual_ap_but_explicit_pin_is_enforced(self):
        calls, published = [], []
        def rpc(request):
            calls.append(dict(request))
            return status() if request['action'] == 'wifi-status' else {'ok': True}
        request = bridge.CONNECTION.pack(12, 1, len(SSID), 32, 0, bytes(6), SSID, bytes(32))
        with patch.object(bridge, 'scan', return_value=[A, B]):
            actual = bridge.handle_connection(request, rpc, lambda *args: published.append(args))
        self.assertEqual(actual, FIRST)
        self.assertEqual(calls[0]['networkID'], B.network_id)  # Host may prefer another AP.
        self.assertEqual(published, [(12, A)])
        pinned = bridge.CONNECTION.pack(13, 1, len(SSID), 32, 0, SECOND, SSID, bytes(32))
        with patch.object(bridge, 'scan', return_value=[A, B]):
            with self.assertRaisesRegex(RuntimeError, 'different requested BSSID'):
                bridge.handle_connection(pinned, rpc)
        # A host choosing an open AP with the same SSID is not a successful WPA2 connection.
        with patch.object(bridge, 'scan', return_value=[replace(A, open=True), B]):
            with self.assertRaisesRegex(RuntimeError, 'security'):
                bridge.handle_connection(request, rpc)

    def test_roam_report_requires_current_identity_security_and_connection_generation(self):
        follower = link.LinkFollower(123)
        snapshot = (8, link.CONNECTED | link.PRIVATE, FIRST, SSID)
        writes = []
        with patch.object(link, 'scan', return_value=[A, B]), \
                patch.object(link.fcntl, 'ioctl', lambda fd, op, data: writes.append((fd, op, data))):
            follower.update(snapshot, status(B), lambda _: status(B))
            self.assertEqual(len(writes), 1)
            header = link.REPORT.unpack(writes[0][2][:link.REPORT.size])
            self.assertEqual((header[0], header[1], header[8]), (8, 1, SECOND))
            writes.clear()
            follower.update(snapshot, status(B), lambda _: status(A))
            self.assertEqual(writes, [])  # Host moved during the scan.
            follower.update((8, snapshot[1] | link.PINNED, FIRST, SSID), status(B), None)
            self.assertEqual(link.REPORT.unpack(writes[0][2])[1], 0)
            writes.clear()
            follower.update((8, snapshot[1] | link.BUSY, FIRST, SSID), status(B), None)
            self.assertEqual(writes, [])
        with patch.object(link, 'scan', return_value=[replace(B, open=True)]):
            with self.assertRaisesRegex(RuntimeError, 'security'):
                follower.update(snapshot, status(B), None)
        with patch.object(link, 'scan', return_value=[B]), \
                patch.object(link.fcntl, 'ioctl', side_effect=OSError(errno.ESTALE, 'New connection')):
            with self.assertRaises(OSError) as error:
                follower.update(snapshot, status(B), lambda _: status(B))
            self.assertEqual(error.exception.errno, errno.ESTALE)

    def test_loss_requires_confirmation_and_authorization_failure_is_not_loss(self):
        follower = link.LinkFollower(123)
        snapshot = (8, link.CONNECTED | link.PRIVATE, FIRST, SSID)
        missing = {'locationAuthorized': True, 'networkIdentityAvailable': False}
        with patch.object(link, 'report') as report, patch.object(link.time, 'monotonic', return_value=10):
            follower.update(snapshot, missing, None)
            report.assert_not_called()
        with patch.object(link, 'report') as report, patch.object(link.time, 'monotonic', return_value=10.5):
            follower.update(snapshot, missing, None)
            report.assert_not_called()
            with self.assertRaises(RuntimeError): follower.update(snapshot, dict(missing, locationAuthorized=False), None)
            report.assert_not_called()
        with patch.object(link, 'report') as report, patch.object(link.time, 'monotonic', return_value=11.1):
            follower.update(snapshot, missing, None)
            report.assert_called_once_with(123, 8, 0)
        with patch.object(link, 'report') as report:
            follower.update(snapshot, missing, None)
            follower.update(snapshot, status(), None)
            self.assertIsNone(follower.missing)
            report.assert_not_called()

    @unittest.skipUnless(sys.platform == 'linux', 'Linux inotify and poll integration')
    def test_status_replacement_heartbeat_and_directory_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / 'runtime'; parent.mkdir(); path = parent / 'status.json'
            def publish(revision):
                temp = path.with_suffix('.tmp')
                temp.write_text(json.dumps({'ok': True, 'statusTransport': 'virtio-serial',
                    'wifiRevision': revision, 'active': True, 'wakeGeneration': 0}))
                temp.replace(path)
            watcher = link.StatusWatch(path)
            try:
                watcher.open(); publish('epoch:1')
                self.assertTrue(select.select([watcher.fd], [], [], 1)[0])
                self.assertTrue(watcher.changed())
                self.assertFalse(select.select([watcher.fd], [], [], .05)[0])
                publish('epoch:1'); self.assertFalse(watcher.changed())
                publish('epoch:2'); self.assertTrue(watcher.changed())
                with patch.object(link.os, 'read', side_effect=[watcher.EVENT.pack(-1, 0x4000, 0, 0), BlockingIOError()]):
                    self.assertTrue(watcher.changed())
                self.assertIsNone(watcher.fd)
                watcher.open()
                parent.rename(Path(directory) / 'previous'); parent.mkdir()
                self.assertTrue(watcher.changed()); self.assertIsNone(watcher.fd)
                watcher.open(); publish('new:1'); self.assertTrue(watcher.changed())
            finally: watcher.close()
            self.assertIsNone(watcher.fd)


if __name__ == '__main__':
    unittest.main()
