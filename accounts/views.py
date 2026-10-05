import time
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model, login, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.views.generic import FormView

from accounts.forms import (
    AdminLoginForm,
    EmailCodeRequestForm,
    LoginForm,
    OTPCodeForm,
    PasswordResetCodeForm,
    RegistrationForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
    ThrottledPasswordResetForm,
)
from accounts.models import Role, VerificationDocument
from accounts.otp import (
    OTPError,
    Purpose,
    admin_two_step_required,
    check_code,
    delivery_address,
    mask_email,
    otp_enabled,
    seconds_until_resend,
    send_code,
)
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
        next_url = safe_next_url(self.request, None)
        if otp_enabled():
            try:
                masked = send_code(user, Purpose.VERIFY_EMAIL)
                messages.success(self.request, _("Welcome to Bangarpet Property Hub! We've emailed a 6-digit code to %(email)s.") % {"email": masked})
            except OTPError as exc:
                messages.warning(self.request, str(exc))
            return redirect(_with_next(reverse("accounts:verify_email_code"), next_url))
        messages.success(self.request, _("Welcome to Bangarpet Property Hub!"))
        return redirect(next_url or "accounts:post_login")


@method_decorator(never_cache, name="dispatch")
class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    form_class = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        user = form.get_user()
        if admin_two_step_required(user):
            return self.start_two_step(form, user)
        response = super().form_valid(form)
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)
        log_action(self.request, "account.login", self.request.user)
        return response

    def start_two_step(self, form, user):
        """Password was right: email the admin a code and ask for it before signing in."""
        try:
            masked = send_code(user, Purpose.ADMIN_LOGIN)
        except OTPError as exc:
            if not seconds_until_resend(user, Purpose.ADMIN_LOGIN):
                form.add_error(None, str(exc))
                return self.form_invalid(form)
            masked = None  # a code was sent moments ago and is still valid
        _start_pending(self.request, Purpose.ADMIN_LOGIN, user=user, next_url=self.get_success_url(),
                       remember=form.cleaned_data.get("remember_me"))
        log_action(self.request, "account.login_code_sent", user, step="admin")
        if masked:
            messages.info(self.request, _("We've emailed a 6-digit code to %(email)s.") % {"email": masked})
        return redirect("accounts:admin_login_verify")

    def get_default_redirect_url(self):
        return str(reverse_lazy("accounts:post_login"))


class AdminLoginView(LoginView):
    """Separate sign-in for platform administrators; lands on the management panel."""

    template_name = "accounts/admin_login.html"
    form_class = AdminLoginForm

    def get_default_redirect_url(self):
        return str(reverse_lazy("adminpanel:dashboard"))


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
    if otp_enabled():
        return redirect(_with_next(reverse("accounts:verify_email_code"), safe_next_url(request, None)))
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
        email = form.cleaned_data["email"].strip().lower()
        allowed = ratelimit.check_and_hit("password_reset", f"{ratelimit.get_client_ip(self.request)}:{email}")
        if otp_enabled():
            user = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
            if allowed and user is not None and user.has_usable_password():
                try:
                    send_code(user, Purpose.PASSWORD_RESET)
                except OTPError:
                    pass  # same response either way; a recent code is still valid
            _start_pending(self.request, Purpose.PASSWORD_RESET, email=email)
            messages.info(self.request, _("If an account exists for %(email)s, we've emailed it a 6-digit code.") % {"email": email})
            return redirect("accounts:password_reset_code")
        if not allowed:
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


# ---------------------------------------------------------------------------
# One-time codes by email
# ---------------------------------------------------------------------------
PENDING_KEY = "bph_otp_pending"
PENDING_MAX_AGE = 30 * 60


def _with_next(url, next_url):
    return f"{url}?{urlencode({'next': next_url})}" if next_url else url


def _start_pending(request, purpose, *, user=None, email="", next_url="", remember=True):
    request.session[PENDING_KEY] = {
        "purpose": str(purpose), "uid": user.pk if user else None, "email": email,
        "next": next_url or "", "remember": bool(remember), "ts": int(time.time()),
    }


def _pending(request, purpose):
    data = request.session.get(PENDING_KEY)
    if not data or data.get("purpose") != purpose or time.time() - data.get("ts", 0) > PENDING_MAX_AGE:
        return None
    return data


def _pending_user(data):
    users = get_user_model().objects.filter(is_active=True)
    if data.get("uid"):
        return users.filter(pk=data["uid"]).first()
    if data.get("email"):
        return users.filter(email__iexact=data["email"]).first()
    return None


def _guess_allowed(request, form):
    if ratelimit.check_and_hit("otp_verify", ratelimit.get_client_ip(request)):
        return True
    form.add_error(None, _("Too many tries from your network. Please wait 15 minutes and try again."))
    return False


