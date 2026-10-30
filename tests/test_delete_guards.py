"""Deletion is the one risky action: these tests prove the guards REFUSE."""
import hashlib
import unittest
from unittest import mock

from support import iphone, new_library, rocket

ITEM = {'p': '/DCIM/X/IMG_1.JPG', 'n': 'IMG_1.JPG', 't': 1700000000000}
GOOD = {'folder': '202311_a', 'name': 'ABCD1234.JPEG', 'size': 500, 'date': 1700000000000 + 400000}


def prepared(entries, around=()):
    deleter = rocket.Deleter(None, None)
    deleter.objects = {'202311': entries, '202310': list(around), '202312': []}
    return deleter


class IdentityTests(unittest.TestCase):
    def assert_refused(self, entries, code):
        with self.assertRaises(iphone.IPhoneError) as caught:
            prepared(entries).find_object(ITEM, 500)
        self.assertEqual(caught.exception.code, code)

    def test_single_exact_match_is_accepted(self):
        self.assertEqual(prepared([GOOD]).find_object(ITEM, 500)['name'], 'ABCD1234.JPEG')

    def test_two_identical_files_are_refused(self):
        self.assert_refused([GOOD, {**GOOD, 'name': 'EFGH5678.JPG'}], 'multiple_matches')

    def test_other_size_is_refused(self):
        self.assert_refused([{**GOOD, 'size': 501}], 'not_found_windows')

    def test_other_extension_is_refused(self):
        self.assert_refused([{**GOOD, 'name': 'ABCD1234.PNG'}], 'not_found_windows')

    def test_distant_date_is_refused(self):
        self.assert_refused([{**GOOD, 'date': GOOD['date'] + 3 * 86400000}], 'not_found_windows')

    def test_nothing_found_is_refused(self):
        self.assert_refused([], 'not_found_windows')

    def test_jpeg_and_jpg_are_the_same_extension(self):
        self.assertTrue(rocket.Deleter.same_file(GOOD, ITEM, 500))

    def test_neighbour_month_is_searched(self):
        entry = {**GOOD, 'folder': '202310_a'}
        self.assertEqual(prepared([], [entry]).find_object(ITEM, 500)['folder'], '202310_a')


class HelperTests(unittest.TestCase):
    """Every refusal from the Windows helper must stop the deletion with a clear reason."""

    def setUp(self):
        self.calls = []
        self.exit_code = 0
        self.originals = rocket.helper_path, rocket.subprocess.run
        rocket.helper_path = lambda: 'wpd-map.exe'
        rocket.subprocess.run = self.fake_run

    def tearDown(self):
        rocket.helper_path, rocket.subprocess.run = self.originals

    def fake_run(self, command, **kwargs):
        self.calls.append(command)
        return mock.Mock(returncode=self.exit_code, stdout=b'0x00000001 0x800700AA\r\n' if self.exit_code == 12 else b'')

    def test_arguments_reach_the_helper(self):
        rocket.Deleter.helper('delete', 'oA1', 'ABCD1234.JPG', '500')
        self.assertEqual(self.calls, [['wpd-map.exe', 'delete', 'oA1', 'ABCD1234.JPG', '500']])

    def test_each_refusal_stops_with_its_reason(self):
        for exit_code, expected in ((3, 'windows_no_device'), (10, 'object_missing'), (11, 'content_mismatch'), (12, 'windows_refused'), (99, 'windows_error')):
            self.exit_code = exit_code
            with self.assertRaises(iphone.IPhoneError) as caught:
                rocket.Deleter.helper('delete', 'oA1', 'A.JPG', '500')
            self.assertEqual(caught.exception.code, expected)

    def test_refusal_keeps_the_windows_codes(self):
        self.exit_code = 12
        with self.assertRaises(iphone.IPhoneError) as caught:
            rocket.Deleter.helper('delete', 'oA1', 'A.JPG', '500')
        self.assertEqual(caught.exception.params['detail'], '0x00000001 0x800700AA')


