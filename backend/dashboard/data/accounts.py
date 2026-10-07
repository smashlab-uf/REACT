"""Which accounts are study participants, which are test or staff, and which are unlabelled.

The study_id prefix is the only signal: RS is a participant, ST is a test or staff
account kept for system validation, and anything else (including no study_id) is
unclassified until someone labels it in Admin. Only study accounts enter the
analytic cohort; the other two are counted so the exclusion is visible.
"""

from django.db.models import Q

from app.models import User
from dashboard.data.config import STUDY_ID_PREFIX, TEST_ID_PREFIX

STUDY = 'study'
TEST = 'test'
UNCLASSIFIED = 'unclassified'
ACCOUNT_CLASSES = (STUDY, TEST, UNCLASSIFIED)


def classify(study_id):
    code = (study_id or '').strip().upper()
    if code.startswith(STUDY_ID_PREFIX):
        return STUDY
    if code.startswith(TEST_ID_PREFIX):
        return TEST
    return UNCLASSIFIED


def _base(queryset):
    return User.objects.all() if queryset is None else queryset


def study_users(queryset=None):
    return _base(queryset).filter(study_id__istartswith=STUDY_ID_PREFIX)


def test_users(queryset=None):
    return _base(queryset).filter(study_id__istartswith=TEST_ID_PREFIX)


def unclassified_users(queryset=None):
    labelled = Q(study_id__istartswith=STUDY_ID_PREFIX) | Q(study_id__istartswith=TEST_ID_PREFIX)
    return _base(queryset).exclude(labelled)


def account_counts(queryset=None):
    return {
        STUDY: study_users(queryset).count(),
        TEST: test_users(queryset).count(),
        UNCLASSIFIED: unclassified_users(queryset).count(),
    }
