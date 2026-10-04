class SecurityHeadersMiddleware:
    """Adds headers that Django does not set by default."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(self), payment=(self)")
        path = request.path
        if path.startswith(("/dashboard/", "/partner/", "/management/", "/accounts/")):
            # Private pages must never be cached by shared caches or indexed.
            response["Cache-Control"] = "private, no-store"
            response.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response
