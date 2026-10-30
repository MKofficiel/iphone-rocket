"""iPhone Rocket: a fast Windows explorer for the photos and videos on an iPhone.

Local server on 127.0.0.1 only. Nothing is sent to the Internet. Direct access
(iphone.py) only reads; the single way to change the iPhone is a confirmed
deletion, which goes through Windows' official device layer.
"""
import argparse
import contextlib
import ctypes
import datetime
import hashlib
import http.server
import json
import os
import queue
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

import iphone
from iphone import IPhoneError

VERSION = '0.1.0'
PORT = 8847
ROOT = os.path.dirname(os.path.abspath(__file__))
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
WPD_MAP_SOURCE = os.path.join(ROOT, 'wpd-map.cs')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

DB_FILES = ('Photos.sqlite', 'Photos.sqlite-wal', 'Photos.sqlite-shm')
MAC_EPOCH = 978307200
RESCAN_SECONDS = 10
META_MAX_AGE = 6 * 3600
IDLE_EXIT_SECONDS = 300
FREE_SPACE_MARGIN = 200 * 1024 * 1024
MAX_BODY = 2 * 1024 * 1024

DELETE_WORDS = {'DELETE', 'SUPPRIMER'}
DELETED_FOLDERS = {'en': 'Deleted', 'fr': 'Supprimés'}
MAX_DELETE = 100
CONTENT_CHECK_LIMIT = 50 * 1024 * 1024
DATE_TOLERANCE_MS = 36 * 3600 * 1000
HELPER_ERRORS = {3: 'windows_no_device', 10: 'object_missing', 11: 'content_mismatch', 12: 'windows_refused'}

PHOTO_EXT = {'JPG', 'JPEG', 'PNG', 'HEIC', 'HEIF', 'WEBP', 'GIF', 'TIF', 'TIFF', 'DNG', 'BMP'}
VIDEO_EXT = {'MOV', 'MP4', 'M4V', '3GP', 'AVI', 'MKV'}
MIME = {
    'JPG': 'image/jpeg', 'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp',
    'GIF': 'image/gif', 'BMP': 'image/bmp', 'MOV': 'video/mp4', 'MP4': 'video/mp4', 'M4V': 'video/mp4',
}
WINDOWS_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{n}' for n in range(1, 10)), *(f'LPT{n}' for n in range(1, 10))}


def data_dir():
    override = os.environ.get('IPHONE_ROCKET_DATA')
    if override:
        return override
    return os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'iPhoneRocket')


def desktop_dir():
    """The real Desktop folder: it is often redirected into OneDrive."""
    try:
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buffer) == 0:
            return buffer.value
    except (AttributeError, OSError):
        pass
    return os.path.join(os.path.expanduser('~'), 'Desktop')


DATA_DIR = data_dir()
LAST_DEVICE = os.path.join(DATA_DIR, 'last-device')
EXPORT = os.environ.get('IPHONE_ROCKET_EXPORT') or os.path.join(desktop_dir(), 'iPhone Export')


def extension(path):
    return path.rsplit('.', 1)[-1].upper() if '.' in path else ''


def kind_of(path):
    ext = extension(path)
    return 'p' if ext in PHOTO_EXT else 'v' if ext in VIDEO_EXT else 'x'


def safe_name(name):
    """Names come from the phone and become paths on the PC: never trust them."""
    name = name.replace('\\', '/').rsplit('/', 1)[-1]
    name = ''.join('_' if c in '<>:"|?*' or ord(c) < 32 else c for c in name).strip(' .')
    if not name or name.split('.')[0].upper() in WINDOWS_RESERVED:
        return f'file_{name}'
    return name


def describe_error(exc):
    if isinstance(exc, IPhoneError):
        return {'code': exc.code, 'params': exc.params}
    return {'code': 'io_error', 'params': {'detail': str(exc)}}


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, separators=(',', ':'))
    os.replace(temp, path)


def read_assets(path):
    """Read the Photos database: original name, capture date, duration, hidden, trashed."""
    db = sqlite3.connect(path)
    try:
        tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
        table = 'ZASSET' if 'ZASSET' in tables else 'ZGENERICASSET'
        rows = db.execute(
            f"""select a.ZDIRECTORY, a.ZFILENAME, b.ZORIGINALFILENAME, a.ZDATECREATED,
                       a.ZDURATION, a.ZHIDDEN, a.ZTRASHEDSTATE
                from {table} a left join ZADDITIONALASSETATTRIBUTES b on b.ZASSET = a.Z_PK"""
        ).fetchall()
    finally:
        db.close()
    assets = {}
    for directory, name, original, created, duration, hidden, trashed in rows:
        if not directory or not name:
            continue
        date = int((created + MAC_EPOCH) * 1000) if created else 0
        assets[f'/{directory}/{name}'] = [original or '', date, round(duration or 0, 1), int(hidden or 0), int(trashed or 0)]
    return assets


