from django.test.runner import DiscoverRunner

# Bare `manage.py test` discovers from the working directory, which the Procfile
# and CI both set to backend/. The dashboard app lives at the repo root, one
# level above, so it is invisible to that discovery and its tests would silently
# never run. Naming the apps explicitly is what keeps them in the default suite.
DEFAULT_TEST_LABELS = ('app', 'dashboard')


class ReactTestRunner(DiscoverRunner):
    def build_suite(self, test_labels=None, **kwargs):
        return super().build_suite(test_labels or DEFAULT_TEST_LABELS, **kwargs)
