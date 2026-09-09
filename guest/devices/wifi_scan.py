"""Validated host scan records for the Linux wireless frontend.

No simulated access points or inferred security capabilities. The eventual
cfg80211 adapter must publish these real BSS records and report connection
success only after the host confirms association.
"""
import base64
import dataclasses
import json
import re
import socket
import uuid


@dataclasses.dataclass(frozen=True)
class BSS:
    network_id: str
    ssid: bytes
    bssid: bytes
    frequency_mhz: int
    signal_mbm: int
    information_elements: bytes
    open: bool


def frequency(band, channel):
    if type(channel) is not int or type(band) is not int:
        raise ValueError('Non-integer channel')
    if band == 1 and 1 <= channel <= 13:
        return 2407 + 5 * channel
    if band == 1 and channel == 14:
        return 2484
    if band == 2 and 1 <= channel <= 177:
        return 5000 + 5 * channel
    if band == 3 and channel == 2:
        return 5935
    if band == 3 and 1 <= channel <= 233:
        return 5950 + 5 * channel
    raise ValueError('Unsupported channel/band')


def parse_bss(entry):
    if type(entry.get('open')) is not bool:
        raise ValueError('Host omitted network security classification')
    network_id = entry['networkID']
    uuid.UUID(network_id)
    ssid = base64.b64decode(entry['ssidBase64'], validate=True)
    if len(ssid) > 32:
        raise ValueError('SSID exceeds IEEE 802.11 limit')
    address = entry['bssid']
    if not isinstance(address, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', address):
        raise ValueError('Invalid BSSID')
    bssid = bytes.fromhex(address.replace(':', ''))
    if bssid[0] & 1 or not any(bssid):
        raise ValueError('BSSID is not a unicast address')
    signal = entry['signalDBm']
    if type(signal) is not int or not -127 <= signal <= 0:
        raise ValueError('Invalid signal strength')
    ies = base64.b64decode(entry['informationElementsBase64'], validate=True)
    if len(ies) > 4096:
        raise ValueError('Oversized information elements')
    offset = 0
    ssid_seen = False
    while offset < len(ies):
        if offset + 2 > len(ies) or offset + 2 + ies[offset + 1] > len(ies):
            raise ValueError('Truncated information element')
        tag, length = ies[offset:offset + 2]
        if tag == 0:
            if ssid_seen or ies[offset + 2:offset + 2 + length] != ssid:
                raise ValueError('Conflicting SSID information')
            ssid_seen = True
        offset += 2 + length
    if not ssid_seen:
        ies = bytes([0, len(ssid)]) + ssid + ies
    return BSS(network_id, ssid, bssid, frequency(entry['channelBand'], entry['channel']), signal * 100, ies, entry['open'])


def agent_rpc(request):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(85)
        client.connect('/run/linuxhost/agent.sock')
        client.sendall(json.dumps(request).encode() + b'\n')
        line = client.makefile('rb').readline(16384)
        if not line.endswith(b'\n'):
            raise ValueError('Truncated host reply')
        result = json.loads(line)
        if not result.get('ok'):
            raise RuntimeError(result.get('error', 'Host scan failed'))
        return result


def scan(rpc=agent_rpc):
    page = rpc({'action': 'wifi-scan'})
    scan_id = page['scanID']
    uuid.UUID(scan_id)
    records = []
    expected_offset = 0
    total = page['total']
    if type(total) is not int or not 0 <= total <= 256 or page.get('truncated'):
        raise ValueError('Incomplete or invalid scan size')
    while True:
        if page['scanID'] != scan_id or page['total'] != total:
            raise ValueError('Scan changed during pagination')
        entries = page['networks']
        if not isinstance(entries, list) or len(entries) > 2:
            raise ValueError('Invalid scan page')
        records.extend(parse_bss(entry) for entry in entries)
        expected_offset += len(entries)
        next_offset = page.get('nextOffset')
        if next_offset is None:
            if expected_offset != total:
                raise ValueError('Incomplete scan')
            return records
        if type(next_offset) is not int or next_offset != expected_offset or not entries or next_offset >= total:
            raise ValueError('Invalid scan cursor')
        page = rpc({'action': 'wifi-scan-page', 'scanID': scan_id, 'offset': next_offset})