class Library:
    demo = False

    def __init__(self):
        self.lock = threading.RLock()
        self.key = None
        self.folder = None
        self.files = {}  # path -> [size, date_ms]
        self.meta = {}  # {'time': seconds, 'assets': {path: [...]}}
        self.items = {}
        self.payload = b'[]'
        self.rev = 0
        self.phone = None
        self.state = 'starting'
        self.error = None
        self.device_name = ''
        self.model = ''
        self.ios = ''
        self.progress = None
        self.enriching = False
        self.last_scan = 0
        self.scan_ms = 0
        self.load_last_device()

    def load_last_device(self):
        with contextlib.suppress(OSError), open(LAST_DEVICE, encoding='ascii') as handle:
            key = handle.read().strip()
            if re.fullmatch('[0-9a-f]{16}', key):
                self.open_store(key)

    def open_store(self, key):
        """Each iPhone has its own cache: two phones share paths like /DCIM/100APPLE/IMG_0001.JPG."""
        folder = os.path.join(DATA_DIR, 'devices', key)
        os.makedirs(os.path.join(folder, 'thumbs'), exist_ok=True)
        # Leftover of an interrupted analysis: almost a gigabyte of wasted disk space.
        shutil.rmtree(os.path.join(folder, 'db'), ignore_errors=True)
        with self.lock:
            self.key, self.folder = key, folder
            self.files = load_json(os.path.join(folder, 'index.json'), {})
            self.meta = load_json(os.path.join(folder, 'meta.json'), {})
        self.rebuild()

    def save(self):
        with self.lock:
            save_json(os.path.join(self.folder, 'index.json'), self.files)
            save_json(os.path.join(self.folder, 'meta.json'), self.meta)

    def thumb_path(self, path):
        digest = hashlib.sha1(path.encode()).hexdigest()
        return os.path.join(self.folder, 'thumbs', digest[:2], digest[2:] + '.jpg')

    def rebuild(self):
        with self.lock:
            assets = self.meta.get('assets', {})
            sidecars = {p[:-4].upper() for p in self.files if extension(p) == 'AAE'}
            stills = {p.rsplit('.', 1)[0].upper() for p in self.files if kind_of(p) == 'p'}
            videos = {p.rsplit('.', 1)[0].upper() for p in self.files if kind_of(p) == 'v'}
            items = {}
            for path, (size, date) in self.files.items():
                if extension(path) == 'AAE':
                    continue
                stem = path.rsplit('.', 1)[0].upper()
                asset = assets.get(path)
                item = items[path] = {
                    'p': path,
                    'n': path.rsplit('/', 1)[1],
                    'o': asset[0] if asset else '',
                    's': size,
                    't': (asset[1] if asset and asset[1] else date),
                    'k': kind_of(path),
                    'd': asset[2] if asset else 0,
                    'e': int(stem in sidecars),
                    'h': asset[3] if asset else 0,
                    'r': asset[4] if asset else 0,
                    'c': 0,
                    # Video half of a Live Photo: not a file of its own in Photos.
                    'l': int(kind_of(path) == 'v' and not asset and stem in stills),
                }
                # Only the simple case is validated: one file, no edit, no Live Photo.
                item['x'] = int(
                    path.startswith('/DCIM/') and item['k'] != 'x'
                    and not (item['e'] or item['h'] or item['r'] or item['l'])
                    and not (stem in stills and stem in videos)
                )
            for path, asset in assets.items():
                if path not in self.files:
                    items[path] = {
                        'p': path, 'n': path.rsplit('/', 1)[1], 'o': asset[0], 's': 0, 't': asset[1],
                        'k': kind_of(path), 'd': asset[2], 'e': 0, 'h': asset[3], 'r': asset[4], 'c': 1, 'l': 0, 'x': 0,
                    }
            ordered = sorted(items.values(), key=lambda item: item['t'], reverse=True)
            self.items = items
            self.payload = json.dumps(ordered, separators=(',', ':')).encode()
            self.rev += 1

    def scan(self):
        started = time.perf_counter()
        with self.phone.session() as afc:
            found = set()
            for folder in afc.listdir('/DCIM'):
                if folder.startswith('.'):
                    continue
                base = f'/DCIM/{folder}'
                try:
                    found.update(f'{base}/{name}' for name in afc.listdir(base))
                except iphone.AFCError:
                    continue
            new = [path for path in found if path not in self.files]
            infos = afc.info_many(new) if new else {}
        changed = False
        with self.lock:
            for path, info in infos.items():
                if info and info.get('st_ifmt') == 'S_IFREG':
                    self.files[path] = [int(info['st_size']), int(info.get('st_birthtime', 0)) // 1000000]
                    changed = True
            for path in [p for p in self.files if p.startswith('/DCIM/') and p not in found]:
                del self.files[path]
                changed = True
                # Deleted in Photos or moved to iCloud: only a fresh analysis can tell which.
                self.meta['time'] = 0
        self.last_scan = time.time()
        self.scan_ms = int((time.perf_counter() - started) * 1000)
        if changed:
            self.rebuild()
            self.save()

    def forget(self, path):
        """Otherwise the deleted file would stay in the database and pass for an iCloud item."""
        with self.lock:
            self.files.pop(path, None)
            self.meta.get('assets', {}).pop(path, None)
            self.save()
        with contextlib.suppress(OSError):
            os.unlink(self.thumb_path(path))
        self.rebuild()

    def needs_enrichment(self):
        # The database weighs about a gigabyte: a new file does not justify copying it,
        # the scan already shows it with its file date.
        if self.enriching or self.state != 'ok':
            return False
        return time.time() - self.meta.get('time', 0) > META_MAX_AGE

    def enrich(self):
        phone = self.phone
        if phone is None:
            return
        self.enriching = True
        self.progress = 0
        db_dir = os.path.join(self.folder, 'db')
        try:
            os.makedirs(db_dir, exist_ok=True)
            with phone.session() as afc:
                total = int(afc.info(f'/PhotoData/{DB_FILES[0]}')['st_size'])
                if shutil.disk_usage(db_dir).free < total * 1.5:
                    raise IPhoneError('disk_full')
                done = 0
                for name in DB_FILES:
                    try:
                        with open(os.path.join(db_dir, name), 'wb') as out, \
                                contextlib.closing(afc.stream(f'/PhotoData/{name}')) as chunks:
                            for chunk in chunks:
                                out.write(chunk)
                                done += len(chunk)
                                self.progress = min(done / total, 0.99)
                    except iphone.AFCError:
                        if name == DB_FILES[0]:
                            raise
                assets = read_assets(os.path.join(db_dir, DB_FILES[0]))
                outside = [p for p in assets if not p.startswith('/DCIM/')]
                extra = afc.info_many(outside) if outside else {}
            with self.lock:
                self.meta = {'time': time.time(), 'assets': assets}
                for path, info in extra.items():
                    if info:
                        self.files[path] = [int(info['st_size']), assets[path][1]]
                self.save()
            self.rebuild()
        except (IPhoneError, OSError, sqlite3.Error, KeyError) as exc:
            # Without the database we lose original names, not access to the files.
            with self.lock:
                self.meta = {**self.meta, 'time': time.time(), 'failed': describe_error(exc)['code']}
            print('Library analysis failed:', repr(exc), file=sys.stderr)
        finally:
            shutil.rmtree(db_dir, ignore_errors=True)
            self.progress = None
            self.enriching = False

    def connect(self):
        self.phone = iphone.Phone()
        info = self.phone.describe()
        self.device_name, self.model, self.ios = info['name'], info['model'], info['ios']
        key = hashlib.sha256(self.phone.udid.encode()).hexdigest()[:16]
        if key != self.key:
            self.open_store(key)
        with open(LAST_DEVICE, 'w', encoding='ascii') as handle:
            handle.write(key)
        self.last_scan = 0

    def watch(self):
        while True:
            try:
                devices = iphone.list_devices()
                if not devices:
                    self.disconnect('nodevice')
                else:
                    if self.phone is None or self.phone.device['id'] != devices[0]['id']:
                        self.disconnect('starting')
                        self.connect()
                    self.state = 'ok'
                    self.error = None
                    if time.time() - self.last_scan >= RESCAN_SECONDS:
                        self.scan()
                    if self.needs_enrichment():
                        threading.Thread(target=self.enrich, daemon=True).start()
            except iphone.ServiceStopped:
                self.disconnect('service')
            except Exception as exc:  # this loop must never die: the iPhone can come back
                print('Device watch error:', repr(exc), file=sys.stderr)
                self.disconnect('error', describe_error(exc))
            time.sleep(2)

    def disconnect(self, state, error=None):
        phone, self.phone = self.phone, None
        if phone:
            phone.close()
        self.state = state
        self.error = error

    def status(self):
        return {
            'version': VERSION,
            'demo': self.demo,
            'state': self.state,
            'error': self.error,
            'device': self.device_name,
            'model': self.model,
            'ios': self.ios,
            'rev': self.rev,
            'ready': 'assets' in self.meta or 'failed' in self.meta,
            'progress': self.progress,
            'scanMs': self.scan_ms,
        }


def demo_image(path):
    """A small invented landscape, so that screenshots never show anyone's real photos."""
    rng = random.Random(path)
    hue = rng.randrange(360)
    sky = f'hsl({hue},70%,{rng.randrange(55, 78)}%)'
    glow = f'hsl({(hue + 40) % 360},85%,80%)'
    far = f'hsl({(hue + 170) % 360},30%,42%)'
    near = f'hsl({(hue + 190) % 360},35%,24%)'
    x, y = rng.randrange(50, 200), rng.randrange(40, 130)
    a, b, c = (rng.randrange(120, 190) for _ in range(3))
    d, e, f = (rng.randrange(170, 225) for _ in range(3))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        f'<defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{sky}"/>'
        f'<stop offset="1" stop-color="{glow}"/></linearGradient></defs>'
        '<rect width="256" height="256" fill="url(#s)"/>'
        f'<circle cx="{x}" cy="{y}" r="24" fill="#fff" opacity=".85"/>'
        f'<path d="M0 {a}Q64 {b} 128 {c}T256 {a}V256H0Z" fill="{far}"/>'
        f'<path d="M0 {d}Q80 {e} 150 {f}T256 {d}V256H0Z" fill="{near}"/></svg>'
    ).encode()


