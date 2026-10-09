"""Staff members: Management accounts limited to the areas an admin gives them.

Admins always have every area. Anything not listed here (settings, plans,
locations, staff accounts, audit logs, user role changes...) stays admin-only.
"""

AREAS = {
    "listings": ("Listings & reports", "Approve or reject listings, review all listings and handle reports."),
    "verifications": ("Verifications", "Check owner and broker documents and verify partners."),
    "users": ("Users (view)", "Look up user accounts. Changes to users stay with admins."),
    "enquiries": ("Enquiries & messages", "Read enquiries and contact-form messages."),
    "payments": ("Payments", "Approve UPI payments and view subscriptions and receipts."),
    "content": ("Banners, ads & videos", "Manage homepage banners, sponsored placements and YouTube videos."),
    "expenses": ("Expenses", "Record and view business expenses. Only admins can delete them."),
}
AREA_CHOICES = [(key, label) for key, (label, _help) in AREAS.items()]
