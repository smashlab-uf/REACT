from django.conf import settings
from django.test.runner import DiscoverRunner

# Bare `manage.py test` discovers from the working directory, which the Procfile
# and CI both set to backend/. Both apps now live under it, so discovery would
# find them, but naming them keeps the default suite explicit: a new top-level
# directory cannot quietly join the run, and a renamed app fails loudly here
# rather than silently dropping its tests.
DEFAULT_TEST_LABELS = ('app', 'dashboard')

# settings.py reads these from the environment, and APIKeyMiddleware enforces
# X-API-Key on every non-exempt route as soon as API_KEY is non-empty. All but
# two test classes assume the middleware is off, so a developer with API_KEY
# exported for a local server or the dress rehearsal sees ~97 tests fail on a
# tree that is fine. Pinned here rather than in test_settings.py because CI runs
# bare `manage.py test` and never loads that module, while TEST_RUNNER lives in
# base settings and so applies either way. The two classes that need a key set
# it with @override_settings, which still layers on top.
NEUTRALISED_SETTINGS = ('API_KEY', 'DASHBOARD_API_KEY')


class ReactTestRunner(DiscoverRunner):
    def build_suite(self, test_labels=None, **kwargs):
        return super().build_suite(test_labels or DEFAULT_TEST_LABELS, **kwargs)

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        for name in NEUTRALISED_SETTINGS:
            setattr(settings, name, '')