class DemoLibrary(Library):
    """An invented library: for screenshots and for working on the interface without an iPhone."""
    demo = True

    def load_last_device(self):
        rng = random.Random(7)
        now = int(time.time() * 1000)
        self.folder = None
        assets = {}
        for index in range(2400):
            folder = f'/DCIM/{100 + index // 1000}APPLE'
            date = now - index * rng.randrange(6, 14) * 3600000
            ext = rng.choices(['HEIC', 'JPG', 'PNG', 'MOV', 'MP4'], [55, 10, 10, 15, 10])[0]
            path = f'{folder}/IMG_{9999 - index:04d}.{ext}'
            video = ext in ('MOV', 'MP4')
            original = '' if rng.random() < .6 else f'{rng.getrandbits(32):08x}.{ext.lower()}'
            duration = round(rng.uniform(2, 240), 1) if video else 0
            hidden, trashed = int(rng.random() < .01), int(rng.random() < .005)
            assets[path] = [original, date, duration, hidden, trashed]
            if rng.random() < .8:
                self.files[path] = [rng.randrange(200, 90000) * 1024 * (40 if video else 1), date]
                if rng.random() < .07:
                    self.files[path.rsplit('.', 1)[0] + '.AAE'] = [900, date]
        self.meta = {'time': time.time(), 'assets': assets}
        self.device_name, self.model, self.ios = 'iPhone', 'demo', ''
        self.state = 'ok'
        self.rebuild()

    def watch(self):
        pass


