"""Opening hours: the pure status function, the hand editor, the list and Google data."""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock
import pytest
from django.utils import timezone
from core.hours import MADRID, format_distance, hours_status, period, week_summary
from core.models import ActivityEvent, Business, PlaceSnapshot, PlacesUsage, SearchCategory, SearchSlice

# Google's day numbers: 0 = Sunday … 6 = Saturday.
SUN, MON, TUE, WED, THU, FRI, SAT = range(7)


def at(day, hour, minute=0):
    """A Madrid time on the given weekday of the week of 2026-09-28 (a Monday)."""
    base = datetime(2026, 9, 27, tzinfo=MADRID)  # Sunday
    return base + timedelta(days=day, hours=hour, minutes=minute)


def split_shift(*days):
    """10:00–14:00 and 17:00–23:30 on the given days."""
    return [p for d in days for p in (period(d, '10:00', '14:00'), period(d, '17:00', '23:30'))]


WEEKDAYS = split_shift(TUE, WED, THU, FRI, SAT, SUN)  # Closed on Mondays.


# --- Pure status -----------------------------------------------------------------

def test_open_closes_later():
    assert hours_status(WEEKDAYS, at(TUE, 19)).text == 'Abierto, cierra a las 23:30'
    assert hours_status(WEEKDAYS, at(TUE, 19)).key == 'open'


def test_closing_in_less_than_an_hour():
    state = hours_status(WEEKDAYS, at(TUE, 22, 50))
    assert state.text == 'Cierra en 40 min' and state.key == 'closing' and state.is_open


def test_exactly_one_hour_left_is_still_open():
    assert hours_status(WEEKDAYS, at(TUE, 22, 30)).text == 'Abierto, cierra a las 23:30'


def test_closed_between_slots_opens_later_today():
    state = hours_status(WEEKDAYS, at(TUE, 15))
    assert state.text == 'Cerrado, abre a las 17:00' and not state.is_open


def test_closing_time_itself_is_closed():
    assert hours_status(WEEKDAYS, at(TUE, 14)).text == 'Cerrado, abre a las 17:00'


def test_closed_day_names_the_next_opening_day():
    # Sunday night after closing: Monday is closed, so Tuesday.
    assert hours_status(WEEKDAYS, at(SUN, 23, 45)).text == 'Cerrado hoy, abre el martes a las 10:00'
    assert hours_status(WEEKDAYS, at(MON, 12)).text == 'Cerrado hoy, abre mañana a las 10:00'


def test_opens_on_tuesday_at_half_past_seven():
    hours = [period(TUE, '07:30', '13:00')]
    assert hours_status(hours, at(SAT, 12)).text == 'Cerrado hoy, abre el martes a las 07:30'


def test_after_midnight_close():
    late = [period(FRI, '20:00', '02:30')]
    assert late[0]['close']['day'] == SAT
    assert hours_status(late, at(FRI, 23)).text == 'Abierto, cierra a las 02:30'
    assert hours_status(late, at(SAT, 2)).text == 'Cierra en 30 min'
    assert hours_status(late, at(SAT, 3)).text == 'Cerrado hoy, abre el viernes a las 20:00'


def test_saturday_night_into_sunday_wraps_the_week():
    late = [period(SAT, '22:00', '03:00')]
    assert late[0]['close']['day'] == SUN
    assert hours_status(late, at(SUN, 1)).text == 'Abierto, cierra a las 03:00'
    assert hours_status(late, at(SAT, 23)).text == 'Abierto, cierra a las 03:00'


def test_touching_slots_merge_across_midnight():
    hours = [period(FRI, '18:00', '00:00'), period(SAT, '00:00', '02:00')]
    assert hours_status(hours, at(FRI, 23)).text == 'Abierto, cierra a las 02:00'


def test_always_open_google_and_manual():
    assert hours_status([{'open': {'day': 0, 'hour': 0, 'minute': 0}}], at(WED, 4)).text == 'Abierto 24 horas'
    assert hours_status([period(d, '00:00', '00:00') for d in range(7)], at(WED, 4)).text == 'Abierto 24 horas'


