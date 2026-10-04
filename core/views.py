from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.http import HttpResponse, HttpResponseServerError
from django.shortcuts import redirect, render
from django.template import loader
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

from accounts.models import BrokerProfile, OwnerProfile, Role, VerificationStatus
from core import ratelimit
from core.faq import FAQS
from core.forms import ContactForm
from core.models import Advertisement, Banner
from notifications.models import Notification
from notifications.services import notify_admins
from properties.forms import PropertySearchForm
from properties.models import Category, Location, Property
from properties.views import favourite_ids


def home(request):
    public = Property.objects.public()
    now = timezone.now()
    featured = list(public.filter(featured_until__gt=now).with_card_data().order_by("-priority", "-published_at")[:6])
    if len(featured) < 6:
        sponsored_ids = Advertisement.objects.live().filter(placement=Advertisement.Placement.HOME_FEATURED).values_list("property_id", flat=True)
        featured += list(public.filter(pk__in=sponsored_ids).exclude(pk__in=[p.pk for p in featured]).with_card_data()[: 6 - len(featured)])
    recent = public.with_card_data().order_by("-published_at")[:8]
    areas = (
        Location.objects.filter(is_active=True, is_popular=True)
        .annotate(
            listing_count=Count("area_properties", filter=Q(area_properties__in=public), distinct=True)
            + Count("town_properties", filter=Q(town_properties__in=public), distinct=True)
        )
        .select_related("parent")
        .order_by("kind", "display_order")[:9]
    )

    # Virtual tour preview: a live listing with several photos (video tours first, then featured).
    tour_candidates = (
        public.annotate(photo_count=Count("images"))
        .filter(photo_count__gte=2)
        .annotate(has_video=Case(When(video_url="", then=Value(0)), default=Value(1), output_field=IntegerField()))
        .order_by("-has_video", F("featured_until").desc(nulls_last=True), "-published_at")
    )
    tour_list = list(tour_candidates.with_card_data()[:4])
    tour = tour_list[0] if tour_list else None
    hero_property = featured[0] if featured and featured[0].primary_image else (tour or (recent[0] if recent else None))

    User = get_user_model()
    agents = (
        User.objects.filter(role=Role.BROKER, is_active=True, broker_profile__verification_status=VerificationStatus.VERIFIED)
        .select_related("broker_profile", "profile")
        .annotate(active_listings=Count("properties", filter=Q(properties__in=public)))
        .order_by("-active_listings")[:4]
    )
    verified_partners = (
        OwnerProfile.objects.filter(verification_status=VerificationStatus.VERIFIED, user__is_active=True).count()
        + BrokerProfile.objects.filter(verification_status=VerificationStatus.VERIFIED, user__is_active=True).count()
    )
    context = {
        "search_form": PropertySearchForm(),
        "categories": Category.objects.filter(is_active=True).annotate(
            live_count=Count("properties", filter=Q(properties__in=public), distinct=True)
        ),
        "featured": featured,
        "recent": recent,
        "areas": areas,
        "tour": tour,
        "tour_images": list(tour.images.all()[:8]) if tour else [],
        "more_tours": tour_list[1:],
        "hero_property": hero_property,
        "agents": agents,
        "banners_top": Banner.objects.live().filter(placement=Banner.Placement.HOME_TOP)[:3],
        "banners_middle": Banner.objects.live().filter(placement=Banner.Placement.HOME_MIDDLE)[:2],
        "faqs": FAQS[:6],
        "favourite_ids": favourite_ids(request.user),
        "contact_form": ContactForm(),
        "contact_sent": request.GET.get("sent") == "1",
        "stats": {
            "live": public.count(),
            "verified_partners": verified_partners,
            "localities": public.values("area").exclude(area=None).distinct().count(),
        },
    }
    return render(request, "core/home.html", context)


def about(request):
    return render(request, "core/about.html")


def faq(request):
    return render(request, "core/faq.html", {"faqs": FAQS})


def privacy(request):
    return render(request, "core/privacy.html")


def terms(request):
    return render(request, "core/terms.html")


def listing_policy(request):
    return render(request, "core/listing_policy.html")


def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST":
        if not ratelimit.check_and_hit("contact", ratelimit.get_client_ip(request)):
            messages.error(request, "You've sent several messages recently. Please try again later.")
        elif form.is_valid():
            msg = form.save(commit=False)
            msg.ip_address = ratelimit.get_client_ip(request)
            msg.save()
            notify_admins(Notification.Event.ADMIN_NOTICE, "New contact message", f"{msg.name}: {msg.subject}", "/management/messages/")
            if request.POST.get("source") == "home":
                # Sent from the homepage form: return to it and confirm in place.
                return redirect(reverse("core:home") + "?sent=1#contact")
            messages.success(request, "Thank you! Your message has been received. We'll get back to you soon.")
            return redirect("core:contact")
    return render(request, "core/contact.html", {"form": form})


@require_GET
@cache_page(60 * 60)
def robots_txt(request):
    from django.conf import settings

    lines = [
        "User-agent: *",
        "Disallow: /dashboard/",
        "Disallow: /partner/",
        "Disallow: /management/",
        "Disallow: /accounts/",
        "Disallow: /payments/",
        "Disallow: /notifications/",
        "Disallow: /login/",
        "Disallow: /register/",
        "Disallow: /*?*sort=",
        "Allow: /",
        f"Sitemap: {settings.SITE_URL}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
def healthz(request):
    """Liveness/readiness check for hosting platforms: confirms the database answers."""
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return HttpResponse("database unavailable", status=503, content_type="text/plain")
    response = HttpResponse("ok", content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def cron_scheduled_tasks(request):
    """Runs `run_scheduled_tasks` for hosts without crontab (Vercel Cron Jobs).

    Vercel sends ``Authorization: Bearer $CRON_SECRET``; without a configured
    secret the endpoint stays disabled.
    """
    import hmac
    import io
    import os

    from django.core.management import call_command

    secret = os.environ.get("CRON_SECRET", "")
    supplied = request.headers.get("Authorization", "")
    if not secret or not hmac.compare_digest(supplied.encode(), f"Bearer {secret}".encode()):
        return HttpResponse("forbidden", status=403, content_type="text/plain")
    out = io.StringIO()
    call_command("run_scheduled_tasks", stdout=out, stderr=out)
    response = HttpResponse(out.getvalue() or "done", content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_403(request, exception=None):
    return render(request, "errors/403.html", status=403)


def error_500(request):
    # Render without context processors: the database may be unavailable.
    return HttpResponseServerError(loader.get_template("errors/500.html").render())