def edited_source(afc, item):
    """The render made in Photos: /PhotoData/Mutations/.../Adjustments/FullSizeRender.xxx"""
    folder = '/PhotoData/Mutations/' + item['p'][1:].rsplit('.', 1)[0] + '/Adjustments'
    try:
        for name in afc.listdir(folder):
            if name.startswith('FullSizeRender'):
                return f'{folder}/{name}'
    except iphone.AFCError:
        pass
    return None


class Copier:
    def __init__(self, library):
        self.library = library
        self.tasks = queue.Queue()
        self.job = self.empty_job()

    @staticmethod
    def empty_job():
        return {'active': False, 'files': 0, 'doneFiles': 0, 'bytes': 0, 'doneBytes': 0,
                'current': '', 'errors': [], 'skipped': 0, 'finished': 0, 'speed': 0, 'lastFile': ''}

    def add(self, paths, mode):
        if self.library.demo:
            raise IPhoneError('demo_mode')
        items = [self.library.items[p] for p in paths if p in self.library.items and not self.library.items[p]['c']]
        if not items:
            return 0
        needed = sum(item['s'] for item in items)
        os.makedirs(EXPORT, exist_ok=True)
        if shutil.disk_usage(EXPORT).free < needed + FREE_SPACE_MARGIN:
            raise IPhoneError('disk_full')
        if not self.job['active']:
            self.job = self.empty_job()
        self.job.update(active=True, files=self.job['files'] + len(items), bytes=self.job['bytes'] + needed)
        for item in items:
            self.tasks.put((item, mode))
        return len(items)

    def run(self):
        while True:
            item, mode = self.tasks.get()
            self.job['current'] = item['n']
            try:
                self.copy_one(item, mode)
            except (IPhoneError, OSError) as exc:
                self.job['errors'].append({'file': item['n'], **describe_error(exc)})
            self.job['doneFiles'] += 1
            if self.tasks.empty():
                self.job.update(active=False, current='', finished=time.time())

    def copy_one(self, item, mode):
        phone = self.library.phone
        if phone is None:
            raise IPhoneError('not_connected')
        with phone.session() as afc:
            source, name = item['p'], safe_name(item['n'])
            if item['e'] and mode != 'original':
                edited = edited_source(afc, item)
                if edited:
                    source = edited
                    stem = name.rsplit('.', 1)[0]
                    stem = f'IMG_E{stem[4:]}' if stem.startswith('IMG_') else f'{stem}_edited'
                    name = f'{stem}.{extension(edited)}'
            size = int(afc.info(source)['st_size'])
            target = self.target_for(name, size)
            if target is None:
                self.job['skipped'] += 1
                self.job['doneBytes'] += item['s']
                return
            temp = target + '.part'
            started = time.perf_counter()
            written = 0
            try:
                with open(temp, 'wb') as out, contextlib.closing(afc.stream(source)) as chunks:
                    for chunk in chunks:
                        out.write(chunk)
                        written += len(chunk)
                        self.job['doneBytes'] += len(chunk)
                        self.job['speed'] = written / max(time.perf_counter() - started, 0.001)
                if os.path.getsize(temp) != size:
                    raise IPhoneError('incomplete_copy')
                os.replace(temp, target)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(temp)
                raise
        if item['t']:
            os.utime(target, (item['t'] / 1000, item['t'] / 1000))
        self.job['lastFile'] = os.path.basename(target)

    @staticmethod
    def target_for(name, size, folder=None):
        folder = folder or EXPORT
        stem, dot, ext = name.rpartition('.')
        for attempt in range(1, 1000):
            candidate = os.path.join(folder, name if attempt == 1 else f'{stem} ({attempt}){dot}{ext}')
            if not os.path.exists(candidate):
                return candidate
            if os.path.getsize(candidate) == size:
                return None
        raise IPhoneError('too_many_copies')


