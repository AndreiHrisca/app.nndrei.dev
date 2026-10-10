"""Opening hours: "open now" and the sentence shown on the badge.

Hours are stored the way Places API (New) returns `regularOpeningHours.periods`,
so Google data and hand-typed hours share one format:

    [{"open": {"day": 1, "hour": 9, "minute": 0}, "close": {"day": 1, "hour": 14, "minute": 0}}, ...]

`day` is 0 = Sunday … 6 = Saturday. A close on the next day (or the next week,
Saturday night into Sunday) means the slot runs past midnight. A single period
with an open and no close means open 24 hours, every day.

Everything here is pure: `hours_status(periods, now)` gets the clock as an
argument, so it is tested with fixed dates and never calls anything.
"""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

MADRID = ZoneInfo('Europe/Madrid')
WEEK = 7 * 24 * 60
DAY_NAMES = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado']
# Monday first, as people read a week in Spain.
WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0]
CLOSING_SOON = 60


@dataclass(frozen=True)
class HoursStatus:
    key: str      # open | closing | closed | unknown
    text: str
    color: str    # chip colour class

    @property
    def is_open(self):
        return self.key in ('open', 'closing')


UNKNOWN = HoursStatus('unknown', 'Horario desconocido', 'gray')


def week_minute(point):
    return point['day'] * 1440 + point.get('hour', 0) * 60 + point.get('minute', 0)


def clock(minute):
    minute %= 1440
    return f'{minute // 60:02d}:{minute % 60:02d}'


def is_always_open(periods):
    return len(periods) == 1 and 'close' not in periods[0] and week_minute(periods[0]['open']) == 0


def intervals(periods):
    """Periods as sorted (start, end) minutes of the week, end > start, touching slots merged."""
    spans = []
    for period in periods:
        if 'close' not in period:
            continue
        start, end = week_minute(period['open']), week_minute(period['close'])
        if end <= start:
            end += WEEK  # Saturday night into Sunday morning.
        spans.append((start, end))
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    # A slot that runs from Saturday into Sunday may join the first Sunday slot.
    if len(merged) > 1 and merged[-1][1] >= merged[0][0] + WEEK:
        first = merged.pop(0)
        merged[-1] = (merged[-1][0], max(merged[-1][1], first[1] + WEEK))
    return merged


def hours_status(periods, now=None):
    """The badge for `periods` at `now` (any aware datetime; read in Madrid time)."""
    if not periods:
        return UNKNOWN
    if is_always_open(periods):
        return HoursStatus('open', 'Abierto 24 horas', 'green')
    spans = intervals(periods)
    if not spans:
        return UNKNOWN
    if any(end - start >= WEEK for start, end in spans):
        return HoursStatus('open', 'Abierto 24 horas', 'green')
    local = (now or datetime.now(MADRID)).astimezone(MADRID)
    today = (local.weekday() + 1) % 7  # Python: Monday = 0; Google: Sunday = 0.
    current = today * 1440 + local.hour * 60 + local.minute
    for start, end in spans:
        for shift in (0, WEEK):
            if start <= current + shift < end:
                left = end - (current + shift)
                if left < CLOSING_SOON:
                    return HoursStatus('closing', f'Cierra en {left} min', 'yellow')
                return HoursStatus('open', f'Abierto, cierra a las {clock(end)}', 'green')
    upcoming = min((start + shift for start, _ in spans for shift in (0, WEEK) if start + shift > current))
    days_ahead = upcoming // 1440 - current // 1440
    at = clock(upcoming)
    if days_ahead == 0:
        return HoursStatus('closed', f'Cerrado, abre a las {at}', 'pink')
    if days_ahead == 1:
        return HoursStatus('closed', f'Cerrado hoy, abre mañana a las {at}', 'pink')
    return HoursStatus('closed', f'Cerrado hoy, abre el {DAY_NAMES[(upcoming // 1440) % 7]} a las {at}', 'pink')


def week_summary(periods):
    """[(day name, 'HH:MM–HH:MM, HH:MM–HH:MM' | 'Cerrado'), …] from Monday, for the card."""
    if not periods:
        return []
    if is_always_open(periods):
        return [(DAY_NAMES[day].capitalize(), 'Abierto 24 horas') for day in WEEK_ORDER]
    by_day = {day: [] for day in range(7)}
    for period in sorted((p for p in periods if 'close' in p), key=lambda p: week_minute(p['open'])):
        by_day[period['open']['day']].append(f"{clock(week_minute(period['open']))}–{clock(week_minute(period['close']))}")
    return [(DAY_NAMES[day].capitalize(), ', '.join(by_day[day]) or 'Cerrado') for day in WEEK_ORDER]


def slots_by_day(periods):
    """{day: [(open 'HH:MM', close 'HH:MM'), …]} to prefill the editor."""
    by_day = {day: [] for day in range(7)}
    if periods and is_always_open(periods):
        return {day: [('00:00', '00:00')] for day in range(7)}
    for period in sorted((p for p in periods or [] if 'close' in p), key=lambda p: week_minute(p['open'])):
        by_day[period['open']['day']].append((clock(week_minute(period['open'])), clock(week_minute(period['close']))))
    return by_day


def period(day, opens, closes):
    """One slot typed by hand: 'HH:MM' strings; a close at or before the open is the next day.

    00:00–00:00 means the whole day.
    """
    oh, om = map(int, opens.split(':'))
    ch, cm = map(int, closes.split(':'))
    close_day = (day + 1) % 7 if (ch, cm) <= (oh, om) else day
    return {'open': {'day': day, 'hour': oh, 'minute': om}, 'close': {'day': close_day, 'hour': ch, 'minute': cm}}


def format_distance(metres):
    if metres is None:
        return ''
    if metres < 1000:
        return f'{int(round(metres, -1))} m'
    return f'{metres / 1000:.1f} km'.replace('.', ',')