class DeleteOneTests(unittest.TestCase):
    """The file is only forgotten once the iPhone itself confirms it is gone."""

    def setUp(self):
        self.path = '/DCIM/A/IMG_1.JPG'
        self.library = new_library('dddddddddddddddd')
        self.library.files = {self.path: [3, 1]}
        self.library.meta = {'time': 1, 'assets': {self.path: ['', 1, 0, 0, 0]}}
        self.library.rebuild()
        self.deleter = rocket.Deleter(self.library, rocket.Copier(self.library))
        self.entry = {'folder': 'f', 'name': 'A.JPG', 'size': 3, 'date': 1, 'id': 'oA1'}
        self.deleter.find_object = lambda item, size: self.entry
        self.deleter.objects = {'x': [self.entry]}
        self.afc = mock.Mock()
        self.afc.info.return_value = {'st_size': '3'}
        self.afc.stream.side_effect = lambda path: (chunk for chunk in [b'abc'])
        self.phone = mock.MagicMock()
        self.phone.session.return_value.__enter__.return_value = self.afc
        self.item = self.library.items[self.path]

    def test_verification_and_deletion_are_two_separate_runs(self):
        """The iPhone answers "busy" if one process reads the file, then deletes it."""
        self.afc.exists.return_value = False
        with mock.patch.object(rocket.Deleter, 'helper') as helper:
            self.deleter.delete_one(self.phone, self.item, None, False)
        digest = hashlib.sha256(b'abc').hexdigest()
        self.assertEqual(helper.call_args_list, [
            mock.call('verify', 'oA1', 'A.JPG', '3', digest),
            mock.call('delete', 'oA1', 'A.JPG', '3'),
        ])

    def test_failed_verification_never_reaches_deletion(self):
        with mock.patch.object(rocket.Deleter, 'helper', side_effect=iphone.IPhoneError('content_mismatch')) as helper:
            with self.assertRaises(iphone.IPhoneError):
                self.deleter.delete_one(self.phone, self.item, None, False)
        self.assertEqual(helper.call_count, 1)
        self.assertEqual(helper.call_args[0][0], 'verify')
        self.assertIn(self.path, self.library.items)

    def test_success_needs_the_file_gone_from_the_iphone(self):
        self.afc.exists.return_value = False
        with mock.patch.object(rocket.Deleter, 'helper'):
            self.deleter.delete_one(self.phone, self.item, None, False)
        self.assertNotIn(self.path, self.library.items)
        self.assertEqual(self.deleter.job['deleted'], 1)
        self.assertEqual(self.deleter.objects['x'], [])

    def test_file_still_there_is_reported_and_kept_in_the_list(self):
        self.afc.exists.return_value = True
        clock = iter(range(0, 1000, 30))
        with mock.patch.object(rocket.Deleter, 'helper'), mock.patch.object(rocket.time, 'time', lambda: next(clock)), mock.patch.object(rocket.time, 'sleep'):
            with self.assertRaises(iphone.IPhoneError) as caught:
                self.deleter.delete_one(self.phone, self.item, None, False)
        self.assertEqual(caught.exception.code, 'phone_kept_file')
        self.assertIn(self.path, self.library.items)
        self.assertEqual(self.deleter.job['deleted'], 0)

    def test_dry_run_changes_nothing(self):
        with mock.patch.object(rocket.Deleter, 'helper') as helper:
            self.deleter.delete_one(self.phone, self.item, None, True)
        self.assertEqual([call.args[0] for call in helper.call_args_list], ['verify'])
        self.assertIn(self.path, self.library.items)
        self.afc.exists.assert_not_called()

    def test_helper_refusal_keeps_the_file(self):
        with mock.patch.object(rocket.Deleter, 'helper', side_effect=[None, iphone.IPhoneError('windows_refused')]):
            with self.assertRaises(iphone.IPhoneError):
                self.deleter.delete_one(self.phone, self.item, None, False)
        self.assertIn(self.path, self.library.items)


class StartTests(unittest.TestCase):
    def setUp(self):
        self.library = new_library()
        self.library.items = {
            '/DCIM/A/simple.JPG': {'p': '/DCIM/A/simple.JPG', 'n': 'simple.JPG', 's': 1, 'x': 1, 'c': 0},
            '/DCIM/A/edited.MOV': {'p': '/DCIM/A/edited.MOV', 'n': 'edited.MOV', 's': 1, 'x': 0, 'c': 0},
        }
        self.library.phone = object()
        self.copier = rocket.Copier(self.library)
        self.deleter = rocket.Deleter(self.library, self.copier)

    def code_of(self, paths, **overrides):
        with self.assertRaises(iphone.IPhoneError) as caught:
            self.deleter.start(paths, False, True, 'en')
        return caught.exception.code

    def test_item_that_is_not_deletable_is_refused(self):
        self.assertEqual(self.code_of(['/DCIM/A/edited.MOV']), 'nothing_deletable')

    def test_invented_path_is_refused(self):
        self.assertEqual(self.code_of(['/PhotoData/Photos.sqlite']), 'nothing_deletable')

    def test_empty_selection_is_refused(self):
        self.assertEqual(self.code_of([]), 'nothing_deletable')

    def test_no_phone_is_refused(self):
        self.library.phone = None
        self.assertEqual(self.code_of(['/DCIM/A/simple.JPG']), 'not_connected')

    def test_running_job_is_refused(self):
        self.copier.job['active'] = True
        self.assertEqual(self.code_of(['/DCIM/A/simple.JPG']), 'job_running')

    def test_size_of_a_batch_is_limited(self):
        for n in range(rocket.MAX_DELETE + 1):
            path = f'/DCIM/A/{n}.JPG'
            self.library.items[path] = {'p': path, 'n': f'{n}.JPG', 's': 1, 'x': 1, 'c': 0}
        paths = [p for p, i in self.library.items.items() if i['x']]
        self.assertEqual(self.code_of(paths), 'too_many_files')

    def test_demo_mode_cannot_delete(self):
        demo = rocket.DemoLibrary()
        path = next(p for p, i in demo.items.items() if i['x'])
        with self.assertRaises(iphone.IPhoneError) as caught:
            rocket.Deleter(demo, rocket.Copier(demo)).start([path], False, False, 'en')
        self.assertEqual(caught.exception.code, 'demo_mode')


if __name__ == '__main__':
    unittest.main()