def helper_path():
    """The helper is compiled on the user's PC, with the compiler built into Windows."""
    target = os.path.join(DATA_DIR, 'wpd-map.exe')
    if os.path.exists(target) and os.path.getmtime(target) >= os.path.getmtime(WPD_MAP_SOURCE):
        return target
    compiler = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Microsoft.NET', 'Framework64', 'v4.0.30319', 'csc.exe')
    if not os.path.exists(compiler):
        raise IPhoneError('helper_missing')
    os.makedirs(DATA_DIR, exist_ok=True)
    done = subprocess.run(
        [compiler, '/nologo', '/optimize', '/platform:x64', f'/out:{target}', WPD_MAP_SOURCE],
        capture_output=True, timeout=120, creationflags=NO_WINDOW,
    )
    if done.returncode:
        raise IPhoneError('helper_failed')
    return target


class Deleter:
    """Deletes a file only once its identity is proven; otherwise it touches nothing."""

    def __init__(self, library, copier):
        self.library = library
        self.copier = copier
        self.job = self.empty_job()
        self.objects = {}  # Explorer folder prefix -> files as Windows sees them
        self.device = None

    @staticmethod
    def empty_job():
        return {'active': False, 'files': 0, 'doneFiles': 0, 'deleted': 0, 'freed': 0, 'errors': [],
                'current': '', 'step': '', 'finished': 0, 'dry': False, 'backup': False}

    def start(self, paths, backup, dry, lang):
        if self.library.demo:
            raise IPhoneError('demo_mode')
        if self.job['active'] or self.copier.job['active']:
            raise IPhoneError('job_running')
        if self.library.phone is None:
            raise IPhoneError('not_connected')
        items = [self.library.items[p] for p in paths if p in self.library.items and self.library.items[p]['x']]
        if not items:
            raise IPhoneError('nothing_deletable')
        if len(items) > MAX_DELETE:
            raise IPhoneError('too_many_files', max=MAX_DELETE)
        folder = None
        if backup:
            folder = os.path.join(EXPORT, DELETED_FOLDERS.get(lang, DELETED_FOLDERS['en']))
            os.makedirs(folder, exist_ok=True)
            if shutil.disk_usage(folder).free < sum(i['s'] for i in items) + FREE_SPACE_MARGIN:
                raise IPhoneError('disk_full')
        self.job = {**self.empty_job(), 'active': True, 'files': len(items), 'dry': dry, 'backup': backup}
        threading.Thread(target=self.run, args=(items, folder, dry), daemon=True).start()
        return len(items)

    def run(self, items, folder, dry):
        job = self.job
        try:
            phone = self.library.phone
            if phone.device['id'] != self.device:
                self.objects = {}
                self.device = phone.device['id']
            for item in items:
                job['current'] = item['n']
                try:
                    self.delete_one(phone, item, folder, dry)
                    self.record(item, 'dry-run' if dry else 'deleted')
                except (IPhoneError, OSError, ValueError, subprocess.SubprocessError) as exc:
                    error = describe_error(exc)
                    job['errors'].append({'file': item['n'], **error})
                    self.record(item, error['code'])
                job['doneFiles'] += 1
        except (AttributeError, OSError) as exc:
            job['errors'].append({'file': '', **describe_error(exc)})
        finally:
            job.update(active=False, current='', step='', finished=time.time())

    @staticmethod
    def record(item, outcome):
        """A local trace of every attempt: a deletion is the one action worth being able to audit."""
        line = f"{datetime.datetime.now().isoformat(timespec='seconds')}\t{item['p']}\t{outcome}\n"
        with contextlib.suppress(OSError), open(os.path.join(DATA_DIR, 'deletions.log'), 'a', encoding='utf-8') as handle:
            handle.write(line)

    def delete_one(self, phone, item, folder, dry):
        job = self.job
        path = item['p']
        with phone.session() as afc:
            size = int(afc.info(path)['st_size'])
            job['step'] = 'find'
            target = self.find_object(item, size)
            job['step'] = 'backup' if folder else 'verify'
            digest = self.save_copy(afc, path, safe_name(item['n']), size, folder)
            job['step'] = 'verify'
            identity = (target['id'], target['name'], str(size))
            self.helper('verify', *identity, digest or '-')
            if dry:
                return
            job['step'] = 'delete'
            # A separate run on purpose: the iPhone answers "busy" while the process
            # that read the file for verification is still alive.
            self.helper('delete', *identity)
            deadline = time.time() + 20
            while afc.exists(path):
                if time.time() > deadline:
                    raise IPhoneError('phone_kept_file')
                time.sleep(0.3)
        for entries in self.objects.values():
            if target in entries:
                entries.remove(target)
        self.library.forget(path)
        job['deleted'] += 1
        job['freed'] += size

    def save_copy(self, afc, path, name, size, folder):
        """Back up if requested. Returns the content hash when the file is reasonably small."""
        if not folder and size > CONTENT_CHECK_LIMIT:
            return ''
        target = self.copier.target_for(name, size, folder) if folder else None
        temp = target + '.part' if target else None
        hasher = hashlib.sha256()
        try:
            with open(temp, 'wb') if temp else contextlib.nullcontext() as out, \
                    contextlib.closing(afc.stream(path)) as chunks:
                written = 0
                for chunk in chunks:
                    hasher.update(chunk)
                    written += len(chunk)
                    if out:
                        out.write(chunk)
            if written != size:
                raise IPhoneError('read_incomplete')
            if temp:
                os.replace(temp, target)
        except BaseException:
            if temp:
                with contextlib.suppress(OSError):
                    os.unlink(temp)
            raise
        return hasher.hexdigest() if size <= CONTENT_CHECK_LIMIT else ''

    def find_object(self, item, size):
        moment = datetime.datetime.fromtimestamp(item['t'] / 1000)
        month = moment.strftime('%Y%m')
        before = (moment.replace(day=1) - datetime.timedelta(days=1)).strftime('%Y%m')
        after = (moment.replace(day=28) + datetime.timedelta(days=7)).strftime('%Y%m')
        for prefixes in ([month], [before, after]):
            found = [o for prefix in prefixes for o in self.read_map(prefix) if self.same_file(o, item, size)]
            if len(found) > 1:
                raise IPhoneError('multiple_matches')
            if found:
                return found[0]
        raise IPhoneError('not_found_windows')

    @staticmethod
    def same_file(entry, item, size):
        normal = lambda name: extension(name).replace('JPEG', 'JPG')
        return (entry['size'] == size and normal(entry['name']) == normal(item['n'])
                and abs(entry['date'] - item['t']) <= DATE_TOLERANCE_MS)

    def read_map(self, prefix):
        if prefix not in self.objects:
            done = subprocess.run([helper_path(), prefix], capture_output=True, timeout=600, creationflags=NO_WINDOW)
            if done.returncode:
                raise IPhoneError('windows_no_device')
            entries = []
            for line in done.stdout.decode('utf-8', 'replace').splitlines():
                folder, name, size, created, object_id = line.split('\t')
                if created:
                    date = int(datetime.datetime.fromisoformat(created).timestamp() * 1000)
                    entries.append({'folder': folder, 'name': name, 'size': int(size), 'date': date, 'id': object_id})
            self.objects[prefix] = entries
        return self.objects[prefix]

    @staticmethod
    def helper(*args):
        """Each run checks the object's identity again, right before acting."""
        done = subprocess.run([helper_path(), *args], capture_output=True, timeout=300, creationflags=NO_WINDOW)
        if done.returncode:
            detail = done.stdout.decode('utf-8', 'replace').strip() or str(done.returncode)
            raise IPhoneError(HELPER_ERRORS.get(done.returncode, 'windows_error'), detail=detail)


