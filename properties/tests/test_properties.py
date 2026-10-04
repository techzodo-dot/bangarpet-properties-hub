import io
from datetime import timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import Role
from core.models import PlatformSetting
from core.tests.factories import image_file, make_property, make_user
from moderation.models import Report
from properties.models import Category, Favourite, Location, Property, PropertyRevision


class ListingWizardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user(Role.OWNER)
        self.client.force_login(self.owner)
        self.house = Category.objects.get(slug="houses")
        self.town = Location.objects.get(slug="bangarpet")

    def _create_draft(self, **overrides):
        data = {"title": "2 BHK independent house near station", "category": self.house.pk, "purpose": "rent",
                "description": "Bright, airy house with 24 hour water supply and covered parking.",
                "availability": "immediate"}
        data.update(overrides)
        return self.client.post(reverse("dashboard:partner_property_add"), data)

    def test_full_wizard_submission(self):
        resp = self._create_draft()
        prop = Property.objects.get(owner=self.owner)
        self.assertEqual(prop.status, Property.Status.DRAFT)
        self.assertRedirects(resp, reverse("dashboard:partner_property_step", args=[prop.pk, 2]))
        self.assertTrue(prop.reference.startswith("BPH"))

        step = lambda n: reverse("dashboard:partner_property_step", args=[prop.pk, n])  # noqa: E731
        self.client.post(step(2), {"state": "Karnataka", "district": "Kolar", "town": self.town.pk,
                                   "area": Location.objects.get(slug="bangarpet-kolar-road").pk,
                                   "locality": "Behind bus stand", "pin_code": "563114"})
        self.client.post(step(3), {"bedrooms": 2, "bathrooms": 1, "built_up_area": 850, "land_area_unit": "sqft",
                                   "furnishing": "semi", "parking": "bike"})
        self.client.post(step(4), {"monthly_rent": 8500, "security_deposit": 40000})
        self.client.post(step(5), {"action": "upload", "images": [image_file("a.jpg"), image_file("b.png", fmt="PNG")]})
        self.client.post(step(5), {"action": "save", "video_url": ""})
        self.client.post(step(6), {"contact_name": "Owner", "contact_phone": "9876543210", "preferred_contact": "phone",
                                   "contact_visibility": "registered"})
        prop.refresh_from_db()
        self.assertEqual(prop.images.count(), 2)
        self.assertEqual(prop.images.filter(is_primary=True).count(), 1)
        self.assertEqual(prop.price, 8500)
        resp = self.client.post(step(7), {"accept_policy": "on"})
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.PENDING)
        self.assertIsNotNone(prop.policy_accepted_at)

    def test_submit_requires_policy_and_complete_details(self):
        self._create_draft()
        prop = Property.objects.get(owner=self.owner)
        self.client.post(reverse("dashboard:partner_property_step", args=[prop.pk, 7]), {"accept_policy": "on"})
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.DRAFT)  # no photos / pricing yet

    def test_pg_cannot_be_listed_for_sale(self):
        pg = Category.objects.get(slug="pg-rooms")
        resp = self._create_draft(category=pg.pk, purpose="sale")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Property.objects.exists())

    def test_rent_listing_requires_rent(self):
        prop = make_property(owner=self.owner, status="draft")
        resp = self.client.post(reverse("dashboard:partner_property_step", args=[prop.pk, 4]), {"monthly_rent": ""})
        self.assertContains(resp, "Enter the monthly rent")

    def test_listing_limit_enforced_on_submit(self):
        make_property(owner=self.owner)  # occupies the single free slot
        prop = make_property(owner=self.owner, status="draft", with_image=True)
        self.client.post(reverse("dashboard:partner_property_step", args=[prop.pk, 7]), {"accept_policy": "on"})
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.DRAFT)

    def test_editing_live_listing_triggers_moderation(self):
        prop = make_property(owner=self.owner, with_image=True)
        data = {"title": "Completely different title for review", "category": prop.category_id, "purpose": "rent",
                "description": prop.description, "availability": "immediate"}
        self.client.post(reverse("dashboard:partner_property_step", args=[prop.pk, 1]), data)
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.PENDING)
        rev = PropertyRevision.objects.get(property=prop)
        self.assertTrue(rev.requires_moderation)
        self.assertIn("title", rev.changes)

    def test_minor_edit_keeps_listing_live(self):
        prop = make_property(owner=self.owner, with_image=True)
        self.client.post(reverse("dashboard:partner_property_step", args=[prop.pk, 3]),
                         {"bedrooms": 2, "bathrooms": 2, "built_up_area": 900, "land_area_unit": "sqft",
                          "furnishing": "fully", "parking": "car"})
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.ACTIVE)
        self.assertEqual(prop.furnishing, "fully")

    def test_pause_resume_close_and_delete(self):
        prop = make_property(owner=self.owner)
        act = lambda a, **d: self.client.post(reverse("dashboard:partner_property_action", args=[prop.pk, a]), d)  # noqa: E731
        act("pause"); prop.refresh_from_db(); self.assertEqual(prop.status, "paused")
        act("resume"); prop.refresh_from_db(); self.assertEqual(prop.status, "active")
        act("rented"); prop.refresh_from_db(); self.assertEqual(prop.status, "rented")
        act("delete", confirm_reference="WRONG"); prop.refresh_from_db(); self.assertEqual(prop.status, "rented")
        act("delete", confirm_reference=prop.reference); prop.refresh_from_db(); self.assertEqual(prop.status, "deleted")

    def test_duplicate_creates_draft_copy(self):
        prop = make_property(owner=self.owner, with_image=True)
        self.client.post(reverse("dashboard:partner_property_action", args=[prop.pk, "duplicate"]))
        copy = Property.objects.exclude(pk=prop.pk).get()
        self.assertEqual(copy.status, "draft")
        self.assertEqual(copy.images.count(), 1)
        self.assertNotEqual(copy.slug, prop.slug)

    def test_renew_expired_listing(self):
        prop = make_property(owner=self.owner, status="expired", expires_at=timezone.now() - timedelta(days=1),
                             published_at=timezone.now() - timedelta(days=31))
        self.client.post(reverse("dashboard:partner_property_action", args=[prop.pk, "renew"]))
        prop.refresh_from_db()
        self.assertEqual(prop.status, "active")
        self.assertGreater(prop.expires_at, timezone.now() + timedelta(days=25))


