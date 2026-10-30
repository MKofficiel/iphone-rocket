"""English and French must stay complete: a missing key shows up as raw text in the interface."""
import json
import os
import re
import unittest

from support import ROOT, rocket


def locale(code):
    with open(os.path.join(ROOT, 'locales', f'{code}.json'), encoding='utf-8') as handle:
        return json.load(handle)


def source(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as handle:
        return handle.read()


EN, FR = locale('en'), locale('fr')
HTML = source('index.html')


class LocaleTests(unittest.TestCase):
    def test_same_keys_in_both_languages(self):
        self.assertEqual(sorted(EN), sorted(FR))

    def test_same_placeholders_in_both_languages(self):
        placeholders = lambda text: sorted(re.findall(r'\{(\w+)\}', text))
        for key in EN:
            self.assertEqual(placeholders(EN[key]), placeholders(FR[key]), key)

    def test_nothing_is_left_empty(self):
        for code, strings in (('en', EN), ('fr', FR)):
            for key, value in strings.items():
                self.assertTrue(value.strip(), f'{code}:{key}')

    def test_static_keys_exist(self):
        for key in re.findall(r'data-i18n(?:-placeholder|-aria)?="([^"]+)"', HTML):
            self.assertIn(key, EN)

    def test_keys_used_in_code_exist(self):
        for key in re.findall(r"\bt\('([\w.]+)'", HTML):
            if not key.endswith('.'):
                self.assertIn(key, EN)
        for key in re.findall(r"\btn\('([\w.]+)'", HTML):
            self.assertIn(f'{key}.one', EN)
            self.assertIn(f'{key}.other', EN)

    def test_filters_and_steps_are_translated(self):
        for name in re.findall(r"^\s*\['(\w+)', ", HTML, re.M):
            self.assertIn(f'filter.{name}', EN)
        for step in ('find', 'backup', 'verify', 'delete'):
            self.assertIn(f'job.step.{step}', EN)

    def test_every_error_code_has_a_message(self):
        codes = set()
        for name in ('iphone.py', 'rocket.py'):
            codes.update(re.findall(r"IPhoneError\('(\w+)'", source(name)))
            codes.update(re.findall(r"super\(\).__init__\('(\w+)'", source(name)))
        codes.update(rocket.HELPER_ERRORS.values())
        codes.update({'io_error', 'windows_error'})
        self.assertGreater(len(codes), 20)
        for code in codes:
            self.assertIn(f'errors.{code}', EN)

    def test_confirmation_word_is_accepted_by_the_server(self):
        self.assertIn(EN['delete.word'], rocket.DELETE_WORDS)
        self.assertIn(FR['delete.word'], rocket.DELETE_WORDS)

    def test_no_french_is_left_in_the_interface_code(self):
        script = HTML[HTML.index('<script>'):]
        for word in ('Supprimer', 'Copier', 'Fermer', 'Annuler', 'fichier', 'iPhone débranché', 'Aperçu'):
            self.assertNotIn(word, script, word)


if __name__ == '__main__':
    unittest.main()
