#!/usr/bin/env python3
"""Exercise the Swift TEST-ONLY signer with Yubico's independent CTAP2 client.

Build tests/fido2-peer.swift on macOS, then pass its command after --. A one-off
administrative SSH command is supported for Linux-to-macOS source testing; it is
never installed or used as an authenticator runtime transport.
"""
import base64
import hashlib
import json
import selectors
import subprocess
import sys

from fido2 import cbor
from fido2.attestation import PackedAttestation
from fido2.ctap import CtapDevice, CtapError
from fido2.ctap2 import Ctap2
from fido2.hid import CAPABILITY, CTAPHID


class Peer(CtapDevice):
    capabilities = CAPABILITY.CBOR

    def __init__(self, command):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.channel = '00000001'
        self.options = {}
        self.last = {}

    @classmethod
    def list_devices(cls):
        return iter(())

    def call(self, cmd, data=b'', event=None, on_keepalive=None):
        assert cmd == CTAPHID.CBOR
        value = dict(data=base64.b64encode(data).decode(), channel=self.channel, **self.options)
        self.process.stdin.write(json.dumps(value).encode() + b'\n'); self.process.stdin.flush()
        if not self.selector.select(10):
            self.process.kill(); raise TimeoutError('Swift fixture stopped responding')
        self.last = json.loads(self.process.stdout.readline())
        assert self.last['fixture'] == 'ashacky-fido2-software-test-only'
        return base64.b64decode(self.last['data'], validate=True)

    def raw(self, data, **options):
        self.options = options
        try:
            return self.call(CTAPHID.CBOR, data)
        finally:
            self.options = {}

    def close(self):
        self.process.stdin.close()
        try:
            assert self.process.wait(timeout=5) == 0
        finally:
            if self.process.poll() is None:
                self.process.kill(); self.process.wait()
            self.process.stdout.close(); self.selector.close()


def main():
    command = sys.argv[1:]
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        raise SystemExit('Usage: check-fido2.py -- /path/to/compiled-test-peer [arguments]')
    with Peer(command) as peer:
        ctap = Ctap2(peer)
        assert ctap.info.versions == ['FIDO_2_0']
        assert ctap.info.options == dict(rk=True, up=True, uv=True, plat=False, alwaysUv=True)
        assert 'clientPin' not in ctap.info.options
        keys = {}
        for name in ('alice', 'bob'):
            client_hash = hashlib.sha256(('register-' + name).encode()).digest()
            att = ctap.make_credential(client_hash, dict(id='example.invalid', name='Fixture'),
                dict(id=name.encode(), name=name, displayName=name.title()),
                [dict(type='public-key', alg=-7)], options=dict(rk=True, uv=True), extensions=dict(credProtect=3))
            assert att.fmt == 'packed'
            assert att.auth_data.is_user_present() and att.auth_data.is_user_verified()
            assert att.auth_data.rp_id_hash == hashlib.sha256(b'example.invalid').digest()
            assert att.auth_data.extensions == {'credProtect': 3}
            PackedAttestation().verify(att.att_stmt, att.auth_data, client_hash)
            credential = att.auth_data.credential_data
            keys[credential.credential_id] = credential.public_key
        challenge = hashlib.sha256(b'fresh-authentication').digest()
        result = ctap.get_assertion('example.invalid', challenge, options=dict(uv=True))
        assert result.number_of_credentials == 2
        results = [result, ctap.get_next_assertion()]
        assert {r.user['id'] for r in results} == {b'alice', b'bob'}
        for result in results:
            keys[result.credential['id']].verify(bytes(result.auth_data) + challenge, result.signature)
            assert result.auth_data.is_user_verified() and result.auth_data.is_user_present()
            assert result.auth_data.counter == 0
        print('Independent CTAP2 registration, packed self-attestation, resident discovery and ECDSA assertions passed')

        def error(code, function):
            try:
                function()
            except CtapError as e:
                assert e.code == code, (e.code, code)
            else:
                raise AssertionError('Expected CTAP rejection')

        descriptor = dict(type='public-key', id=next(iter(keys)))
        error(0x2e, lambda: ctap.get_assertion('different.invalid', challenge, [descriptor]))
        error(0x2e, lambda: ctap.get_assertion('example.invalid', challenge, [descriptor], options=dict(up=False)))
        error(0x26, lambda: ctap.make_credential(challenge, dict(id='example.invalid'), dict(id=b'c'), [dict(type='public-key', alg=-8)]))
        error(0x19, lambda: ctap.make_credential(challenge, dict(id='example.invalid'), dict(id=b'c'), [dict(type='public-key', alg=-7)], [descriptor]))
        error(0x01, lambda: ctap.reset())
        request = b'\x02' + cbor.encode({1: 'example.invalid', 2: challenge})
        assert peer.raw(request, allowed=False) == b'\x27'
        before = peer.last['signatures']
        assert peer.raw(request, cancelled=True) == b'\x2d'
        assert peer.last['signatures'] == before
        ctap.get_assertion('example.invalid', challenge)
        peer.channel = '00000002'
        error(0x30, lambda: ctap.get_next_assertion())
        peer.channel = '00000001'
        error(0x30, lambda: ctap.get_next_assertion())
        print('RP isolation, excluded credentials, silent-probe denial, cancellation and channel-scoped continuation passed')

        for invalid in (b'\x01\xa2\x01\x00\x01\x00', b'\x01\xa0\x00', b'\x01\xbf\xff',
                        b'\x01\x81' * 12, b'\x01\xa1\x01\x18\x01', b'\x01\xa1\x01\x7f'):
            assert peer.raw(invalid) == b'\x12', invalid.hex()
        assert peer.raw(b'\x01' + b'\0' * 7610) == b'\x03'
        assert peer.last['stored'] == 2
        print('Duplicate/noncanonical CBOR, indefinite/truncated/trailing data and message bounds rejected without storing credentials')

        protected = ctap.make_credential(challenge, dict(id='probe.invalid'), dict(id=b'probe'),
            [dict(type='public-key', alg=-7)], options=dict(rk=True), extensions=dict(credProtect=2))
        assert protected.auth_data.extensions == {'credProtect': 2}
        probe_key = protected.auth_data.credential_data
        before = peer.last['signatures']
        probe = ctap.get_assertion('probe.invalid', challenge,
            [dict(type='public-key', id=probe_key.credential_id)], options=dict(up=False))
        assert peer.last['signatures'] == before
        assert probe.user is None and probe.number_of_credentials is None
        assert not probe.auth_data.is_user_present() and not probe.auth_data.is_user_verified()
        try:
            probe_key.public_key.verify(bytes(probe.auth_data) + challenge, probe.signature)
        except Exception:
            pass
        else:
            raise AssertionError('A pre-flight probe must never be a valid signature')
        error(0x2e, lambda: ctap.get_assertion('probe.invalid', challenge, options=dict(up=False)))
        error(0x2e, lambda: ctap.get_assertion('wrong.invalid', challenge,
            [dict(type='public-key', id=probe_key.credential_id)], options=dict(up=False)))
        assert peer.last['signatures'] == before
        real = ctap.get_assertion('probe.invalid', challenge, [dict(type='public-key', id=probe_key.credential_id)])
        probe_key.public_key.verify(bytes(real.auth_data) + challenge, real.signature)
        assert real.auth_data.is_user_present() and real.auth_data.is_user_verified()
        print('Firefox-style known-ID pre-flight uses no key/signature or user metadata; RP isolation, strict protection and verified real signing passed')


if __name__ == '__main__':
    main()
