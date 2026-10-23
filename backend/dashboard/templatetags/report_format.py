"""Display filters for the PI report templates. Every value can be None, which the
report means as "nothing to show", so each filter renders None as an em dash."""

from django import template

from dashboard.data.windows import participant_time

register = template.Library()

DASH = '—'
STATUS_SLUGS = {
    'PASS': 'pass', 'FAIL': 'fail', 'WATCH': 'watch',
    'SUPPRESSED': 'muted', 'NO DATA': 'muted', 'NOT MEASURED': 'muted',
}


@register.filter
def pct(value, places=0):
    if value is None:
        return DASH
    return f'{value * 100:.{int(places)}f}%'


@register.filter
def num(value, places=0):
    if value is None:
        return DASH
    if isinstance(value, float) and not value.is_integer():
        return f'{value:,.{int(places)}f}'
    return f'{int(value):,}'


@register.filter
def signed(value, places=2):
    if value is None:
        return DASH
    return f'{value:+.{int(places)}f}'


@register.filter
def status_slug(status):
    return STATUS_SLUGS.get(status, 'muted')


@register.filter
def eastern(moment, fmt='%Y-%m-%d %H:%M'):
    if moment is None:
        return DASH
    return participant_time(moment).strftime(fmt)


@register.filter
def yesno_dash(value):
    if value is None:
        return DASH
    return 'Yes' if value else 'No'


@register.filter
def get(mapping, key):
    if not mapping:
        return None
    return mapping.get(key)


@register.filter
def label(name):
    return str(name).replace('_', ' ')
