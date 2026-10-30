import plistlib
import struct
import unittest

from support import DATA, OPEN_RESULT, FakeSocket, afc_packet, iphone, status_packet


class ReadOnlyContract(unittest.TestCase):
    """This file's promise: the direct connection can never change the iPhone."""

    def test_only_read_operations_exist(self):
        operations = {name for name in dir(iphone) if name.startswith('AFC_') and isinstance(getattr(iphone, name), int)}
        self.assertEqual(operations, {
            'AFC_STATUS', 'AFC_READ_DIR', 'AFC_GET_FILE_INFO', 'AFC_FILE_OPEN',
            'AFC_FILE_READ', 'AFC_FILE_SEEK', 'AFC_FILE_CLOSE', 'AFC_MODE_READ',
        })

    def test_files_are_always_opened_for_reading(self):
        self.assertEqual(iphone.AFC_MODE_READ, 1)
        sock = FakeSocket(afc_packet(OPEN_RESULT, struct.pack('<Q', 7)) + afc_packet(DATA, b'') + status_packet())
        iphone.AFC(sock).read('/x')
        mode = struct.unpack('<Q', bytes(sock.sent[40:48]))[0]
        self.assertEqual(mode, iphone.AFC_MODE_READ)


class AFCTests(unittest.TestCase):
    def test_listdir_hides_dots(self):
        afc = iphone.AFC(FakeSocket(afc_packet(DATA, b'.\0..\0IMG_1.JPG\0IMG_2.MOV\0')))
        self.assertEqual(afc.listdir('/DCIM/100APPLE'), ['IMG_1.JPG', 'IMG_2.MOV'])

    def test_listdir_tolerates_bad_utf8(self):
        afc = iphone.AFC(FakeSocket(afc_packet(DATA, b'a\xff.JPG\0')))
        self.assertEqual(afc.listdir('/x'), ['a�.JPG'])

    def test_info_parses_pairs(self):
        afc = iphone.AFC(FakeSocket(afc_packet(DATA, b'st_size\x0012\0st_ifmt\0S_IFREG\0')))
        self.assertEqual(afc.info('/x'), {'st_size': '12', 'st_ifmt': 'S_IFREG'})

    def test_missing_path_raises_with_status(self):
        afc = iphone.AFC(FakeSocket(status_packet(8)))
        with self.assertRaises(iphone.AFCError) as caught:
            afc.info('/missing')
        self.assertEqual(caught.exception.status, 8)
        self.assertEqual(caught.exception.code, 'afc_error')

    def test_exists(self):
        self.assertTrue(iphone.AFC(FakeSocket(afc_packet(DATA, b'st_size\x001\0'))).exists('/x'))
        self.assertFalse(iphone.AFC(FakeSocket(status_packet(8))).exists('/x'))

    def test_info_many_keeps_going_after_a_missing_file(self):
        incoming = afc_packet(DATA, b'st_size\x001\0') + status_packet(8) + afc_packet(DATA, b'st_size\x003\0')
        result = iphone.AFC(FakeSocket(incoming)).info_many(['/a', '/b', '/c'])
        self.assertEqual(result, {'/a': {'st_size': '1'}, '/b': None, '/c': {'st_size': '3'}})

    def test_bad_magic_is_refused(self):
        sock = FakeSocket(struct.pack('<8sQQQQ', b'NOTMAGIC', 40, 40, 1, DATA))
        with self.assertRaises(iphone.IPhoneError) as caught:
            iphone.AFC(sock).listdir('/x')
        self.assertEqual(caught.exception.code, 'bad_response')

    def test_cut_connection_is_reported(self):
        with self.assertRaises(iphone.IPhoneError) as caught:
            iphone.AFC(FakeSocket(b'short')).listdir('/x')
        self.assertEqual(caught.exception.code, 'connection_lost')


class StreamTests(unittest.TestCase):
    def script(self, *chunks):
        packets = afc_packet(OPEN_RESULT, struct.pack('<Q', 7))
        return packets + b''.join(afc_packet(DATA, c) for c in chunks) + status_packet()

    def test_reads_until_short_chunk(self):
        afc = iphone.AFC(FakeSocket(self.script(b'hello')))
        self.assertEqual(afc.read('/x'), b'hello')

    def test_seeks_only_when_needed(self):
        sock = FakeSocket(self.script(b'a'))
        iphone.AFC(sock).read('/x')
        self.assertNotIn(iphone.AFC_FILE_SEEK, sock.sent_ops())

        sock = FakeSocket(afc_packet(OPEN_RESULT, struct.pack('<Q', 7)) + status_packet() + afc_packet(DATA, b'a') + status_packet())
        list(iphone.AFC(sock).stream('/x', limit=1, offset=100))
        self.assertEqual(sock.sent_ops(), [iphone.AFC_FILE_OPEN, iphone.AFC_FILE_SEEK, iphone.AFC_FILE_READ, iphone.AFC_FILE_CLOSE])

    def test_limit_is_respected(self):
        sock = FakeSocket(self.script(b'abc'))
        self.assertEqual(iphone.AFC(sock).read('/x', limit=3), b'abc')
        read_payload = next(payload for op, payload in sock.sent_packets() if op == iphone.AFC_FILE_READ)
        self.assertEqual(struct.unpack('<QQ', read_payload), (7, 3))

    def test_abandoned_stream_still_closes_the_file(self):
        """A file left open would corrupt the next request on this pooled connection."""
        sock = FakeSocket(self.script(b'x' * iphone.CHUNK))
        stream = iphone.AFC(sock).stream('/x')
        next(stream)
        stream.close()
        self.assertEqual(sock.sent_ops()[-1], iphone.AFC_FILE_CLOSE)


class FramingTests(unittest.TestCase):
    def test_lockdown_call(self):
        body = plistlib.dumps({'Value': 'iPhone'})
        sock = FakeSocket(struct.pack('>I', len(body)) + body)
        self.assertEqual(iphone.lockdown_call(sock, {'Request': 'GetValue'}), {'Value': 'iPhone'})
        size = struct.unpack('>I', bytes(sock.sent[:4]))[0]
        self.assertEqual(plistlib.loads(bytes(sock.sent[4:4 + size]))['Label'], iphone.LABEL)

    def test_usb_only(self):
        """AFC is not encrypted: a phone on Wi-Fi must never be used."""
        reply = plistlib.dumps({'DeviceList': [
            {'DeviceID': 1, 'Properties': {'ConnectionType': 'Network', 'SerialNumber': 'wifi'}},
            {'DeviceID': 2, 'Properties': {'ConnectionType': 'USB', 'SerialNumber': 'cable'}},
        ]})

        class FakeMux:
            def request(self, message):
                return plistlib.loads(reply)

            def close(self):
                pass

        original, iphone.Usbmux = iphone.Usbmux, FakeMux
        try:
            self.assertEqual(iphone.list_devices(), [{'id': 2, 'udid': 'cable'}])
        finally:
            iphone.Usbmux = original


if __name__ == '__main__':
    unittest.main()
