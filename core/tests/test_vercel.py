import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from core.models import StoredFile
from core.storage import DatabaseStorage, VercelBlobStorage, VersionedStaticFilesStorage

TOKEN = "vercel_blob_rw_AbCd1234_secretpart"
BASE_DIR = Path(__file__).resolve().parents[2]


def _response(status=200, payload=None, content=b""):
    resp = mock.Mock(status_code=status, content=content, text=json.dumps(payload or {}))
    resp.json.return_value = payload or {}
    resp.raise_for_status = mock.Mock()
    return resp


class VercelBlobStorageTests(TestCase):
    def setUp(self):
        self.storage = VercelBlobStorage(token=TOKEN)
        self.session = mock.Mock()
        self.storage._session = self.session

    def test_save_uploads_publicly_and_url_points_at_the_store(self):
        self.session.get.return_value = _response(404)  # name is free
        self.session.put.return_value = _response(200, {"pathname": "properties/1/a.webp"})
        name = self.storage.save("properties/1/a.webp", ContentFile(b"img", name="a.webp"))
        self.assertEqual(name, "properties/1/a.webp")
        args, kwargs = self.session.put.call_args
        self.assertEqual(kwargs["params"], {"pathname": "properties/1/a.webp"})
        self.assertEqual(kwargs["data"], b"img")
        headers = kwargs["headers"]
        self.assertEqual(headers["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["x-vercel-blob-access"], "public")
        self.assertEqual(headers["x-content-type"], "image/webp")
        self.assertEqual(headers["x-add-random-suffix"], "0")
        self.assertEqual(
            self.storage.url(name), "https://abcd1234.public.blob.vercel-storage.com/properties/1/a.webp"
        )

    def test_existing_name_gets_a_new_name(self):
        self.session.get.side_effect = [_response(200, {"size": 3}), _response(404)]
        self.session.put.side_effect = lambda url, params, **kw: _response(200, {"pathname": params["pathname"]})
        name = self.storage.save("banners/logo.png", ContentFile(b"png"))
        self.assertNotEqual(name, "banners/logo.png")
        self.assertTrue(name.startswith("banners/logo_"))

    def test_delete_calls_the_delete_api(self):
        self.session.post.return_value = _response(200)
        self.storage.delete("avatars/x.webp")
        args, kwargs = self.session.post.call_args
        self.assertTrue(args[0].endswith("/delete"))
        self.assertEqual(kwargs["json"], {"urls": ["https://abcd1234.public.blob.vercel-storage.com/avatars/x.webp"]})


class DatabaseStorageTests(TestCase):
    def test_round_trip_without_public_url(self):
        storage = DatabaseStorage()
        name = storage.save("verification/1/doc.pdf", ContentFile(b"%PDF-1.4 test"))
        self.assertTrue(storage.exists(name))
        self.assertEqual(storage.size(name), 13)
        with storage.open(name) as fh:
            self.assertEqual(fh.read(), b"%PDF-1.4 test")
        with self.assertRaises(NotImplementedError):
            storage.url(name)
        storage.delete(name)
        self.assertFalse(StoredFile.objects.exists())

    def test_name_collision_gets_unique_name(self):
        storage = DatabaseStorage()
        first = storage.save("verification/1/doc.pdf", ContentFile(b"a"))
        second = storage.save("verification/1/doc.pdf", ContentFile(b"b"))
        self.assertNotEqual(first, second)

    @override_settings(PRIVATE_FILE_STORAGE="core.storage.DatabaseStorage")
    def test_verification_documents_can_use_database_storage(self):
        from accounts.models import private_storage

        self.assertIsInstance(private_storage(), DatabaseStorage)


@override_settings(STATIC_VERSION="abc123")
class VersionedStaticTests(TestCase):
    def test_static_urls_carry_the_deployment_version(self):
        self.assertEqual(VersionedStaticFilesStorage().url("css/main.css"), "/static/css/main.css?v=abc123")


class CronEndpointTests(TestCase):
    url = "/cron/scheduled-tasks/"

    def test_disabled_without_secret(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": ""}):
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_requires_the_bearer_secret(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret-value"}):
            self.assertEqual(self.client.get(self.url, HTTP_AUTHORIZATION="Bearer wrong").status_code, 403)
            with mock.patch("django.core.management.call_command") as call:
                resp = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer s3cret-value")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(call.call_args[0][0], "run_scheduled_tasks")


class VercelSettingsTests(TestCase):
    """Production settings load correctly with the environment Vercel provides."""

    def _load(self, **extra):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
            "SECRET_KEY": "x" * 50,
            "ALLOWED_HOSTS": "",
            "VERCEL": "1",
            "VERCEL_URL": "bph-abc123.vercel.app",
            "VERCEL_PROJECT_PRODUCTION_URL": "bph.vercel.app",
            "VERCEL_DEPLOYMENT_ID": "dpl_XyZ987654321",
            "DATABASE_URL": "postgres://u:p@db.example.com:5432/bph?sslmode=require",
            **extra,
        }
        code = (
            "import json, django; from django.conf import settings; django.setup();"
            "print(json.dumps({'hosts': settings.ALLOWED_HOSTS, 'csrf': settings.CSRF_TRUSTED_ORIGINS,"
            "'site': settings.SITE_URL, 'storages': settings.STORAGES, 'private': settings.PRIVATE_FILE_STORAGE,"
            "'version': settings.STATIC_VERSION, 'conn': settings.DATABASES['default']['CONN_MAX_AGE'],"
            "'cursors': settings.DATABASES['default'].get('DISABLE_SERVER_SIDE_CURSORS'),"
            "'prepare': settings.DATABASES['default']['OPTIONS'].get('prepare_threshold', 'unset'),"
            "'handlers': settings.LOGGING['root']['handlers'], 'finders': settings.WHITENOISE_USE_FINDERS}))"
        )
        return subprocess.run([sys.executable, "-c", code], cwd=BASE_DIR, env=env, capture_output=True, text=True)

    def test_vercel_environment(self):
        result = self._load()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIn("bph.vercel.app", data["hosts"])
        self.assertIn("bph-abc123.vercel.app", data["hosts"])
        self.assertIn("https://bph.vercel.app", data["csrf"])
        self.assertEqual(data["site"], "https://bph.vercel.app")
        self.assertEqual(data["storages"]["default"]["BACKEND"], "core.storage.VercelBlobStorage")
        self.assertEqual(data["private"], "core.storage.DatabaseStorage")
        self.assertEqual(data["version"], "XyZ9876543")
        self.assertEqual(data["conn"], 0)
        self.assertTrue(data["cursors"])
        self.assertIsNone(data["prepare"])
        self.assertEqual(data["handlers"], ["console"])
        self.assertTrue(data["finders"])

    def test_vercel_app_hosts_allowed_without_system_variables(self):
        result = self._load(VERCEL_URL="", VERCEL_PROJECT_PRODUCTION_URL="", VERCEL_DEPLOYMENT_ID="")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIn(".vercel.app", data["hosts"])
        self.assertIn("https://*.vercel.app", data["csrf"])

    def test_missing_database_fails_loudly(self):
        result = self._load(DATABASE_URL="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_URL is not set", result.stderr)
