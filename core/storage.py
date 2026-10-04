"""File storage backends for serverless hosting (Vercel).

* ``VercelBlobStorage`` keeps public uploads (property photos, avatars,
  banners) in a Vercel Blob store and serves them from its CDN.
* ``DatabaseStorage`` keeps private files (verification documents) inside the
  database, so they never get a public URL. They are only ever streamed through
  the permission-checked document view.
* ``VersionedStaticFilesStorage`` adds a per-deployment version to static URLs
  so browsers pick up new CSS/JS after each deploy without ``collectstatic``.
"""
import mimetypes
import os
import posixpath
from urllib.parse import quote

import requests
from django.conf import settings
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible

BLOB_API_URL = os.environ.get("VERCEL_BLOB_API_URL", "https://vercel.com/api/blob")
BLOB_API_VERSION = os.environ.get("VERCEL_BLOB_API_VERSION_OVERRIDE", "11")
BLOB_TIMEOUT = 30


class BlobStorageError(IOError):
    pass


@deconstructible
class VercelBlobStorage(Storage):
    """Public file storage on Vercel Blob (https://vercel.com/docs/vercel-blob)."""

    def __init__(self, token=None, cache_max_age=60 * 60 * 24 * 365):
        self._token = token
        self.cache_max_age = cache_max_age
        self._session = None

    # -- helpers ---------------------------------------------------------
    @property
    def token(self):
        token = self._token or os.environ.get("BLOB_READ_WRITE_TOKEN", "")
        if not token:
            raise ImproperlyConfigured(
                "BLOB_READ_WRITE_TOKEN is not set. Connect a Vercel Blob store to the project."
            )
        return token

    @property
    def store_id(self):
        # Tokens look like vercel_blob_rw_<storeId>_<secret>.
        parts = self.token.split("_")
        if len(parts) < 5:
            raise ImproperlyConfigured("BLOB_READ_WRITE_TOKEN has an unexpected format.")
        return parts[3].lower()

    @property
    def base_url(self):
        return f"https://{self.store_id}.public.blob.vercel-storage.com/"

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _headers(self, **extra):
        headers = {"authorization": f"Bearer {self.token}", "x-api-version": BLOB_API_VERSION}
        headers.update(extra)
        return headers

    @staticmethod
    def _clean(name):
        return posixpath.normpath(name.replace("\\", "/")).lstrip("/")

    # -- Storage API -----------------------------------------------------
    def _save(self, name, content):
        name = self._clean(name)
        content.seek(0)
        data = content.read()
        content_type = getattr(content, "content_type", None) or mimetypes.guess_type(name)[0] or "application/octet-stream"
        resp = self.session.put(
            BLOB_API_URL,
            params={"pathname": name},
            data=data,
            headers=self._headers(**{
                "x-content-type": content_type,
                "x-add-random-suffix": "0",
                "x-allow-overwrite": "0",
                "x-cache-control-max-age": str(self.cache_max_age),
                "x-vercel-blob-access": "public",
            }),
            timeout=BLOB_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise BlobStorageError(f"Blob upload failed ({resp.status_code}): {resp.text[:200]}")
        return resp.json().get("pathname", name)

    def _open(self, name, mode="rb"):
        resp = self.session.get(self.url(name), timeout=BLOB_TIMEOUT)
        if resp.status_code == 404:
            raise FileNotFoundError(name)
        resp.raise_for_status()
        return ContentFile(resp.content, name=name)

    def _head(self, name):
        resp = self.session.get(
            BLOB_API_URL, params={"url": self.url(name)}, headers=self._headers(), timeout=BLOB_TIMEOUT
        )
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise BlobStorageError(f"Blob lookup failed ({resp.status_code})")
        return resp.json()

    def exists(self, name):
        return self._head(self._clean(name)) is not None

    def size(self, name):
        info = self._head(self._clean(name))
        if info is None:
            raise FileNotFoundError(name)
        return info.get("size", 0)

    def delete(self, name):
        if not name:
            return
        resp = self.session.post(
            f"{BLOB_API_URL}/delete",
            json={"urls": [self.url(name)]},
            headers=self._headers(),
            timeout=BLOB_TIMEOUT,
        )
        if resp.status_code >= 400 and resp.status_code != 404:
            raise BlobStorageError(f"Blob delete failed ({resp.status_code})")

    def url(self, name):
        return self.base_url + quote(self._clean(name))

    def listdir(self, path):  # pragma: no cover - not needed by the app
        raise NotImplementedError("Listing is not supported for Vercel Blob storage.")


@deconstructible
class DatabaseStorage(Storage):
    """Private files stored in the database (``core.StoredFile``); no public URL."""

    def _model(self):
        from core.models import StoredFile

        return StoredFile

    def _save(self, name, content):
        content.seek(0)
        data = content.read()
        content_type = getattr(content, "content_type", None) or mimetypes.guess_type(name)[0] or ""
        self._model().objects.create(name=name, data=data, size=len(data), content_type=content_type)
        return name

    def _open(self, name, mode="rb"):
        try:
            obj = self._model().objects.get(name=name)
        except self._model().DoesNotExist:
            raise FileNotFoundError(name) from None
        return ContentFile(bytes(obj.data), name=name)

    def exists(self, name):
        return self._model().objects.filter(name=name).exists()

    def delete(self, name):
        self._model().objects.filter(name=name).delete()

    def size(self, name):
        obj = self._model().objects.filter(name=name).only("size").first()
        if obj is None:
            raise FileNotFoundError(name)
        return obj.size

    def url(self, name):
        raise NotImplementedError("Private documents are served through a permission-checked view only.")

    def listdir(self, path):  # pragma: no cover - not needed by the app
        raise NotImplementedError


class VersionedStaticFilesStorage(StaticFilesStorage):
    """Appends ?v=<deployment> to static URLs (cache busting without collectstatic)."""

    def url(self, name):
        url = super().url(name)
        version = getattr(settings, "STATIC_VERSION", "")
        if version and "?" not in url:
            url = f"{url}?v={version}"
        return url
