"""Small inline SVG charts for the PI report.

Plain strings, no plotting library, so the backend environment needs nothing new and
the HTML file stays self-contained. Colors come from CSS classes the report's
stylesheet defines (one series hue, recessive chrome), so light, dark and print
are handled in one place. Every mark carries a <title> for hover, and every chart
sits beside a table holding the same numbers.

A rate the suppression rule withholds is never drawn: the interval chart prints the
raw counts in its place, and a sparkline drops that day.
"""

from django.utils.html import escape
from django.utils.safestring import mark_safe

ROW_HEIGHT = 30
LABEL_WIDTH = 150
PLOT_WIDTH = 380
RIGHT_PAD = 70
DOT_RADIUS = 4


def _x(value, left, width):
    return left + max(0.0, min(1.0, value)) * width


def _pct(value):
    return f'{value * 100:.0f}%'


def benchmark_intervals(entries):
    rows = [entry for entry in entries if entry.get('status') != 'NOT MEASURED']
    if not rows:
        return ''
    width = LABEL_WIDTH + PLOT_WIDTH + RIGHT_PAD
    height = ROW_HEIGHT * len(rows) + 24
    left = LABEL_WIDTH
    parts = [
        f'<svg class="viz" viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="Benchmarks against target, with 95% Wilson intervals">'
    ]
    for tick in (0, 0.25, 0.5, 0.75, 1):
        x = _x(tick, left, PLOT_WIDTH)
        parts.append(f'<line class="grid" x1="{x:.1f}" y1="4" x2="{x:.1f}" y2="{height - 20}"/>')
        parts.append(f'<text class="axis" x="{x:.1f}" y="{height - 6}" text-anchor="middle">{_pct(tick)}</text>')

    for index, entry in enumerate(rows):
        y = 4 + index * ROW_HEIGHT + ROW_HEIGHT / 2
        parts.append(f'<text class="label" x="0" y="{y + 4:.1f}">{escape(entry["label"])}</text>')
        if entry.get('target') is not None:
            tx = _x(entry['target'], left, PLOT_WIDTH)
            parts.append(
                f'<line class="target" x1="{tx:.1f}" y1="{y - 9:.1f}" x2="{tx:.1f}" y2="{y + 9:.1f}">'
                f'<title>Target {_pct(entry["target"])}</title></line>')
        if entry.get('value') is None:
            counts = (f'{entry["numerator"]}/{entry["denominator"]}, rate suppressed'
                      if entry.get('denominator') else 'no data')
            parts.append(f'<text class="note" x="{left + 6}" y="{y + 4:.1f}">{escape(counts)}</text>')
            continue
        low, high = entry['wilson_low'], entry['wilson_high']
        title = (f'{entry["label"]}: {_pct(entry["value"])} '
                 f'(95% CI {_pct(low)}–{_pct(high)}; {entry["numerator"]}/{entry["denominator"]})')
        parts.append(
            f'<g><title>{escape(title)}</title>'
            f'<line class="ci" x1="{_x(low, left, PLOT_WIDTH):.1f}" y1="{y:.1f}" '
            f'x2="{_x(high, left, PLOT_WIDTH):.1f}" y2="{y:.1f}"/>'
            f'<circle class="dot" cx="{_x(entry["value"], left, PLOT_WIDTH):.1f}" cy="{y:.1f}" r="{DOT_RADIUS}"/>'
            f'<rect class="hit" x="{left}" y="{y - ROW_HEIGHT / 2:.1f}" width="{PLOT_WIDTH}" height="{ROW_HEIGHT}"/>'
            f'</g>')
        parts.append(
            f'<text class="value" x="{left + PLOT_WIDTH + 10}" y="{y + 4:.1f}">{_pct(entry["value"])}</text>')
    parts.append('</svg>')
    return mark_safe(''.join(parts))


def sparkline(points, width=120, height=28):
    """points: [(label, value or None)]. None days are gaps, never zeros."""
    values = [value for _, value in points if value is not None]
    if len(values) < 2:
        return ''
    step = (width - 2 * DOT_RADIUS) / max(1, len(points) - 1)

    def y(value):
        return height - DOT_RADIUS - value * (height - 2 * DOT_RADIUS)

    segments, current = [], []
    for index, (_, value) in enumerate(points):
        if value is None:
            if current:
                segments.append(current)
            current = []
            continue
        current.append((DOT_RADIUS + index * step, y(value)))
    if current:
        segments.append(current)

    parts = [f'<svg class="viz spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
             f'role="img" aria-label="Daily trend">']
    for segment in segments:
        if len(segment) > 1:
            path = ' '.join(f'{x:.1f},{yy:.1f}' for x, yy in segment)
            parts.append(f'<polyline class="line" points="{path}"/>')
    for index, (name, value) in enumerate(points):
        if value is None:
            continue
        cx = DOT_RADIUS + index * step
        last = index == max(i for i, (_, v) in enumerate(points) if v is not None)
        parts.append(
            f'<circle class="{"dot" if last else "hit-dot"}" cx="{cx:.1f}" cy="{y(value):.1f}" '
            f'r="{DOT_RADIUS if last else DOT_RADIUS + 2}"><title>{escape(name)}: {_pct(value)}</title></circle>')
    parts.append('</svg>')
    return mark_safe(''.join(parts))


def difference_strip(differences, unit='', label='Sent − randomized out'):
    """Per-participant A - B differences on one axis centred on zero."""
    items = [(name, value) for name, value in differences.items() if value is not None]
    if not items:
        return ''
    extent = max(abs(value) for _, value in items) or 1
    width = LABEL_WIDTH + PLOT_WIDTH + RIGHT_PAD
    height = 64
    left, axis_y = LABEL_WIDTH, 28

    def x(value):
        return left + (value + extent) / (2 * extent) * PLOT_WIDTH

    parts = [
        f'<svg class="viz" viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="Per-participant difference, {escape(label)}">',
        f'<text class="label" x="0" y="{axis_y + 4}">{escape(label)}</text>',
        f'<line class="baseline" x1="{left}" y1="{axis_y}" x2="{left + PLOT_WIDTH}" y2="{axis_y}"/>',
        f'<line class="zero" x1="{x(0):.1f}" y1="{axis_y - 14}" x2="{x(0):.1f}" y2="{axis_y + 14}"/>',
    ]
    for tick in (-extent, 0, extent):
        parts.append(f'<text class="axis" x="{x(tick):.1f}" y="{height - 6}" text-anchor="middle">'
                     f'{tick:+.2g}{escape(unit)}</text>')
    stacked = {}
    for name, value in sorted(items, key=lambda item: item[1]):
        slot = round(x(value) / (2 * DOT_RADIUS))
        count = stacked.get(slot, 0)
        stacked[slot] = count + 1
        offset = ((count + 1) // 2) * (2 * DOT_RADIUS + 1) * (1 if count % 2 else -1)
        cy = axis_y + max(-12, min(12, offset))
        parts.append(
            f'<circle class="dot" cx="{x(value):.1f}" cy="{cy}" r="{DOT_RADIUS}">'
            f'<title>{escape(name)}: {value:+.2f}{escape(unit)}</title></circle>')
    parts.append('</svg>')
    return mark_safe(''.join(parts))
