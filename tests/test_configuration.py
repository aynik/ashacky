import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
ROOT = Path(__file__).resolve().parents[1]

def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

transport = module('transport_config', 'host/transport/vm_session.py')
devices = module('devices_config', 'guest/devices/probe-devices.py')
builder = module('builder', 'tools/ashacky.py')
session = module('session', 'host/session/session.py')

class ConfigurationTests(unittest.TestCase):
    def test_transport_has_no_developer_vm_fallback(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            transport.settings()

    def test_transport_rejects_writable_config(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config = directory / 'session.json'
            config.write_text(json.dumps({'uuid': str(uuid.uuid4()), 'privateDirectory': temp,
                'managementSubnet': '192.0.2.0/24', 'fallbackAddress': '192.0.2.2'}))
            config.chmod(0o600)
            with patch.dict(os.environ, {'LINUXHOST_SESSION_CONFIG': str(config)}):
                self.assertEqual(transport.settings()['privateDirectory'], directory)
                config.chmod(0o666)
                with self.assertRaises(ValueError): transport.settings()

    def test_virtual_devices_refuse_wrong_vm_or_nic(self):
        config = {'vmUUID': str(uuid.uuid4()), 'wifiInterface': 'ens2', 'wifiMAC': '02:00:00:00:00:02'}
        self.assertEqual(devices.validate(config, config['vmUUID'], config['wifiMAC']), ('ens2', 10))
        with self.assertRaises(ValueError): devices.validate(config, str(uuid.uuid4()), config['wifiMAC'])
        with self.assertRaises(ValueError): devices.validate(config, config['vmUUID'], '02:00:00:00:00:03')

    def test_usb_topology_preserves_four_explicit_root_ports(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {'runtime': temp, 'uuid': str(uuid.uuid4()), 'macs': ['02:00:00:00:00:01', '02:00:00:00:00:02'],
                'vars': temp + '/vars', 'disk': temp + '/disk', 'shared': temp + '/shared',
                'diskSerial': 'TESTDISK', 'usbSocket': temp + '/usb.sock'}
            args = session.arguments(config, [5, 6])
            redirs = [value for value in args if value.startswith('usb-redir,')]
            self.assertEqual(len(redirs), 4)
            for index, value in enumerate(redirs, 1):
                self.assertTrue(value.endswith(f'bus=usb-controller-0.0,port={index}'))
            self.assertIn('hvf,ipa-granule-size=0x1000', args)
            self.assertIn('spiceport,id=ashacky-status,name=org.ashacky.status', args)
            self.assertIn('virtserialport,chardev=ashacky-status,name=org.ashacky.status', args)
            self.assertIn('spiceport,id=ashacky-control,name=org.ashacky.control', args)
            self.assertIn('virtserialport,chardev=ashacky-control,name=org.ashacky.control', args)
            self.assertTrue(any('venus=true,neptune=true' in value for value in args))
            seeded = session.arguments(dict(config, provisioningISO=temp + '/seed.iso'), [5, 6])
            self.assertTrue(any('media=cdrom,readonly=on,file=' in value for value in seeded))
            self.assertIn('usb-storage,drive=ashacky-seed,bus=usb-bus.0', seeded)
            self.assertEqual(redirs, [value for value in seeded if value.startswith('usb-redir,')])

    def test_privileged_build_requires_explicit_nonroot_identity(self):
        config = {'userID': 501, 'groupID': 20, 'usbRuntime': '/var/run/ashacky-test-usb',
                  'powerRuntime': '/var/run/ashacky-test-power',
                  'powerPolicy': '/Library/Application Support/Ashacky-Test/power.json',
                  'powerClients': ['/tmp/example/Ashacky.app/Contents/MacOS/Ashacky']}
        with tempfile.TemporaryDirectory() as temp:
            builder.host_constants(config, Path(temp))
            self.assertIn('ASHACKY_USER_ID 501', (Path(temp) / 'installation.h').read_text())
            config['userID'] = 0
            with self.assertRaises(ValueError): builder.host_constants(config, Path(temp))
