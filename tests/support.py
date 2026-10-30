"""Shared test setup. No iPhone needed, and nothing touches the real user data."""
import atexit
import os
import shutil
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_temp = tempfile.mkdtemp(prefix='iphone-rocket-tests-')
atexit.register(shutil.rmtree, _temp, ignore_errors=True)
os.environ['IPHONE_ROCKET_DATA'] = os.path.join(_temp, 'data')
os.environ['IPHONE_ROCKET_EXPORT'] = os.path.join(_temp, 'export')

import iphone  # noqa: E402
import rocket  # noqa: E402

os.makedirs(rocket.DATA_DIR, exist_ok=True)


class FakeSocket:
    """Plays back scripted bytes and records everything that is sent."""

    def __init__(self, incoming=b''):
        self.incoming = bytearray(incoming)
        self.sent = bytearray()

    def sendall(self, data):
        self.sent += data

    def recv_into(self, view, size):
        chunk = bytes(self.incoming[:size])
        del self.incoming[:size]
        view[:len(chunk)] = chunk
        return len(chunk)

    def close(self):
        pass

    def sent_packets(self):
        """The AFC requests that were sent, in order, as (opcode, payload)."""
        packets, data = [], bytes(self.sent)
        while data:
            _, total, _, _, op = struct.unpack('<8sQQQQ', data[:40])
            packets.append((op, data[40:total]))
            data = data[total:]
        return packets

    def sent_ops(self):
        return [op for op, _ in self.sent_packets()]


def afc_packet(op, payload=b''):
    total = 40 + len(payload)
    return struct.pack('<8sQQQQ', iphone.AFC_MAGIC, total, total, 1, op) + payload


def status_packet(code=0):
    return afc_packet(iphone.AFC_STATUS, struct.pack('<Q', code))


DATA = 0x02
OPEN_RESULT = 0x0E


def new_library(key='0123456789abcdef'):
    library = rocket.Library()
    library.open_store(key)
    return library
