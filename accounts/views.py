from django.contrib import messages
from django.contrib.auth import get_user_model, login, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.views.generic import FormView

from accounts.forms import (
    LoginForm,
    RegistrationForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
    ThrottledPasswordResetForm,
)
from accounts.models import Role, VerificationDocument
from accounts.services import read_email_token, send_verification_email
from core import ratelimit
from core.audit import log_action
from core.utils import safe_next_url


class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = RegistrationForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:post_login")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        account_type = self.request.GET.get("type")
        if account_type in (Role.CUSTOMER, Role.OWNER, Role.BROKER):
            initial["account_type"] = account_type
        return initial

    def form_valid(self, form):
        ip = ratelimit.get_client_ip(self.request)
        if not ratelimit.check_and_hit("register", ip):
            form.add_error(None, "Too many registrations from your network. Please try again later.")
            return self.form_invalid(form)
        user = form.save()
        login(self.request, user, backend="accounts.backends.EmailBackend")
        log_action(self.request, "account.registered", user, role=user.role)
        sent = send_verification_email(user)
        msg = "Welcome to Bangarpet Property Hub! "
        msg += "We've sent a link to verify your email address." if sent else "Email verification could not be sent right now; you can resend it from your profile."
        messages.success(self.request, msg)
        return redirect("accounts:post_login")


@method_decorator(never_cache, name="dispatch")
class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    form_class = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)
        log_action(self.request, "account.login", self.request.user)
        return response

    def get_default_redirect_url(self):
        return str(reverse_lazy("accounts:post_login"))


@login_required
def post_login(request):
    """Send each role to its own dashboard after sign-in."""
    target = safe_next_url(request, None)
    if target:
        return redirect(target)
    return redirect(request.user.dashboard_url_name())


def verify_email(request, token):
    result = read_email_token(token)
    User = get_user_model()
    if not result:
        messages.error(request, "This verification link is invalid or has expired. Please request a new one.")
        return redirect("core:home")
    uid, email = result
    user = User.objects.filter(pk=uid, email__iexact=email).first()
    if not user:
        messages.error(request, "This verification link is no longer valid.")
        return redirect("core:home")
    if not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        log_action(user, "account.email_verified", user)
    messages.success(request, "Your email address has been verified.")
    return redirect("accounts:post_login" if request.user.is_authenticated else "accounts:login")


@login_required
@require_POST
def resend_verification(request):
    if request.user.email_verified:
        messages.info(request, "Your email is already verified.")
    elif not ratelimit.check_and_hit("password_reset", f"verify:{request.user.pk}"):
        messages.error(request, "Please wait before requesting another verification email.")
    elif send_verification_email(request.user):
        messages.success(request, "Verification email sent. Please check your inbox.")
    else:
        messages.error(request, "We couldn't send the email right now. Please try again later.")
    return redirect(safe_next_url(request, None) or request.user.dashboard_url_name())


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    email_template_name = "emails/password_reset.txt"
    subject_template_name = "emails/password_reset_subject.txt"
    form_class = ThrottledPasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        ident = f"{ratelimit.get_client_ip(self.request)}:{form.cleaned_data['email'].lower()}"
        if not ratelimit.check_and_hit("password_reset", ident):
            # Same response as success to avoid revealing anything; just don't send.
            return redirect(self.success_url)
        from django.conf import settings

        self.extra_email_context = {"site_url": settings.SITE_URL}
        return super().form_valid(form)


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request, "account.password_reset", form.user)
        return response


@login_required
def password_change(request):
    form = StyledPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        log_action(request, "account.password_changed", user)
        messages.success(request, "Your password has been changed.")
        return redirect("accounts:password_change")
    base = "dashboard/partner_base.html" if request.user.is_partner else "dashboard/customer_base.html"
    if request.user.is_platform_admin:
        base = "adminpanel/base.html"
    return render(request, "accounts/password_change.html", {"form": form, "base_template": base, "active": "password"})


@login_required
def verification_document(request, pk):
    """Serve a private verification document to its owner or an admin only."""
    doc = get_object_or_404(VerificationDocument, pk=pk)
    if not (request.user.is_platform_admin or doc.user_id == request.user.pk):
        raise PermissionDenied
    if not doc.file:
        raise Http404("This document has been deleted under the retention policy.")
    if request.user.is_platform_admin and doc.user_id != request.user.pk:
        log_action(request, "verification.document_viewed", doc, owner=doc.user_id)
    response = FileResponse(doc.file.open("rb"), as_attachment=False, filename=doc.original_name or doc.file.name.rsplit("/", 1)[-1])
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return response