def test_unknown_hours():
    assert hours_status(None, at(WED, 12)).text == 'Horario desconocido'
    assert hours_status([], at(WED, 12)).key == 'unknown'


def test_reads_the_clock_in_madrid():
    utc = datetime(2026, 9, 29, 17, 0, tzinfo=dt_timezone.utc)  # 19:00 in Madrid (summer time), a Tuesday.
    assert hours_status(WEEKDAYS, utc).text == 'Abierto, cierra a las 23:30'


def test_week_summary_starts_on_monday():
    summary = week_summary(WEEKDAYS)
    assert summary[0] == ('Lunes', 'Cerrado')
    assert summary[1] == ('Martes', '10:00–14:00, 17:00–23:30')


def test_distance_formatting():
    assert format_distance(648) == '650 m' and format_distance(1234) == '1,2 km' and format_distance(None) == ''


# --- Hand editor -------------------------------------------------------------------

pytestmark_db = pytest.mark.django_db


def editor_post(**days):
    data = {f'd{d}_closed': '1' for d in range(7)}
    for day, slots in days.items():
        d = int(day[1:])
        data.pop(f'd{d}_closed')
        for i, (o, c) in enumerate(slots):
            data[f'd{d}_s{i}_open'], data[f'd{d}_s{i}_close'] = o, c
    return data


@pytest.mark.django_db
def test_editor_saves_manual_hours_with_overnight_slot(sign_in, businesses):
    b = businesses[0]
    response = sign_in('admin').post(f'/clientes/{b.public_id}/horario/', editor_post(d5=[('10:00', '14:00'), ('20:00', '02:00')]), HTTP_HX_REQUEST='true')
    assert response.status_code == 200
    b.refresh_from_db()
    assert b.hours_source == 'manual' and len(b.opening_hours) == 2
    assert b.opening_hours[1]['close'] == {'day': SAT, 'hour': 2, 'minute': 0}
    assert ActivityEvent.objects.filter(business=b, kind='hours_updated', client_visible=False).exists()


@pytest.mark.django_db
def test_editor_rejects_overlaps_and_half_slots(sign_in, businesses):
    b = businesses[0]
    c = sign_in('admin')
    body = c.post(f'/clientes/{b.public_id}/horario/', editor_post(d1=[('10:00', '15:00'), ('14:00', '18:00')]), HTTP_HX_REQUEST='true').content.decode()
    assert 'Los tramos se solapan.' in body
    body = c.post(f'/clientes/{b.public_id}/horario/', editor_post(d2=[('10:00', '')]), HTTP_HX_REQUEST='true').content.decode()
    assert 'Rellena la apertura y el cierre' in body
    b.refresh_from_db()
    assert b.opening_hours is None


@pytest.mark.django_db
def test_editor_prefills_saved_hours(sign_in, businesses):
    b = businesses[0]
    b.opening_hours = WEEKDAYS; b.hours_source = 'google'; b.save()
    body = sign_in('admin').get(f'/clientes/{b.public_id}/horario/?edit=1').content.decode()
    assert 'name="d2_s1_open" value="17:00"' in body
    assert 'name="d1_closed" value="1" data-closed-toggle checked' in body


@pytest.mark.django_db
def test_back_to_google_hours_needs_no_call(sign_in, businesses):
    b = businesses[0]
    b.google_hours = WEEKDAYS; b.opening_hours = [period(MON, '09:00', '10:00')]; b.hours_source = 'manual'; b.save()
    with mock.patch('core.places.requests') as api:
        sign_in('admin').post(f'/clientes/{b.public_id}/horario/google/', HTTP_HX_REQUEST='true')
        assert not api.method_calls
    b.refresh_from_db()
    assert b.hours_source == 'google' and b.opening_hours == WEEKDAYS


