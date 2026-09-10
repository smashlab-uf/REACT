from django.conf import settings
from django.http import JsonResponse


class APIKeyMiddleware:
    """Require X-API-Key for API routes when API_KEY is configured."""

    EXEMPT_PREFIXES = (
        '/admin/',
        '/swagger/',
        '/static/',
        '/favicon.ico',
    )

    # Read-only researcher surfaces, reachable with the dashboard key instead of
    # the app's X-API-Key. Both are still gated by IsAdminUserOrDashboardAPIKey.
    DASHBOARD_PREFIXES = (
        '/dashboard/',
        '/api/monitor/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        expected_key = getattr(settings, 'API_KEY', '')
        if not expected_key or self._is_exempt(request.path):
            return self.get_response(request)

        provided_key = request.headers.get('X-API-Key', '')
        if provided_key == expected_key:
            return self.get_response(request)

        # Dashboard clients may continue using the existing dashboard key.
        dashboard_key = getattr(settings, 'DASHBOARD_API_KEY', '')
        provided_dashboard_key = request.headers.get('X-Dashboard-API-Key', '')
        if (dashboard_key and provided_dashboard_key == dashboard_key
                and self._is_dashboard(request.path)):
            return self.get_response(request)

        return JsonResponse({'error': 'Valid X-API-Key header is required.'}, status=403)

    def _is_exempt(self, path):
        if path == '/':
            return True
        return any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)

    def _is_dashboard(self, path):
        return any(path.startswith(prefix) for prefix in self.DASHBOARD_PREFIXES)
