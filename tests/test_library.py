import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from support import new_library, rocket

DAY = 86400000


class NameTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(rocket.kind_of('/a/IMG_1.HEIC'), 'p')
        self.assertEqual(rocket.kind_of('/a/IMG_1.mov'), 'v')
        self.assertEqual(rocket.kind_of('/a/IMG_1.AAE'), 'x')
        self.assertEqual(rocket.kind_of('/a/noext'), 'x')

    def test_safe_name_blocks_path_traversal(self):
        """A name from the phone must never escape the export folder."""
        self.assertEqual(rocket.safe_name('..\\..\\Startup\\evil.exe'), 'evil.exe')
        self.assertEqual(rocket.safe_name('../../evil.exe'), 'evil.exe')
        self.assertEqual(rocket.safe_name('C:\\Windows\\evil.dll'), 'evil.dll')

    def test_safe_name_cleans_windows_characters(self):
        self.assertEqual(rocket.safe_name('a:b*c?.jpg'), 'a_b_c_.jpg')
        self.assertEqual(rocket.safe_name('name.jpg. '), 'name.jpg')
        self.assertEqual(rocket.safe_name('a\x01b.jpg'), 'a_b.jpg')

    def test_safe_name_refuses_reserved_names(self):
        self.assertEqual(rocket.safe_name('CON.jpg'), 'file_CON.jpg')
        self.assertEqual(rocket.safe_name('nul'), 'file_nul')
        self.assertEqual(rocket.safe_name('...'), 'file_')

    def test_target_for_avoids_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            first = rocket.Copier.target_for('a.jpg', 3, folder)
            self.assertEqual(os.path.basename(first), 'a.jpg')
            with open(first, 'wb') as handle:
                handle.write(b'abc')
            self.assertIsNone(rocket.Copier.target_for('a.jpg', 3, folder))
            self.assertEqual(os.path.basename(rocket.Copier.target_for('a.jpg', 9, folder)), 'a (2).jpg')


class RangeTests(unittest.TestCase):
    def test_no_header(self):
        self.assertEqual(rocket.parse_range('', 100), (0, 99, False))

    def test_ranges(self):
        self.assertEqual(rocket.parse_range('bytes=0-9', 100), (0, 9, True))
        self.assertEqual(rocket.parse_range('bytes=90-', 100), (90, 99, True))
        self.assertEqual(rocket.parse_range('bytes=-10', 100), (90, 99, True))
        self.assertEqual(rocket.parse_range('bytes=90-500', 100), (90, 99, True))

    def test_bad_ranges(self):
        for header in ('bytes=500-', 'bytes=abc-', 'bytes=-', 'bytes=9-1', 'bytes=-x'):
            self.assertIsNone(rocket.parse_range(header, 100), header)


