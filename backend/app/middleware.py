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
        if request.path.startswith('/dashboard/') and dashboard_key and provided_dashboard_key == dashboard_key:
            return self.get_response(request)

        return JsonResponse({'error': 'Valid X-API-Key header is required.'}, status=403)

    def _is_exempt(self, path):
        if path == '/':
            return True
        return any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)
