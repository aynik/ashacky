import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import selectors
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('ashacky_control', str(ROOT / 'guest/libexec/ashacky-control'))
spec = importlib.util.spec_from_loader(loader.name, loader)
control = importlib.util.module_from_spec(spec)
loader.exec_module(control)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.port, self.host = socket.socketpair()
        self.port.setblocking(False)
        self.host.settimeout(1)
        self.temp = tempfile.TemporaryDirectory()
        self.broker = control.Broker(self.port.fileno(), 'x' * 32, Path(self.temp.name))
        self.broker.step()
        self.hello = json.loads(self.host.recv(65536))
        self.session = str(uuid.uuid4())

    def tearDown(self):
        self.broker.close()
        self.port.close(); self.host.close(); self.temp.cleanup()

    def welcome(self, **updates):
        value = dict(version=1, type='welcome', nonce=self.hello['nonce'], token='x' * 32,
                     session=self.session)
        value.update(updates)
        self.broker.receive(value)

    def receive(self, **fields):
        self.broker.receive(dict(version=1, session=self.session, **fields))

    def client(self, service='host'):
        # Protocol fixture: listener peer-UID validation is exercised separately.
        client, peer = socket.socketpair()
        client.setblocking(False); peer.settimeout(1)
        self.broker.clients[client] = dict(service=service, frames=control.Frames(),
            deadline=time.monotonic() + 5, output=bytearray(), id=None)
        self.broker.selector.register(client, selectors.EVENT_READ, 'client')
        self.addCleanup(peer.close)
        return client, peer

    def test_fragmented_and_multiple_frames(self):
        raw = control.encode({'test': 'é'}) + control.encode({'test': 2})
        parser = control.Frames()
        result = [value for byte in raw for value in parser.feed(bytes([byte]))]
        self.assertEqual([v['test'] for v in result], ['é', 2])
        self.assertEqual(len(list(control.Frames().feed(raw))), 2)

    def test_unterminated_oversized_and_nonobject_frames(self):
        for raw in (b'a' * (control.MAX_FRAME + 2), b'[]\n', b'{bad}\n'):
            with self.assertRaises(ValueError):
                list(control.Frames().feed(raw))
        with self.assertRaises(ValueError):
            control.encode({'large': 'x' * control.MAX_FRAME})

    def test_handshake_checks_token_nonce_version_and_generation(self):
        for update in ({'token': 'wrong'}, {'nonce': 'wrong'}, {'version': True}):
            with self.assertRaises((PermissionError, ValueError)):
                self.welcome(**update)
        self.welcome()
        with self.assertRaises(PermissionError):
            self.broker.receive(dict(version=1, type='response', session=str(uuid.uuid4()), id=1, payload={}))

    def test_parallel_rpc_responses_are_correlated(self):
        self.welcome()
        one, first = self.client('wifi')
        two, second = self.client('bluetooth')
        first.sendall(b'{"action":"wifi-scan"}\n')
        second.sendall(b'{"action":"audio-devices"}\n')
        self.broker.step(); self.broker.step()
        messages = list(control.Frames().feed(self.host.recv(65536)))
        self.assertEqual([m['service'] for m in messages], ['wifi', 'bluetooth'])
        self.receive(type='response', id=2, payload={'ok': True, 'second': True})
        self.broker.step()
        self.assertTrue(json.loads(second.recv(4096))['second'])
        self.assertIn(one, self.broker.clients)
        self.receive(type='response', id=1, payload={'ok': True})
        self.broker.step()
        self.assertTrue(json.loads(first.recv(4096))['ok'])

    def test_disconnected_requests_fail_without_replay(self):
        self.welcome()
        client, peer = self.client()
        peer.sendall(b'{"action":"authenticate","purpose":"sudo"}\n')
        self.broker.step(); self.broker.step()
        self.host.recv(65536)
        self.broker.close_client(client)
        self.assertFalse(self.broker.pending)
        self.receive(type='response', id=1, payload={'ok': True})
        self.assertEqual(peer.recv(4096), b'')

    def test_fido_cancellation_can_pass_while_authentication_waits(self):
        self.welcome()
        pending, first = self.client('fido2')
        cancellation, second = self.client('fido2')
        first.sendall(b'{"action":"request","id":"fixture"}\n')
        self.broker.step(); self.broker.step()
        message = json.loads(self.host.recv(65536))
        self.assertEqual(message['service'], 'fido2')
        self.assertGreater(self.broker.clients[pending]['deadline'] - time.monotonic(), 75)
        second.sendall(b'{"action":"cancel","id":"fixture"}\n')
        self.broker.step(); self.broker.step()
        message = json.loads(self.host.recv(65536))
        self.assertEqual(message['payload']['action'], 'cancel')
        self.receive(type='response', id=2, payload={'ok': True})
        self.broker.step()
        self.assertTrue(json.loads(second.recv(4096))['ok'])
        self.assertIn(pending, self.broker.clients)

    def test_handshake_and_backpressure_deadlines(self):
        self.broker.hello_deadline = time.monotonic() - 1
        with self.assertRaises(TimeoutError):
            self.broker.step()
        self.welcome()
        self.broker.send({'type': 'state', 'state': {}})
        with patch.object(control.os, 'write', side_effect=BlockingIOError):
            self.broker.write_deadline = time.monotonic() - 1
            with self.assertRaises(TimeoutError):
                self.broker.step()
        self.broker.outgoing = bytearray(control.MAX_QUEUE)
        with self.assertRaises(ValueError):
            self.broker.send({'type': 'state'})

    def test_host_cannot_execute_guest_commands(self):
        self.welcome()
        for action in ('lock', 'unlock', 'shutdown', 'sh'):
            with self.assertRaises(ValueError):
                self.receive(type='command', id=1, action=action)

    @unittest.skipIf(os.geteuid() == 0, 'This case verifies rejection of an unprivileged peer')
    def test_private_sockets_reject_nonroot_even_if_directory_accessible(self):
        self.broker.listen()
        for name in ('host', 'fido2'):
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(str(Path(self.temp.name) / (name + '.sock')))
                self.broker.step()
                self.assertEqual(client.recv(4096), b'')
        self.assertFalse(self.broker.clients)

    def test_port_eof_and_payload_do_not_enter_public_status(self):
        self.welcome()
        self.host.shutdown(socket.SHUT_WR)
        with self.assertRaises(EOFError):
            self.broker.step()
        self.assertNotIn('status.json', (ROOT / 'guest/libexec/ashacky-control').read_text())