class RebuildTests(unittest.TestCase):
    def build(self, files, assets=None):
        library = new_library()
        library.files = files
        library.meta = {'time': 1, 'assets': assets or {}}
        library.rebuild()
        return library.items

    def test_simple_file_is_deletable(self):
        items = self.build({'/DCIM/100APPLE/IMG_1.PNG': [10, 5]})
        self.assertEqual(items['/DCIM/100APPLE/IMG_1.PNG']['x'], 1)

    def test_sidecar_marks_edited_and_blocks_deletion(self):
        items = self.build({'/DCIM/100APPLE/IMG_1.MOV': [10, 5], '/DCIM/100APPLE/IMG_1.AAE': [1, 5]})
        self.assertNotIn('/DCIM/100APPLE/IMG_1.AAE', items)
        item = items['/DCIM/100APPLE/IMG_1.MOV']
        self.assertEqual((item['e'], item['x']), (1, 0))

    def test_live_photo_is_never_deletable(self):
        still, motion = '/DCIM/100APPLE/IMG_1.HEIC', '/DCIM/100APPLE/IMG_1.MOV'
        items = self.build({still: [10, 5], motion: [20, 5]}, {still: ['', 5, 0, 0, 0]})
        self.assertEqual(items[motion]['l'], 1)
        self.assertEqual(items[still]['x'], 0)
        self.assertEqual(items[motion]['x'], 0)

    def test_hidden_and_trashed_are_not_deletable(self):
        hidden, trashed = '/DCIM/100APPLE/IMG_1.JPG', '/DCIM/100APPLE/IMG_2.JPG'
        items = self.build({hidden: [1, 1], trashed: [1, 1]}, {hidden: ['', 1, 0, 1, 0], trashed: ['', 1, 0, 0, 1]})
        self.assertEqual(items[hidden]['x'], 0)
        self.assertEqual(items[trashed]['x'], 0)

    def test_database_row_without_file_is_icloud_only(self):
        path = '/DCIM/100APPLE/IMG_1.JPG'
        item = self.build({}, {path: ['orig.jpg', 9, 0, 0, 0]})[path]
        self.assertEqual((item['c'], item['x'], item['s'], item['o']), (1, 0, 0, 'orig.jpg'))

    def test_outside_dcim_is_not_deletable(self):
        path = '/PhotoData/CPLAssets/group1/A.JPG'
        self.assertEqual(self.build({path: [1, 1]})[path]['x'], 0)

    def test_capture_date_wins_over_file_date(self):
        path = '/DCIM/100APPLE/IMG_1.JPG'
        self.assertEqual(self.build({path: [1, 5]}, {path: ['', 9, 0, 0, 0]})[path]['t'], 9)

    def test_forget_does_not_turn_into_icloud_ghost(self):
        path = '/DCIM/100APPLE/IMG_1.JPG'
        library = new_library()
        library.files = {path: [1, 1]}
        library.meta = {'time': 1, 'assets': {path: ['', 1, 0, 0, 0]}}
        library.rebuild()
        library.forget(path)
        self.assertNotIn(path, library.items)

    def test_file_gone_from_the_iphone_triggers_a_fresh_analysis(self):
        """It may be deleted or moved to iCloud: calling it 'iCloud only' could be wrong."""
        path = '/DCIM/100APPLE/IMG_1.JPG'
        library = new_library('eeeeeeeeeeeeeeee')
        library.files = {path: [1, 1]}
        library.meta = {'time': rocket.time.time(), 'assets': {path: ['', 1, 0, 0, 0]}}
        library.state = 'ok'
        afc = mock.MagicMock()
        afc.listdir.side_effect = lambda folder: ['100APPLE'] if folder == '/DCIM' else []
        library.phone = mock.MagicMock()
        library.phone.session.return_value.__enter__.return_value = afc
        library.scan()
        self.assertNotIn(path, library.files)
        self.assertTrue(library.needs_enrichment())

    def test_each_iphone_has_its_own_cache(self):
        """Two phones share paths like /DCIM/100APPLE/IMG_0001.JPG."""
        path = '/DCIM/100APPLE/IMG_1.JPG'
        first = new_library('aaaaaaaaaaaaaaaa')
        first.files = {path: [1, 1]}
        first.save()
        second = new_library('bbbbbbbbbbbbbbbb')
        self.assertEqual(second.files, {})
        self.assertNotEqual(first.thumb_path(path), second.thumb_path(path))
        self.assertEqual(new_library('aaaaaaaaaaaaaaaa').files, {path: [1, 1]})

    def test_interrupted_analysis_leaves_nothing_behind(self):
        library = new_library('cccccccccccccccc')
        leftover = os.path.join(library.folder, 'db')
        os.makedirs(leftover)
        new_library('cccccccccccccccc')
        self.assertFalse(os.path.exists(leftover))

    def test_new_file_does_not_retrigger_the_big_analysis(self):
        library = new_library()
        library.state = 'ok'
        library.files = {'/DCIM/100APPLE/NEW.JPG': [1, 1]}
        library.meta = {'time': rocket.time.time(), 'assets': {}}
        self.assertFalse(library.needs_enrichment())


class PhotosDatabaseTests(unittest.TestCase):
    def test_reads_assets_and_joins_original_names(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'Photos.sqlite')
            db = sqlite3.connect(path)
            db.executescript("""
                create table ZASSET (Z_PK integer, ZDIRECTORY text, ZFILENAME text, ZDATECREATED real,
                                     ZDURATION real, ZHIDDEN integer, ZTRASHEDSTATE integer);
                create table ZADDITIONALASSETATTRIBUTES (ZASSET integer, ZORIGINALFILENAME text);
                insert into ZASSET values (1, 'DCIM/100APPLE', 'IMG_1.MP4', 100, 27.84, 0, 1);
                insert into ZASSET values (2, null, 'IMG_2.MP4', 100, 0, 0, 0);
                insert into ZADDITIONALASSETATTRIBUTES values (1, 'orig.mp4');
            """)
            db.close()
            self.assertEqual(rocket.read_assets(path), {
                '/DCIM/100APPLE/IMG_1.MP4': ['orig.mp4', (100 + rocket.MAC_EPOCH) * 1000, 27.8, 0, 1],
            })


if __name__ == '__main__':
    unittest.main()