def start_service():
    ctypes.windll.shell32.ShellExecuteW(None, 'runas', 'sc.exe', 'start "Apple Mobile Device Service"', None, 0)


def parse_range(header, size):
    """Returns (start, end, partial), or None when the range cannot be served."""
    if not header.startswith('bytes='):
        return 0, size - 1, False
    first, _, last = header[6:].split(',')[0].strip().partition('-')
    try:
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        elif last:
            start, end = max(size - int(last), 0), size - 1
        else:
            return None
    except ValueError:
        return None
    if start < 0 or start >= size or start > end:
        return None
    return start, end, True


class Server(http.server.ThreadingHTTPServer):
    # On Windows, reusing the address would let two instances listen on the same port.
    allow_reuse_address = False
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'iPhoneRocket'
    disable_nagle_algorithm = True
    library = None
    copier = None
    deleter = None
    port = PORT
    last_ping = time.time()

    def log_message(self, *args):
        pass

    def allowed(self):
        """Blocks other websites from reaching this local server (DNS rebinding, CSRF)."""
        if self.headers.get('Host') not in (f'127.0.0.1:{self.port}', f'localhost:{self.port}'):
            return False
        return self.headers.get('Sec-Fetch-Site', 'same-origin') in ('same-origin', 'none')

    def reply(self, code, body=b'', ctype='application/json', headers=()):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, data, code=200):
        self.reply(code, json.dumps(data).encode())

    def reply_error(self, exc, code=400):
        self.reply_json(describe_error(exc), code)

    def do_GET(self):
        if not self.allowed():
            return self.reply(403)
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        path = query.get('p', [''])[0]
        try:
            if url.path == '/':
                with open(os.path.join(ROOT, 'index.html'), 'rb') as handle:
                    return self.reply(200, handle.read(), 'text/html; charset=utf-8', [('Cache-Control', 'no-store')])
            locale = re.fullmatch(r'/locales/([a-z]{2})\.json', url.path)
            if locale:
                with open(os.path.join(ROOT, 'locales', locale[1] + '.json'), 'rb') as handle:
                    return self.reply(200, handle.read(), headers=[('Cache-Control', 'no-store')])
            if url.path == '/api/items':
                return self.reply(200, self.library.payload)
            if url.path == '/api/status':
                Handler.last_ping = time.time()
                return self.reply_json({**self.library.status(), 'job': self.copier.job, 'del': self.deleter.job})
            if url.path == '/thumb':
                return self.send_thumb(path)
            if url.path == '/file':
                return self.send_file(path, query.get('v', [''])[0])
            return self.reply(404)
        except FileNotFoundError:
            return self.reply(404)
        except ConnectionError:
            self.close_connection = True
        except (IPhoneError, OSError) as exc:
            self.close_connection = True
            with contextlib.suppress(OSError):
                self.reply_error(exc, 503)

    def do_POST(self):
        if not self.allowed() or not self.headers.get('X-Rocket'):
            return self.reply(403)
        try:
            length = int(self.headers.get('Content-Length') or 0)
            if length > MAX_BODY:
                raise ValueError
            body = json.loads(self.rfile.read(length) or b'{}')
            paths = body.get('paths', [])
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                raise ValueError
        except (ValueError, AttributeError):
            return self.reply_error(IPhoneError('bad_request'))
        try:
            if self.path == '/api/copy':
                return self.reply_json({'queued': self.copier.add(paths, body.get('mode', 'auto'))})
            if self.path == '/api/delete':
                if body.get('confirm') not in DELETE_WORDS:
                    raise IPhoneError('confirmation_missing')
                queued = self.deleter.start(paths, bool(body.get('backup', True)), bool(body.get('dry')), body.get('lang'))
                return self.reply_json({'queued': queued})
            if self.path == '/api/open-export':
                os.makedirs(EXPORT, exist_ok=True)
                os.startfile(EXPORT)
                return self.reply_json({})
            if self.path == '/api/start-service':
                start_service()
                return self.reply_json({})
            return self.reply(404)
        except (IPhoneError, OSError) as exc:
            self.reply_error(exc)

    def send_thumb(self, path):
        if path not in self.library.items:
            return self.reply(404)
        headers = [('Cache-Control', 'public, max-age=31536000, immutable')]
        if self.library.demo:
            return self.reply(200, demo_image(path), 'image/svg+xml', headers)
        cached = self.library.thumb_path(path)
        with contextlib.suppress(OSError):
            with open(cached, 'rb') as handle:
                return self.reply(200, handle.read(), 'image/jpeg', headers)
        phone = self.library.phone
        if phone is None:
            return self.reply(503)
        try:
            with phone.session() as afc:
                data = afc.read(f'/PhotoData/Thumbnails/V2{path}/5005.JPG')
        except iphone.AFCError:
            return self.reply(404)
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        temp = f'{cached}.{threading.get_ident()}'
        with open(temp, 'wb') as handle:
            handle.write(data)
        os.replace(temp, cached)
        self.reply(200, data, 'image/jpeg', headers)

    def send_file(self, path, version):
        item = self.library.items.get(path)
        if self.library.demo and item and item['k'] == 'p':
            return self.reply(200, demo_image(path), 'image/svg+xml')
        phone = self.library.phone
        if item is None or item['c'] or phone is None:
            return self.reply(404)
        with phone.session() as afc:
            source = path
            if version == 'edited' and item['e']:
                source = edited_source(afc, item) or path
            size = int(afc.info(source)['st_size'])
            wanted = parse_range(self.headers.get('Range', ''), size)
            if wanted is None:
                return self.reply(416, headers=[('Content-Range', f'bytes */{size}')])
            start, end, partial = wanted
            self.send_response(206 if partial else 200)
            self.send_header('Content-Type', MIME.get(extension(source), 'application/octet-stream'))
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            if partial:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            with contextlib.closing(afc.stream(source, end - start + 1, start)) as chunks:
                for chunk in chunks:
                    self.wfile.write(chunk)