class CrossUserProtectionTests(TestCase):
    def setUp(self):
        self.owner = make_user(Role.OWNER)
        self.other = make_user(Role.OWNER)
        self.prop = make_property(owner=self.owner, with_image=True)

    def test_other_partner_cannot_view_or_edit(self):
        self.client.force_login(self.other)
        for step in range(1, 8):
            self.assertEqual(self.client.get(reverse("dashboard:partner_property_step", args=[self.prop.pk, step])).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard:partner_property_manage", args=[self.prop.pk])).status_code, 404)
        resp = self.client.post(reverse("dashboard:partner_property_step", args=[self.prop.pk, 1]), {"title": "Hijacked title here"})
        self.assertEqual(resp.status_code, 404)
        for action in ("pause", "delete", "duplicate"):
            self.client.post(reverse("dashboard:partner_property_action", args=[self.prop.pk, action]),
                             {"confirm_reference": self.prop.reference})
        image = self.prop.images.first()
        self.client.post(reverse("dashboard:partner_image_action", args=[self.prop.pk, image.pk, "delete"]))
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, "active")
        self.assertEqual(self.prop.title, "Spacious 2 BHK house for families")
        self.assertEqual(self.prop.images.count(), 1)
        self.assertEqual(Property.objects.count(), 1)


class ImageUploadValidationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user(Role.OWNER)
        self.client.force_login(self.owner)
        self.prop = make_property(owner=self.owner, status="draft")
        self.url = reverse("dashboard:partner_property_step", args=[self.prop.pk, 5])

    def test_rejects_non_image(self):
        fake = SimpleUploadedFile("evil.jpg", b"<?php echo 'hi'; ?>", content_type="image/jpeg")
        resp = self.client.post(self.url, {"action": "upload", "images": [fake]})
        self.assertContains(resp, "valid image")
        self.assertEqual(self.prop.images.count(), 0)

    def test_rejects_disallowed_format_and_tiny_images(self):
        buf = io.BytesIO()
        Image.new("RGB", (800, 600)).save(buf, "GIF")
        gif = SimpleUploadedFile("a.gif", buf.getvalue(), content_type="image/gif")
        self.client.post(self.url, {"action": "upload", "images": [gif]})
        self.client.post(self.url, {"action": "upload", "images": [image_file(size=(100, 80))]})
        self.assertEqual(self.prop.images.count(), 0)

    def test_rejects_oversized_file(self):
        with self.settings(MAX_IMAGE_UPLOAD_MB=0.001):
            resp = self.client.post(self.url, {"action": "upload", "images": [image_file()]})
        self.assertContains(resp, "too large")
        self.assertEqual(self.prop.images.count(), 0)

    def test_valid_image_is_reencoded_without_exif(self):
        self.client.post(self.url, {"action": "upload", "images": [image_file(exif=True, size=(2600, 1800))]})
        img = self.prop.images.get()
        self.assertTrue(img.is_primary)
        with Image.open(img.image.path) as stored:
            self.assertLessEqual(max(stored.size), 1920)
            self.assertFalse(stored.getexif())
        self.assertTrue(img.thumbnail)

    def test_set_primary_and_remove(self):
        self.client.post(self.url, {"action": "upload", "images": [image_file("a.jpg"), image_file("b.jpg")]})
        first, second = list(self.prop.images.order_by("id"))
        self.client.post(reverse("dashboard:partner_image_action", args=[self.prop.pk, second.pk, "primary"]))
        second.refresh_from_db()
        self.assertTrue(second.is_primary)
        self.client.post(reverse("dashboard:partner_image_action", args=[self.prop.pk, second.pk, "delete"]))
        self.assertTrue(self.prop.images.get().is_primary)


class SearchTests(TestCase):
    def setUp(self):
        verified_broker = make_user(Role.BROKER)
        verified_broker.broker_profile.verification_status = "verified"
        verified_broker.broker_profile.save()
        self.cheap = make_property(title="Cheap rental house one", price=5000, bedrooms=1)
        self.mid = make_property(title="Mid rental house two", price=12000, bedrooms=3, owner=verified_broker, furnishing="fully")
        self.sale = make_property(title="House for sale in town", purpose="sale", price=4500000)
        self.apartment = make_property(title="Apartment in KGF road", category="apartments", price=9000,
                                       area=Location.objects.get(slug="bangarpet-kgf-road"))
        self.pending = make_property(title="Pending listing not public", status="pending", price=7000)
        self.expired = make_property(title="Expired but status active", price=7000,
                                     expires_at=timezone.now() - timedelta(hours=1))

    def _titles(self, params, url=None):
        resp = self.client.get(url or reverse("properties:search"), params)
        self.assertEqual(resp.status_code, 200)
        return [p.title for p in resp.context["page_obj"].object_list]

    def test_only_public_listings(self):
        titles = self._titles({})
        self.assertNotIn(self.pending.title, titles)
        self.assertNotIn(self.expired.title, titles)
        self.assertEqual(len(titles), 4)

    def test_purpose_and_price_filters(self):
        self.assertEqual(self._titles({"purpose": "sale"}), [self.sale.title])
        titles = self._titles({"purpose": "rent", "min_price": 6000, "max_price": 10000})
        self.assertEqual(titles, [self.apartment.title])

    def test_bedrooms_furnishing_verified_listed_by(self):
        self.assertEqual(self._titles({"bedrooms": "3"}), [self.mid.title])
        self.assertEqual(self._titles({"furnishing": "fully"}), [self.mid.title])
        self.assertEqual(self._titles({"verified": "on"}), [self.mid.title])
        self.assertEqual(self._titles({"listed_by": "broker"}), [self.mid.title])

    def test_location_and_category_and_keyword(self):
        self.assertEqual(self._titles({"location": "bangarpet-kgf-road"}), [self.apartment.title])
        self.assertEqual(self._titles({}, url=reverse("properties:category", args=["apartments"])), [self.apartment.title])
        self.assertEqual(self._titles({"q": self.sale.reference}), [self.sale.title])
        self.assertEqual(len(self._titles({}, url=reverse("properties:rent"))), 3)

    def test_sorting_and_pagination(self):
        titles = self._titles({"purpose": "rent", "sort": "price_asc"})
        self.assertEqual(titles[0], self.cheap.title)
        titles = self._titles({"purpose": "rent", "sort": "price_desc"})
        self.assertEqual(titles[0], self.mid.title)
        for i in range(12):
            make_property(title=f"Bulk listing number {i}")
        resp = self.client.get(reverse("properties:search"), {"page": 2})
        self.assertEqual(len(resp.context["page_obj"].object_list), 4)

    def test_invalid_filters_are_ignored(self):
        resp = self.client.get(reverse("properties:search"), {"min_price": "abc", "bedrooms": "99", "sort": "hack"})
        self.assertEqual(resp.status_code, 200)

    def test_featured_listings_rank_first_by_default(self):
        self.cheap.featured_until = timezone.now() + timedelta(days=3)
        self.cheap.save()
        self.assertEqual(self._titles({})[0], self.cheap.title)