def _code_page(request, form, *, heading, intro, submit, back_url, back_label, resend_wait=0, skip_url="", skip_label=""):
    return render(request, "accounts/otp_verify.html", {
        "form": form, "heading": heading, "intro": intro, "submit_label": submit, "back_url": back_url,
        "back_label": back_label, "resend_wait": resend_wait, "skip_url": skip_url, "skip_label": skip_label,
    })


def _can_use_login_code(user):
    return user is not None and user.is_active and not user.is_platform_admin and not user.is_suspended


@login_required
@never_cache
def verify_email_code(request):
    """Confirm the account's email address with a code (offered right after sign-up)."""
    user = request.user
    next_url = safe_next_url(request, None)
    if user.email_verified:
        messages.info(request, _("Your email address is already verified."))
        return redirect(next_url or "accounts:post_login")
    if not otp_enabled():
        messages.info(request, _("Email verification is not available right now. Please try again later."))
        return redirect(next_url or "accounts:post_login")
    here = _with_next(reverse("accounts:verify_email_code"), next_url)
    form = OTPCodeForm(request.POST or None)
    if request.method == "POST" and request.POST.get("action") == "resend":
        try:
            masked = send_code(user, Purpose.VERIFY_EMAIL)
            messages.success(request, _("We've emailed a new code to %(email)s.") % {"email": masked})
        except OTPError as exc:
            messages.error(request, str(exc))
        return redirect(here)
    if request.method == "POST" and form.is_valid() and _guess_allowed(request, form):
        try:
            check_code(user, Purpose.VERIFY_EMAIL, form.cleaned_data["code"])
        except OTPError as exc:
            form.add_error("code", str(exc))
        else:
            user.email_verified = True
            user.save(update_fields=["email_verified"])
            log_action(request, "account.email_verified", user, method="code")
            messages.success(request, _("Your email address has been verified."))
            return redirect(next_url or "accounts:post_login")
    return _code_page(
        request, form,
        heading=_("Verify your email"),
        intro=_("Enter the 6-digit code we emailed to %(email)s. It is valid for 10 minutes.") % {"email": user.email},
        submit=_("Verify email"),
        resend_wait=seconds_until_resend(user, Purpose.VERIFY_EMAIL),
        skip_url=next_url or reverse("accounts:post_login"), skip_label=_("I'll do this later"),
        back_url=reverse("core:home"), back_label=_("Back to home"),
    )


@method_decorator(never_cache, name="dispatch")
class LoginCodeView(FormView):
    """Sign in without a password: ask for a code by email."""

    template_name = "accounts/login_code.html"
    form_class = EmailCodeRequestForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:post_login")
        if not otp_enabled():
            messages.info(request, _("Sign-in with an email code is not available right now. Please use your password."))
            return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["next"] = safe_next_url(self.request, None) or ""
        return ctx

    def form_valid(self, form):
        if not ratelimit.check_and_hit("otp_request", ratelimit.get_client_ip(self.request)):
            form.add_error(None, _("Too many code requests from your network. Please try again later."))
            return self.form_invalid(form)
        email = form.cleaned_data["email"]
        user = get_user_model().objects.filter(email__iexact=email).first()
        if _can_use_login_code(user):
            try:
                send_code(user, Purpose.LOGIN)
            except OTPError:
                pass  # same response either way; a recent code is still valid
        _start_pending(self.request, Purpose.LOGIN, email=email, next_url=safe_next_url(self.request, None))
        messages.info(self.request, _("If an account exists for %(email)s, we've emailed it a 6-digit code.") % {"email": email})
        return redirect("accounts:login_code_verify")


@never_cache
def login_code_verify(request):
    pending = _pending(request, Purpose.LOGIN)
    if pending is None:
        messages.info(request, _("Please ask for a new sign-in code."))
        return redirect("accounts:login_code")
    form = OTPCodeForm(request.POST or None)
    user = _pending_user(pending)
    if request.method == "POST" and request.POST.get("action") == "resend":
        if ratelimit.check_and_hit("otp_request", ratelimit.get_client_ip(request)) and _can_use_login_code(user):
            try:
                send_code(user, Purpose.LOGIN)
            except OTPError:
                pass
        messages.info(request, _("If an account exists for %(email)s, we've emailed it a new code.") % {"email": pending["email"]})
        return redirect("accounts:login_code_verify")
    if request.method == "POST" and form.is_valid() and _guess_allowed(request, form):
        try:
            if not _can_use_login_code(user):
                raise OTPError(_("That code is not correct."))
            check_code(user, Purpose.LOGIN, form.cleaned_data["code"])
        except OTPError as exc:
            form.add_error("code", str(exc))
        else:
            request.session.pop(PENDING_KEY, None)
            login(request, user, backend="accounts.backends.EmailBackend")
            if not user.email_verified:
                user.email_verified = True
                user.save(update_fields=["email_verified"])
            log_action(request, "account.login", user, method="email_code")
            return redirect(pending.get("next") or "accounts:post_login")
    return _code_page(
        request, form,
        heading=_("Enter your sign-in code"),
        intro=_("If an account exists for %(email)s, we've emailed it a 6-digit code. It is valid for 10 minutes.") % {"email": pending["email"]},
        submit=_("Sign in"),
        resend_wait=seconds_until_resend(user, Purpose.LOGIN) if user else 0,
        back_url=reverse("accounts:login"), back_label=_("Sign in with password instead"),
    )


