"""Direct iPhone access over USB: usbmuxd -> lockdownd -> AFC.

Standard library only. READ-ONLY on purpose: this file implements no AFC write,
rename or delete operation, and tests/test_protocol.py keeps it that way.
"""
import contextlib
import os
import plistlib
import queue
import socket
import ssl
import struct
import tempfile
import threading

USBMUX_ADDR = ('127.0.0.1', 27015)
LOCKDOWN_PORT = 62078
LABEL = 'iPhoneRocket'

AFC_MAGIC = b'CFA6LPAA'
AFC_STATUS = 0x01
AFC_READ_DIR = 0x03
AFC_GET_FILE_INFO = 0x0A
AFC_FILE_OPEN = 0x0D
AFC_FILE_READ = 0x0F
AFC_FILE_SEEK = 0x11
AFC_FILE_CLOSE = 0x14
AFC_MODE_READ = 1
CHUNK = 1024 * 1024
INFO_WINDOW = 64


class IPhoneError(Exception):
    """A failure with a stable code that the interface translates."""

    def __init__(self, code='device_error', **params):
        super().__init__(code)
        self.code = code
        self.params = params


class ServiceStopped(IPhoneError):
    """Nothing listens on the usbmuxd port: the Apple service is not running."""

    def __init__(self):
        super().__init__('service_stopped')


class AFCError(IPhoneError):
    def __init__(self, status, path=''):
        super().__init__('afc_error', status=status, path=path)
        self.status = status
        self.path = path


def recv_exact(sock, size):
    buf = bytearray(size)
    view = memoryview(buf)
    done = 0
    while done < size:
        got = sock.recv_into(view[done:], size - done)
        if not got:
            raise IPhoneError('connection_lost')
        done += got
    return bytes(buf)


class Usbmux:
    def __init__(self):
        try:
            self.sock = socket.create_connection(USBMUX_ADDR, timeout=3)
        except OSError as exc:
            raise ServiceStopped() from exc
        self.sock.settimeout(30)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.tag = 0

    def request(self, message):
        self.tag += 1
        body = plistlib.dumps({
            'ClientVersionString': LABEL,
            'ProgName': LABEL,
            'kLibUSBMuxVersion': 3,
            **message,
        })
        self.sock.sendall(struct.pack('<IIII', len(body) + 16, 1, 8, self.tag) + body)
        length = struct.unpack('<IIII', recv_exact(self.sock, 16))[0]
        return plistlib.loads(recv_exact(self.sock, length - 16))

    def close(self):
        self.sock.close()


def list_devices():
    """USB devices only: AFC is not encrypted, so it must never travel over Wi-Fi."""
    mux = Usbmux()
    try:
        devices = mux.request({'MessageType': 'ListDevices'}).get('DeviceList', [])
    finally:
        mux.close()
    return [
        {'id': d['DeviceID'], 'udid': d['Properties'].get('SerialNumber', '')}
        for d in devices if d['Properties'].get('ConnectionType') == 'USB'
    ]


def read_pair_record(udid):
    mux = Usbmux()
    try:
        data = mux.request({'MessageType': 'ReadPairRecord', 'PairRecordID': udid}).get('PairRecordData')
    finally:
        mux.close()
    if not data:
        raise IPhoneError('not_trusted')
    return plistlib.loads(data)


def tunnel(device_id, port):
    mux = Usbmux()
    answer = mux.request({
        'MessageType': 'Connect',
        'DeviceID': device_id,
        'PortNumber': socket.htons(port),
    })
    if answer.get('Number') != 0:
        mux.close()
        raise IPhoneError('connection_refused', port=port, reason=answer.get('Number'))
    mux.sock.settimeout(60)
    return mux.sock


def tls_context(pair):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers('ALL:!aNULL:!eNULL:@SECLEVEL=0')
    # load_cert_chain only accepts files: the key stays on disk just for this call.
    fd, path = tempfile.mkstemp(suffix='.pem')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(pair['HostCertificate'])
            handle.write(b'\n')
            handle.write(pair['HostPrivateKey'])
        ctx.load_cert_chain(path)
    finally:
        os.unlink(path)
    return ctx


def lockdown_call(sock, message):
    body = plistlib.dumps({'Label': LABEL, **message})
    sock.sendall(struct.pack('>I', len(body)) + body)
    size = struct.unpack('>I', recv_exact(sock, 4))[0]
    return plistlib.loads(recv_exact(sock, size))


