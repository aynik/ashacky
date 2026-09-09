"""CTAPHID fixtures; never open a real HID device or request authentication."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ashacky_fido2', ROOT / 'guest/auth/fido2.py')
fido = importlib.util.module_from_spec(spec); spec.loader.exec_module(fido)


class HIDTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.output, self.requests, self.cancels = [], [], []
        self.hid = fido.HID(self.output.append, lambda *args: self.requests.append(args), self.cancels.append, lambda: self.now)
        self.cid = self.allocate()

    def feed(self, cid, cmd, data):
        for packet in fido.packets(cid, cmd, data):
            self.hid.receive(packet)

    def allocate(self):
        nonce = b'12345678'
        self.feed(fido.BROADCAST, fido.INIT, nonce)
        response = self.output.pop()
        self.assertEqual(response[7:15], nonce)
        self.assertEqual(response[19:24], bytes([2, 0, 1, 0, 0x0c]))
        return struct.unpack('>I', response[15:19])[0]

    def test_ping_maximum_payload_roundtrip(self):
        payload = bytes(i % 251 for i in range(fido.MAX_MESSAGE))
        self.feed(self.cid, fido.PING, payload)
        self.assertEqual(self.output, list(fido.packets(self.cid, fido.PING, payload)))
        self.assertIsNone(self.hid.deadline())

    def test_request_correlation_cancel_and_stale_completion(self):
        self.feed(self.cid, fido.CBOR, b'\x04')
        request, channel, raw = self.requests.pop()
        self.assertEqual((channel, raw), (self.cid, b'\x04'))
        self.feed(self.cid, fido.CANCEL, b'')
        self.assertEqual(self.cancels, [request])
        self.assertEqual(self.output[-1][7], 0x2d)
        count = len(self.output)
        self.hid.complete(request, b'\0old')
        self.assertEqual(len(self.output), count)
        self.feed(self.cid, fido.CBOR, b'\x04')
        second = self.requests.pop()[0]
        self.hid.complete(request, b'\0old')
        self.assertIsNotNone(self.hid.busy)
        self.hid.complete(second, b'\0new')
        self.assertEqual(self.output[-1][7:11], b'\0new')

    def test_channel_isolation_and_invalid_channel(self):
        other = self.allocate()
        self.feed(self.cid, fido.CBOR, b'\x04')
        self.feed(other, fido.CANCEL, b'')
        self.assertFalse(self.cancels)
        self.feed(other, fido.PING, b'test')
        self.assertEqual(self.output[-1][7], 6)
        self.feed(0, fido.PING, b'')
        self.assertEqual(self.output[-1][7], 0x0b)

    def test_fragment_deadline_is_not_extended_by_unrelated_packets(self):
        first = next(fido.packets(self.cid, fido.PING, b'x' * 500))
        self.hid.receive(first)
        self.now += 2
        self.allocate()
        self.now += 1.1
        self.hid.timeout()
        self.assertEqual(self.output[-1][7], 5)
        self.assertIsNone(self.hid.fragment)

    def test_sequence_length_and_unsupported_command(self):
        packets = list(fido.packets(self.cid, fido.PING, b'x' * 100))
        self.hid.receive(packets[0])
        bad = bytearray(packets[1]); bad[4] = 1
        self.hid.receive(bytes(bad))
        self.assertEqual(self.output[-1][7], 4)
        self.hid.receive(struct.pack('>IBH', self.cid, fido.CBOR, 7610).ljust(64, b'\0'))
        self.assertEqual(self.output[-1][7], 3)
        self.feed(self.cid, 0x83, b'legacy U2F')
        self.assertEqual(self.output[-1][7], 1)

    def test_keepalive_and_expiry_only_while_busy(self):
        self.assertIsNone(self.hid.deadline())
        self.feed(self.cid, fido.CBOR, b'\x01\xa0')
        self.now += 0.3
        self.hid.timeout()
        self.assertEqual(self.output[-1][4], fido.KEEPALIVE)
        self.now += 65
        self.hid.timeout()
        self.assertEqual(self.output[-1][7], 0x2f)
        self.assertEqual(len(self.cancels), 1)
        self.assertIsNone(self.hid.deadline())

    def test_init_resynchronization_cancels_pending_operation(self):
        self.feed(self.cid, fido.CBOR, b'\x04')
        request = self.requests[-1][0]
        self.feed(self.cid, fido.INIT, b'abcdefgh')
        self.assertEqual(self.cancels, [request])
        self.assertIsNone(self.hid.busy)
        self.hid.complete(request, b'\0stale')
        self.assertEqual(self.output[-1][4], fido.INIT)

    def test_channel_allocation_is_bounded(self):
        for _ in range(100):
            self.allocate()
        self.assertEqual(len(self.hid.channels), 64)

    def test_channel_allocation_preserves_incomplete_request(self):
        fragments = list(fido.packets(self.cid, fido.PING, b'x' * 100))
        self.hid.receive(fragments[0])
        for _ in range(100):
            self.allocate()
        self.assertIn(self.cid, self.hid.channels)
        self.hid.receive(fragments[1])
        self.assertEqual(self.output, fragments)

    def test_new_command_cannot_splice_an_incomplete_request(self):
        self.hid.receive(next(fido.packets(self.cid, fido.CBOR, b'x' * 100)))
        self.feed(self.cid, fido.CBOR, b'\x04')
        self.assertFalse(self.requests)
        self.assertIsNone(self.hid.fragment)
        self.assertEqual(self.output[-1][7], 4)


if __name__ == '__main__':
    unittest.main()