@never_cache
def admin_login_verify(request):
    """Second step of the admin sign-in: the code emailed after the password was accepted."""
    pending = _pending(request, Purpose.ADMIN_LOGIN)
    user = _pending_user(pending) if pending else None
    if user is None or not user.is_platform_admin:
        request.session.pop(PENDING_KEY, None)
        messages.info(request, _("Please sign in again."))
        return redirect("accounts:admin_login")
    form = OTPCodeForm(request.POST or None)
    if request.method == "POST" and request.POST.get("action") == "resend":
        try:
            masked = send_code(user, Purpose.ADMIN_LOGIN)
            messages.success(request, _("We've emailed a new code to %(email)s.") % {"email": masked})
        except OTPError as exc:
            messages.error(request, str(exc))
        return redirect("accounts:admin_login_verify")
    if request.method == "POST" and form.is_valid() and _guess_allowed(request, form):
        try:
            check_code(user, Purpose.ADMIN_LOGIN, form.cleaned_data["code"])
        except OTPError as exc:
            form.add_error("code", str(exc))
        else:
            request.session.pop(PENDING_KEY, None)
            login(request, user, backend="accounts.backends.EmailBackend")
            if not pending.get("remember"):
                request.session.set_expiry(0)
            log_action(request, "account.login", user, method="password+email_code")
            return redirect(pending.get("next") or "adminpanel:dashboard")
    return _code_page(
        request, form,
        heading=_("2-step sign-in"),
        intro=_("Enter the 6-digit code we emailed to %(email)s to finish signing in.") % {"email": mask_email(delivery_address(user) or "")},
        submit=_("Verify and sign in"),
        resend_wait=seconds_until_resend(user, Purpose.ADMIN_LOGIN),
        back_url=reverse("accounts:admin_login"), back_label=_("Back to sign in"),
    )


@never_cache
def password_reset_code(request):
    """Second step of the password reset: the emailed code plus a new password."""
    pending = _pending(request, Purpose.PASSWORD_RESET)
    if pending is None:
        messages.info(request, _("Please ask for a new password reset code."))
        return redirect("accounts:password_reset")
    user = _pending_user(pending)
    form = PasswordResetCodeForm(request.POST or None, user=user)
    if request.method == "POST" and request.POST.get("action") == "resend":
        if ratelimit.check_and_hit("otp_request", ratelimit.get_client_ip(request)) and user and user.has_usable_password():
            try:
                send_code(user, Purpose.PASSWORD_RESET)
            except OTPError:
                pass
        messages.info(request, _("If an account exists for %(email)s, we've emailed it a new code.") % {"email": pending["email"]})
        return redirect("accounts:password_reset_code")
    if request.method == "POST" and form.is_valid() and _guess_allowed(request, form):
        try:
            if user is None:
                raise OTPError(_("That code is not correct."))
            check_code(user, Purpose.PASSWORD_RESET, form.cleaned_data["code"])
        except OTPError as exc:
            form.add_error("code", str(exc))
        else:
            request.session.pop(PENDING_KEY, None)
            user.set_password(form.cleaned_data["new_password1"])
            update_fields = ["password"]
            if not user.email_verified and delivery_address(user) == user.email:
                user.email_verified = True
                update_fields.append("email_verified")
            user.save(update_fields=update_fields)
            log_action(request, "account.password_reset", user, method="email_code")
            return redirect("accounts:password_reset_complete")
    return _code_page(
        request, form,
        heading=_("Choose a new password"),
        intro=_("If an account exists for %(email)s, we've emailed it a 6-digit code. Enter it below with your new password.") % {"email": pending["email"]},
        submit=_("Reset password"),
        resend_wait=seconds_until_resend(user, Purpose.PASSWORD_RESET) if user else 0,
        back_url=reverse("accounts:login"), back_label=_("Back to sign in"),
    )
