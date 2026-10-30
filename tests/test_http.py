"""The local server must stay out of reach of any website open in the browser."""
import http.client
import json
import threading
import unittest
import urllib.parse

from support import rocket


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        library = rocket.DemoLibrary()
        copier = rocket.Copier(library)
        rocket.Handler.library = library
        rocket.Handler.copier = copier
        rocket.Handler.deleter = rocket.Deleter(library, copier)
        cls.server = rocket.Server(('127.0.0.1', 0), rocket.Handler)
        cls.port = cls.server.server_address[1]
        rocket.Handler.port = cls.port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.library = library

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def post(self, path, data, headers=None):
        return self.call('POST', path, json.dumps(data), {'X-Rocket': '1', **(headers or {})})

    def test_page_and_status(self):
        self.assertEqual(self.call('GET', '/')[0], 200)
        status, data = self.call('GET', '/api/status')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)['demo'])

    def test_foreign_host_is_refused(self):
        self.assertEqual(self.call('GET', '/api/items', headers={'Host': 'evil.example'})[0], 403)

    def test_other_website_is_refused(self):
        self.assertEqual(self.call('GET', '/api/items', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.call('GET', '/api/items', headers={'Sec-Fetch-Site': 'same-site'})[0], 403)

    def test_post_without_the_custom_header_is_refused(self):
        self.assertEqual(self.call('POST', '/api/delete', '{}')[0], 403)
        self.assertEqual(self.call('POST', '/api/copy', '{}')[0], 403)

    def test_delete_needs_the_confirmation_word(self):
        path = next(p for p, i in self.library.items.items() if i['x'])
        for word in (None, '', 'supprimer', 'delete', 'yes'):
            status, data = self.post('/api/delete', {'paths': [path], 'confirm': word})
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(data)['code'], 'confirmation_missing')

    def test_malformed_bodies_do_not_crash(self):
        self.assertEqual(self.call('POST', '/api/copy', 'not json', {'X-Rocket': '1'})[0], 400)
        self.assertEqual(self.post('/api/copy', [1, 2])[0], 400)
        self.assertEqual(self.post('/api/copy', {'paths': 'x'})[0], 400)
        self.assertEqual(self.post('/api/copy', {'paths': [{'a': 1}]})[0], 400)

    def test_files_outside_the_library_are_unreachable(self):
        for route in ('thumb', 'file'):
            for target in ('/PhotoData/Photos.sqlite', '/etc/passwd', '..\\..\\rocket.py'):
                query = urllib.parse.quote(target, safe='')
                self.assertEqual(self.call('GET', f'/{route}?p={query}')[0], 404, target)

    def test_locale_route_serves_only_locales(self):
        self.assertEqual(self.call('GET', '/locales/fr.json')[0], 200)
        self.assertEqual(self.call('GET', '/locales/de.json')[0], 404)
        for path in ('/locales/../rocket.py', '/locales/..%5Crocket.py', '/locales/en.json/../../rocket.py', '/rocket.py', '/iphone.py'):
            self.assertEqual(self.call('GET', path)[0], 404, path)

    def test_demo_never_touches_anything(self):
        path = next(p for p, i in self.library.items.items() if i['x'])
        status, data = self.post('/api/delete', {'paths': [path], 'confirm': 'DELETE'})
        self.assertEqual((status, json.loads(data)['code']), (400, 'demo_mode'))
        status, data = self.post('/api/copy', {'paths': [path]})
        self.assertEqual((status, json.loads(data)['code']), (400, 'demo_mode'))


class DemoTests(unittest.TestCase):
    def test_demo_library_is_invented_and_stable(self):
        library = rocket.DemoLibrary()
        self.assertGreater(len(library.items), 2000)
        self.assertEqual(rocket.demo_image('/a.jpg'), rocket.demo_image('/a.jpg'))
        self.assertTrue(rocket.demo_image('/a.jpg').startswith(b'<svg'))


if __name__ == '__main__':
    unittest.main()