@pytest.mark.django_db
def test_card_shows_status_and_is_admin_only_to_edit(sign_in, businesses):
    b = businesses[0]
    b.opening_hours = [{'open': {'day': 0, 'hour': 0, 'minute': 0}}]; b.save()
    body = sign_in('developer').get(f'/clientes/{b.public_id}/').content.decode()
    assert 'Abierto 24 horas' in body and 'Editar horario' not in body


# --- Google data -------------------------------------------------------------------

GOOGLE_PLACE = {'id': 'g-1', 'displayName': {'text': 'Bar Uno'}, 'businessStatus': 'OPERATIONAL', 'location': {'latitude': 40.401, 'longitude': -3.701},
                'rating': 4.5, 'userRatingCount': 320, 'priceLevel': 'PRICE_LEVEL_MODERATE', 'googleMapsUri': 'https://maps.google.com/?cid=1',
                'regularOpeningHours': {'periods': WEEKDAYS}, 'utcOffsetMinutes': 120}


@pytest.fixture
def zone_category(area):
    return area, SearchCategory.objects.create(name='Bares')


def fake_search(places):
    response = mock.Mock()
    response.json.return_value = {'places': places}
    return response


@pytest.mark.django_db
def test_search_refresh_updates_business_but_not_manual_hours(settings, zone_category):
    from core.places import refresh_places
    settings.GOOGLE_PLACES_API_KEY = 'test'
    auto = Business.objects.create(name='Auto', google_place_id='g-1')
    manual_hours = [period(MON, '08:00', '09:00')]
    manual = Business.objects.create(name='Manual', google_place_id='g-2', opening_hours=manual_hours, hours_source='manual')
    other = dict(GOOGLE_PLACE, id='g-2')
    with mock.patch('core.places.requests.post', return_value=fake_search([GOOGLE_PLACE, other])) as post:
        refresh_places()
    assert 'places.regularOpeningHours' in post.call_args.kwargs['headers']['X-Goog-FieldMask']
    auto.refresh_from_db(); manual.refresh_from_db()
    assert auto.opening_hours == WEEKDAYS and auto.hours_source == 'google' and auto.latitude == 40.401 and auto.google_reviews == 320
    assert manual.opening_hours == manual_hours and manual.hours_source == 'manual' and manual.google_hours == WEEKDAYS
    assert PlaceSnapshot.objects.get(place_id='g-1').opening_hours == WEEKDAYS


@pytest.mark.django_db
def test_second_refresh_spends_nothing(settings, zone_category):
    from core.places import refresh_places
    settings.GOOGLE_PLACES_API_KEY = 'test'
    with mock.patch('core.places.requests.post', return_value=fake_search([GOOGLE_PLACE])) as post:
        refresh_places()
        refresh_places()
    assert post.call_count == 1 and PlacesUsage.objects.get().requests == 1
    # Once the pair is older than PLACES_REFRESH_MIN_DAYS it is asked again.
    SearchSlice.objects.update(fetched_at=timezone.now() - timedelta(days=settings.PLACES_REFRESH_MIN_DAYS + 1))
    with mock.patch('core.places.requests.post', return_value=fake_search([])) as post:
        refresh_places()
    assert post.call_count == 1


@pytest.mark.django_db
def test_search_page_says_what_refresh_costs(sign_in, zone_category):
    body = sign_in('admin').get('/buscar/').content.decode()
    assert 'Se consultarán 1 combinaciones' in body
    SearchSlice.objects.create(area=zone_category[0], category=zone_category[1], fetched_at=timezone.now())
    body = sign_in('admin').get('/buscar/').content.decode()
    assert 'No se gastará ninguna llamada' in body