def open_window(url):
    if os.path.exists(EDGE):
        subprocess.Popen([EDGE, f'--app={url}', '--window-size=1400,900'])
    else:
        webbrowser.open(url)


def exit_when_idle(copier, deleter):
    while True:
        time.sleep(30)
        busy = copier.job['active'] or deleter.job['active']
        if time.time() - Handler.last_ping > IDLE_EXIT_SECONDS and not busy:
            os._exit(0)


def main():
    parser = argparse.ArgumentParser(description='Fast explorer for iPhone photos and videos on Windows.')
    parser.add_argument('--demo', action='store_true', help='show an invented library, no iPhone needed')
    parser.add_argument('--no-open', action='store_true', help='do not open a window')
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    if sys.stderr is None:  # started without a console by pythonw
        sys.stdout = sys.stderr = open(os.path.join(DATA_DIR, 'rocket.log'), 'a', encoding='utf-8', buffering=1)
    url = f'http://127.0.0.1:{args.port}/'
    try:
        server = Server(('127.0.0.1', args.port), Handler)
    except OSError:
        if not args.no_open:
            open_window(url)  # already running: just open the window again
        return
    library = DemoLibrary() if args.demo else Library()
    copier = Copier(library)
    deleter = Deleter(library, copier)
    Handler.library, Handler.copier, Handler.deleter, Handler.port = library, copier, deleter, args.port
    threading.Thread(target=library.watch, daemon=True).start()
    threading.Thread(target=copier.run, daemon=True).start()
    threading.Thread(target=exit_when_idle, args=(copier, deleter), daemon=True).start()
    if not args.no_open:
        open_window(url)
    server.serve_forever()


if __name__ == '__main__':
    main()
