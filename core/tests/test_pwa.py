import json

from django.test import TestCase


class InstallableWebAppTests(TestCase):
    def test_manifest_describes_an_installable_app(self):
        resp = self.client.get("/manifest.webmanifest")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/manifest+json")
        data = json.loads(resp.content)
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["scope"], "/")
        sizes = {icon["sizes"] for icon in data["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        self.assertIn("maskable", {icon["purpose"] for icon in data["icons"]})

    def test_service_worker_served_from_root_and_never_caches_pages(self):
        resp = self.client.get("/sw.js")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp["Content-Type"].startswith("application/javascript"))
        self.assertEqual(resp["Service-Worker-Allowed"], "/")
        self.assertEqual(resp["Cache-Control"], "no-cache")
        body = resp.content.decode()
        self.assertIn('"/offline/"', body)
        self.assertIn("/static/css/main.css", body)
        self.assertIn('request.mode === "navigate"', body)

    def test_offline_page_and_head_tags(self):
        self.assertContains(self.client.get("/offline/"), "You're offline")
        home = self.client.get("/")
        self.assertContains(home, '<link rel="manifest" href="/manifest.webmanifest">')
        self.assertContains(home, 'data-sw="/sw.js"')