@pytest.mark.django_db
def test_capture_carries_hours_and_location(sign_in, zone_category):
    zone, category = zone_category
    snapshot = PlaceSnapshot.objects.create(place_id='g-9', name='Bar Nueve', city=zone.city, neighbourhood=zone.neighbourhood, category=category, latitude=40.41, longitude=-3.71, opening_hours=WEEKDAYS, price_level='PRICE_LEVEL_INEXPENSIVE')
    sign_in('admin').post(f'/buscar/{snapshot.public_id}/captar/', HTTP_HX_REQUEST='true')
    b = Business.objects.get(google_place_id='g-9')
    assert b.opening_hours == WEEKDAYS and b.hours_source == 'google' and b.latitude == 40.41 and b.google_price_level == 'PRICE_LEVEL_INEXPENSIVE'
    assert b.city == zone.city and b.neighbourhood == zone.neighbourhood


@pytest.mark.django_db
def test_refresh_one_business_spends_one_call(sign_in, settings, businesses):
    settings.GOOGLE_PLACES_API_KEY = 'test'
    b = businesses[0]; b.google_place_id = 'g-1'; b.save()
    response = mock.Mock(); response.json.return_value = GOOGLE_PLACE
    with mock.patch('core.places.requests.get', return_value=response) as get:
        sign_in('admin').post(f'/clientes/{b.public_id}/actualizar-google/')
    assert get.call_args.kwargs['headers']['X-Goog-FieldMask'] == 'id,location,regularOpeningHours,utcOffsetMinutes,rating,userRatingCount,priceLevel,googleMapsUri'
    assert PlacesUsage.objects.get().requests == 1
    b.refresh_from_db()
    assert b.opening_hours == WEEKDAYS and b.google_rating == 4.5


@pytest.mark.django_db
def test_google_data_expires_after_30_days_but_manual_hours_stay(db):
    from core.tasks import expire_google_data
    old = timezone.now() - timedelta(days=31)
    google = Business.objects.create(name='G', latitude=1, longitude=2, opening_hours=WEEKDAYS, google_hours=WEEKDAYS, hours_source='google', google_rating=4, google_fetched_at=old)
    manual = Business.objects.create(name='M', latitude=1, longitude=2, opening_hours=WEEKDAYS, google_hours=WEEKDAYS, hours_source='manual', google_fetched_at=old)
    expire_google_data()
    google.refresh_from_db(); manual.refresh_from_db()
    assert google.opening_hours is None and google.latitude is None and google.google_rating is None and google.hours_source == ''
    assert manual.opening_hours == WEEKDAYS and manual.hours_source == 'manual' and manual.google_hours is None and manual.latitude is None


# --- Clients list ------------------------------------------------------------------

@pytest.fixture
def located(db):
    always = [{'open': {'day': 0, 'hour': 0, 'minute': 0}}]
    near = Business.objects.create(name='Cerca', latitude=40.4005, longitude=-3.7, opening_hours=always)
    far = Business.objects.create(name='Aaa lejos', latitude=40.45, longitude=-3.7)
    nowhere = Business.objects.create(name='Sin sitio')
    return near, far, nowhere


@pytest.mark.django_db
def test_open_now_filter(sign_in, located):
    body = sign_in('admin').get('/clientes/?abierto=1', HTTP_HX_REQUEST='true').content.decode()
    assert 'Cerca' in body and 'Aaa lejos' not in body and 'Abierto 24 horas' in body


@pytest.mark.django_db
def test_near_me_sorts_by_distance_and_keeps_position_out_of_the_url(sign_in, located):
    response = sign_in('admin').get('/clientes/?sort=cerca&lat=40.4&lng=-3.7&q=', HTTP_HX_REQUEST='true')
    body = response.content.decode()
    assert body.index('Cerca') < body.index('Aaa lejos') < body.index('Sin sitio')
    assert '60 m' in body and '5,6 km' in body
    assert 'lat' not in response['HX-Push-Url'] and 'sort=cerca' in response['HX-Push-Url']


@pytest.mark.django_db
def test_list_without_position_still_works(sign_in, located):
    response = sign_in('admin').get('/clientes/?sort=cerca', HTTP_HX_REQUEST='true')
    body = response.content.decode()
    assert response.status_code == 200 and body.index('Aaa lejos') < body.index('Cerca')
    assert 'HX-Push-Url' not in response and ' km<' not in body