class AFC:
    def __init__(self, sock):
        self.sock = sock
        self.number = 0

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _send(self, op, payload=b''):
        self.number += 1
        total = 40 + len(payload)
        self.sock.sendall(struct.pack('<8sQQQQ', AFC_MAGIC, total, total, self.number, op) + payload)

    def _recv(self, path=''):
        magic, total, _, _, op = struct.unpack('<8sQQQQ', recv_exact(self.sock, 40))
        if magic != AFC_MAGIC:
            raise IPhoneError('bad_response')
        payload = recv_exact(self.sock, total - 40) if total > 40 else b''
        if op == AFC_STATUS:
            status = struct.unpack('<Q', payload[:8])[0]
            if status:
                raise AFCError(status, path)
        return payload

    def listdir(self, path):
        self._send(AFC_READ_DIR, path.encode() + b'\0')
        names = self._recv(path).split(b'\0')
        return [n.decode('utf-8', 'replace') for n in names if n not in (b'', b'.', b'..')]

    @staticmethod
    def _parse_info(payload):
        parts = payload.split(b'\0')
        return {parts[i].decode(): parts[i + 1].decode() for i in range(0, len(parts) - 1, 2)}

    def info(self, path):
        self._send(AFC_GET_FILE_INFO, path.encode() + b'\0')
        return self._parse_info(self._recv(path))

    def info_many(self, paths):
        """Requests in bursts: far faster than one round trip per file."""
        result = {}
        for start in range(0, len(paths), INFO_WINDOW):
            window = paths[start:start + INFO_WINDOW]
            for path in window:
                self._send(AFC_GET_FILE_INFO, path.encode() + b'\0')
            for path in window:
                try:
                    result[path] = self._parse_info(self._recv(path))
                except AFCError:
                    result[path] = None
        return result

    def exists(self, path):
        try:
            self.info(path)
            return True
        except AFCError:
            return False

    def stream(self, path, limit=None, offset=0):
        self._send(AFC_FILE_OPEN, struct.pack('<Q', AFC_MODE_READ) + path.encode() + b'\0')
        handle = struct.unpack('<Q', self._recv(path)[:8])[0]
        sent = 0
        try:
            if offset:
                self._send(AFC_FILE_SEEK, struct.pack('<QQq', handle, 0, offset))
                self._recv(path)
            while limit is None or sent < limit:
                want = CHUNK if limit is None else min(CHUNK, limit - sent)
                self._send(AFC_FILE_READ, struct.pack('<QQ', handle, want))
                data = self._recv(path)
                if not data:
                    break
                sent += len(data)
                yield data
                if len(data) < want:
                    break
        finally:
            self._send(AFC_FILE_CLOSE, struct.pack('<Q', handle))
            self._recv(path)

    def read(self, path, limit=None):
        return b''.join(self.stream(path, limit))


class Phone:
    """A connected iPhone with a pool of parallel AFC connections."""

    def __init__(self, pool_size=6):
        devices = list_devices()
        if not devices:
            raise IPhoneError('no_device')
        self.device = devices[0]
        self.udid = self.device['udid']
        self.pair = read_pair_record(self.udid)
        self.tls = tls_context(self.pair)
        self.pool = queue.LifoQueue()
        self.pool_size = pool_size
        self.created = 0
        self.lock = threading.Lock()

    def _lockdown(self):
        sock = tunnel(self.device['id'], LOCKDOWN_PORT)
        lockdown_call(sock, {'Request': 'QueryType'})
        session = lockdown_call(sock, {
            'Request': 'StartSession',
            'HostID': self.pair['HostID'],
            'SystemBUID': self.pair['SystemBUID'],
        })
        if 'SessionID' not in session:
            sock.close()
            raise IPhoneError('session_refused', reason=session.get('Error', ''))
        if session.get('EnableSessionSSL'):
            sock = self.tls.wrap_socket(sock)
        return sock

    def describe(self):
        sock = self._lockdown()
        try:
            value = lambda key: lockdown_call(sock, {'Request': 'GetValue', 'Key': key}).get('Value', '')
            return {'name': value('DeviceName'), 'model': value('ProductType'), 'ios': value('ProductVersion')}
        finally:
            sock.close()

    def new_afc(self):
        sock = self._lockdown()
        try:
            service = lockdown_call(sock, {'Request': 'StartService', 'Service': 'com.apple.afc'})
        finally:
            sock.close()
        if 'Port' not in service:
            raise IPhoneError('afc_refused', reason=service.get('Error', ''))
        afc_sock = tunnel(self.device['id'], service['Port'])
        if service.get('EnableServiceSSL'):
            afc_sock = self.tls.wrap_socket(afc_sock)
        return AFC(afc_sock)

    def _acquire(self):
        try:
            return self.pool.get_nowait()
        except queue.Empty:
            pass
        with self.lock:
            create = self.created < self.pool_size
            if create:
                self.created += 1
        if create:
            try:
                return self.new_afc()
            except BaseException:
                with self.lock:
                    self.created -= 1
                raise
        try:
            return self.pool.get(timeout=20)
        except queue.Empty as exc:
            raise IPhoneError('busy') from exc

    @contextlib.contextmanager
    def session(self):
        """Lend a connection. It only goes back to the pool if the protocol stayed in sync."""
        afc = self._acquire()
        try:
            yield afc
        except AFCError:
            self.pool.put(afc)
            raise
        except BaseException:
            afc.close()
            with self.lock:
                self.created -= 1
            raise
        else:
            self.pool.put(afc)

    def close(self):
        while True:
            try:
                self.pool.get_nowait().close()
            except queue.Empty:
                return