class DetailVisibilityTests(TestCase):
    def test_public_detail_and_private_preview(self):
        prop = make_property(with_image=True)
        resp = self.client.get(prop.get_absolute_url())
        self.assertContains(resp, prop.title)
        self.assertContains(resp, "application/ld+json")
        self.assertNotContains(resp, prop.contact_phone)  # registered-only by default
        prop.refresh_from_db()
        self.assertEqual(prop.view_count, 1)
        self.client.get(prop.get_absolute_url())
        prop.refresh_from_db()
        self.assertEqual(prop.view_count, 1)  # once per session

        pending = make_property(status="pending")
        self.assertEqual(self.client.get(pending.get_absolute_url()).status_code, 404)
        self.client.force_login(pending.owner)
        resp = self.client.get(pending.get_absolute_url())
        self.assertContains(resp, "noindex")

    def test_signed_in_user_sees_phone_and_hidden_never_shown(self):
        prop = make_property()
        self.client.force_login(make_user())
        self.assertContains(self.client.get(prop.get_absolute_url()), prop.contact_phone)
        prop.contact_visibility = "hidden"
        prop.save()
        self.assertNotContains(self.client.get(prop.get_absolute_url()), "tel:" + prop.contact_phone)

    def test_exact_address_hidden_by_default(self):
        prop = make_property(street_address="12, Secret Lane")
        self.assertNotContains(self.client.get(prop.get_absolute_url()), "Secret Lane")

    def test_rented_listing_returns_gone(self):
        prop = make_property(status="rented")
        self.assertEqual(self.client.get(prop.get_absolute_url()).status_code, 410)


class FavouriteAndReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.prop = make_property()
        self.user = make_user()

    def test_favourite_toggle(self):
        url = reverse("properties:toggle_favourite", args=[self.prop.pk])
        self.assertEqual(self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code, 401)
        self.client.force_login(self.user)
        resp = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.json(), {"favourited": True})
        self.assertTrue(Favourite.objects.filter(user=self.user, property=self.prop).exists())
        self.assertEqual(self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest").json(), {"favourited": False})
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_cannot_favourite_non_public(self):
        pending = make_property(status="pending")
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse("properties:toggle_favourite", args=[pending.pk])).status_code, 404)

    def test_report_once_per_listing(self):
        self.client.force_login(self.user)
        url = reverse("properties:report", args=[self.prop.pk])
        self.client.post(url, {"reason": "fraud", "details": "Asked for advance"})
        self.client.post(url, {"reason": "fraud"})
        self.assertEqual(Report.objects.count(), 1)

    def test_owner_cannot_report_own_listing(self):
        self.client.force_login(self.prop.owner)
        self.client.post(reverse("properties:report", args=[self.prop.pk]), {"reason": "fraud"})
        self.assertEqual(Report.objects.count(), 0)

    def test_save_search(self):
        self.client.force_login(self.user)
        self.client.post(reverse("properties:save_search"), {"name": "2BHK under 10k", "query_string": "bedrooms=2&max_price=10000"})
        search = self.user.saved_searches.get()
        self.assertIn("bedrooms=2", search.get_absolute_url())


class SeoTests(TestCase):
    def test_sitemap_and_robots(self):
        live = make_property(title="Live listing for sitemap")
        hidden = make_property(title="Hidden pending listing", status="pending")
        resp = self.client.get("/sitemap.xml")
        self.assertContains(resp, live.get_absolute_url())
        self.assertNotContains(resp, hidden.slug)
        robots = self.client.get("/robots.txt").content.decode()
        self.assertIn("Disallow: /management/", robots)
        self.assertIn("Sitemap:", robots)

    def test_homepage_renders_from_database(self):
        PlatformSetting.load()
        resp = self.client.get("/")
        self.assertContains(resp, "Listings are on their way")  # empty state, no fake listings
        prop = make_property(title="Real listing on home")
        cache.clear()
        self.assertContains(self.client.get("/"), prop.title)
