"""Staff accounts (admin only), the staff home page and the expense tracker."""
import csv
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role, VerificationStatus
from accounts.staff import AREAS
from adminpanel.forms import ExpenseFilterForm, ExpenseForm, StaffForm
from adminpanel.models import Expense
from core.audit import log_action
from core.permissions import admin_required, management_required
from enquiries.models import Enquiry
from moderation.models import Report
from payments.models import Payment
from properties.models import Property

User = get_user_model()


# ---------------------------------------------------------------------------
# Staff home: the landing page for staff members
# ---------------------------------------------------------------------------
AREA_LINKS = {
    "listings": [("Approvals", "adminpanel:approvals", "bi-check2-square"), ("All listings", "adminpanel:properties", "bi-buildings"),
                 ("Reports", "adminpanel:reports", "bi-flag")],
    "verifications": [("Verifications", "adminpanel:verifications", "bi-patch-check")],
    "users": [("Users", "adminpanel:users", "bi-people")],
    "enquiries": [("Enquiries", "adminpanel:enquiries", "bi-chat-left-text"), ("Contact messages", "adminpanel:messages", "bi-envelope")],
    "payments": [("Payments", "adminpanel:payments", "bi-credit-card"), ("Subscriptions", "adminpanel:subscriptions", "bi-stars")],
    "content": [("Banners, ads & videos", "adminpanel:banners", "bi-image")],
    "expenses": [("Expenses", "adminpanel:expenses", "bi-wallet2")],
}


def _pending_counts(user):
    from accounts.models import BrokerProfile, OwnerProfile

    counts = {}
    if user.can_manage("listings"):
        counts["listings"] = (Property.objects.filter(status=Property.Status.PENDING).count(), "listings waiting for approval")
    if user.can_manage("verifications"):
        pending = (OwnerProfile.objects.filter(verification_status=VerificationStatus.PENDING).count()
                   + BrokerProfile.objects.filter(verification_status=VerificationStatus.PENDING).count())
        counts["verifications"] = (pending, "partners waiting for verification")
    if user.can_manage("payments"):
        counts["payments"] = (Payment.objects.filter(status=Payment.Status.PENDING_VERIFICATION).count(), "UPI payments to check")
    if user.can_manage("enquiries"):
        counts["enquiries"] = (Enquiry.objects.filter(created_at__date=timezone.localdate()).count(), "enquiries today")
    return counts


@management_required("__any__")
def staff_home(request):
    user = request.user
    if user.is_platform_admin:
        return redirect("adminpanel:dashboard")
    counts = _pending_counts(user)
    areas = [{"key": key, "label": AREAS[key][0], "help": AREAS[key][1], "links": AREA_LINKS.get(key, []),
              "count": counts.get(key)} for key in AREAS if user.can_manage(key)]
    open_reports = Report.objects.filter(status=Report.Status.OPEN).count() if user.can_manage("listings") else None
    return render(request, "adminpanel/staff_home.html", {"active": "staff_home", "areas": areas, "open_reports": open_reports})


# ---------------------------------------------------------------------------
# Staff accounts (admins only)
# ---------------------------------------------------------------------------
@admin_required
def staff_list(request):
    staff = User.objects.filter(role=Role.STAFF).order_by("-is_active", "full_name")
    rows = [{"user": s, "areas": [AREAS[a][0] for a in AREAS if a in (s.staff_permissions or [])]} for s in staff]
    return render(request, "adminpanel/staff_list.html", {"active": "staff", "rows": rows, "areas": AREAS})


@admin_required
def staff_edit(request, pk=None):
    staff = get_object_or_404(User, pk=pk, role=Role.STAFF) if pk else None
    form = StaffForm(request.POST or None, staff=staff)
    if request.method == "POST" and form.is_valid():
        before = list(staff.staff_permissions) if staff else []
        member = form.save()
        log_action(request, "staff.updated" if staff else "staff.created", member,
                   permissions=member.staff_permissions, before=before, active=member.is_active,
                   password_changed=bool(form.cleaned_data.get("password")))
        messages.success(request, f"Staff account {'updated' if staff else 'created'} for {member.full_name}. "
                                  + ("" if staff else "They sign in at the Admin & staff sign-in page with their email."))
        return redirect("adminpanel:staff")
    return render(request, "adminpanel/staff_form.html", {"active": "staff", "form": form, "staff": staff, "areas": AREAS})


# ---------------------------------------------------------------------------
# Expense tracker
# ---------------------------------------------------------------------------
def _month_start(day):
    return day.replace(day=1)


def _next_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _parse_month(value):
    try:
        year, month = (int(x) for x in (value or "").split("-"))
        return date(year, month, 1)
    except (TypeError, ValueError):
        return None


def _total(qs):
    return qs.aggregate(t=Sum("amount"))["t"] or Decimal("0")


def _income(start, end):
    return _total(Payment.objects.filter(status=Payment.Status.PAID, paid_at__date__gte=start, paid_at__date__lt=end))


