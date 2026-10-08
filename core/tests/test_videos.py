from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from core.models import Video
from core.tests.factories import make_property, make_user


class VideoTests(TestCase):
    def test_youtube_links_of_every_kind_are_accepted(self):
        for url in ("https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
                    "https://youtube.com/shorts/dQw4w9WgXcQ", "https://m.youtube.com/watch?v=dQw4w9WgXcQ"):
            video = Video(title="Tour", youtube_url=url)
            video.full_clean()
            self.assertEqual(video.video_id, "dQw4w9WgXcQ")

    def test_other_links_are_rejected(self):
        with self.assertRaises(ValidationError):
            Video(title="x", youtube_url="https://vimeo.com/123").full_clean()

    def test_homepage_shows_videos_marked_for_home(self):
        Video.objects.create(title="Bangarpet town walk", youtube_url="https://youtu.be/dQw4w9WgXcQ")
        Video.objects.create(title="Hidden one", youtube_url="https://youtu.be/aaaaaaaaaaa", show_on_home=False)
        Video.objects.create(title="Switched off", youtube_url="https://youtu.be/bbbbbbbbbbb", is_active=False)
        page = self.client.get(reverse("core:home"))
        self.assertContains(page, "Bangarpet town walk")
        self.assertContains(page, 'data-yt-play="dQw4w9WgXcQ"')
        self.assertContains(page, 'id="ytPlayerModal"')
        self.assertNotContains(page, "Hidden one")
        self.assertNotContains(page, "Switched off")
        # Nothing is loaded from YouTube until a video is played.
        self.assertNotContains(page, "youtube-nocookie.com/embed/dQw4w9WgXcQ")

    def test_homepage_without_videos_has_no_section(self):
        self.assertNotContains(self.client.get(reverse("core:home")), 'id="ytPlayerModal"')

    def test_videos_page_lists_videos_and_property_tours(self):
        Video.objects.create(title="Hidden from home", youtube_url="https://youtu.be/aaaaaaaaaaa", show_on_home=False)
        prop = make_property(video_url="https://www.youtube.com/watch?v=ccccccccccc")
        make_property(video_url="")
        page = self.client.get(reverse("core:videos"))
        self.assertContains(page, "Hidden from home")
        self.assertContains(page, 'data-yt-play="ccccccccccc"')
        self.assertContains(page, prop.get_absolute_url())
        self.assertContains(page, 'data-yt-play=', count=2)

    def test_empty_videos_page(self):
        self.assertContains(self.client.get(reverse("core:videos")), "Videos are coming soon")

    def test_admin_can_add_a_video(self):
        self.client.force_login(make_user(Role.ADMIN))
        self.assertEqual(self.client.get(reverse("adminpanel:crud_list", args=["videos"])).status_code, 200)
        self.client.post(reverse("adminpanel:crud_add", args=["videos"]), {
            "title": "Plots near the station", "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
            "description": "", "show_on_home": "on", "display_order": "0", "is_active": "on",
        })
        self.assertTrue(Video.objects.filter(title="Plots near the station").exists())