@management_required("expenses")
def expenses(request):
    form = ExpenseFilterForm(request.GET or None)
    form.is_valid()
    data = getattr(form, "cleaned_data", {}) or {}
    qs = Expense.objects.select_related("created_by")
    month = _parse_month(data.get("month"))
    if month:
        qs = qs.filter(spent_on__gte=month, spent_on__lt=_next_month(month))
    if data.get("category"):
        qs = qs.filter(category=data["category"])
    if data.get("q"):
        q = data["q"]
        qs = qs.filter(Q(description__icontains=q) | Q(paid_to__icontains=q) | Q(reference__icontains=q) | Q(notes__icontains=q))

    if request.GET.get("export") == "csv":
        log_action(request, "expenses.exported", None, rows=qs.count())
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="expenses-{timezone.localdate():%Y-%m-%d}.csv"'
        response.write("﻿")  # so Excel opens the ₹ and Kannada text correctly
        writer = csv.writer(response)
        writer.writerow(["Date", "Category", "What for", "Amount (INR)", "Paid by", "Paid to", "Reference", "Notes", "Recorded by"])
        for e in qs:
            writer.writerow([e.spent_on.isoformat(), e.get_category_display(), e.description, f"{e.amount:.2f}",
                             e.get_payment_method_display(), e.paid_to, e.reference, e.notes,
                             e.created_by.full_name if e.created_by else ""])
        return response

    today = timezone.localdate()
    this_month = _month_start(today)
    last_month = _month_start(this_month - timedelta(days=1))
    year_start = today.replace(month=4, day=1) if today.month >= 4 else date(today.year - 1, 4, 1)  # Indian financial year
    summary = {
        "this_month": _total(Expense.objects.filter(spent_on__gte=this_month)),
        "last_month": _total(Expense.objects.filter(spent_on__gte=last_month, spent_on__lt=this_month)),
        "year": _total(Expense.objects.filter(spent_on__gte=year_start)),
        "year_label": f"FY {year_start.year}-{str(year_start.year + 1)[-2:]}",
        "income_month": _income(this_month, _next_month(this_month)),
    }
    summary["profit_month"] = summary["income_month"] - summary["this_month"]
    summary["profit_abs"] = abs(summary["profit_month"])

    # Six months of income vs expenses, newest last.
    months, start = [], this_month
    for _ in range(6):
        months.insert(0, start)
        start = _month_start(start - timedelta(days=1))
    trend = []
    for m in months:
        spent = _total(Expense.objects.filter(spent_on__gte=m, spent_on__lt=_next_month(m)))
        trend.append({"month": m, "spent": spent, "income": _income(m, _next_month(m))})
    peak = max([max(t["spent"], t["income"]) for t in trend] + [Decimal("1")])
    for t in trend:
        t["spent_pct"] = int(t["spent"] * 100 / peak)
        t["income_pct"] = int(t["income"] * 100 / peak)

    filtered_total = _total(qs)
    by_category = []
    for row in qs.values("category").annotate(t=Sum("amount")).order_by("-t"):
        by_category.append({"label": Expense.Category(row["category"]).label, "total": row["t"],
                            "pct": int(row["t"] * 100 / filtered_total) if filtered_total else 0})

    from django.core.paginator import Paginator

    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "adminpanel/expenses.html", {
        "active": "expenses", "form": form, "page_obj": page, "summary": summary, "trend": trend,
        "by_category": by_category, "filtered_total": filtered_total, "filtered": bool(month or data.get("category") or data.get("q")),
        "querystring": params.urlencode(), "month_label": month,
    })


@management_required("expenses")
def expense_edit(request, pk=None):
    expense = get_object_or_404(Expense, pk=pk) if pk else None
    form = ExpenseForm(request.POST or None, request.FILES or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        if "receipt" in request.FILES:
            item.receipt_name = request.FILES["receipt"].name[:160]
        elif not item.receipt:
            item.receipt_name = ""
        if expense is None:
            item.created_by = request.user
        item.save()
        log_action(request, "expense.updated" if expense else "expense.created", item, amount=str(item.amount),
                   category=item.category)
        messages.success(request, "Expense saved.")
        if expense is None and request.POST.get("another"):
            return redirect("adminpanel:expense_add")
        return redirect("adminpanel:expenses")
    return render(request, "adminpanel/expense_form.html", {"active": "expenses", "form": form, "expense": expense})


@admin_required
@require_POST
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    log_action(request, "expense.deleted", expense, amount=str(expense.amount), description=expense.description,
               spent_on=expense.spent_on.isoformat())
    if expense.receipt:
        expense.receipt.delete(save=False)
    expense.delete()
    messages.success(request, "Expense deleted.")
    return redirect(reverse("adminpanel:expenses"))


@management_required("expenses")
def expense_receipt(request, pk):
    """Stream a private receipt to Management only."""
    expense = get_object_or_404(Expense, pk=pk)
    if not expense.receipt:
        raise Http404
    if not request.user.can_manage("expenses"):
        raise PermissionDenied
    response = FileResponse(expense.receipt.open("rb"), as_attachment=False,
                            filename=expense.receipt_name or expense.receipt.name.rsplit("/", 1)[-1])
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return response
