"""Unit tests for generate_service_ad.py's pure helpers.

Run from this directory:  python -m unittest test_generate_service_ad -v

Covers which broadcast gets advertised and how it's presented, without
touching the YouTube API, Chrome or Drive.
"""

import datetime
import os
import sys
import unittest
from unittest import mock

from generate_service_ad import (
    CARD_FILENAME,
    build_qr_data_uri,
    subscribe_url,
    GRACE,
    TZ,
    best_thumbnail,
    build_html,
    format_when,
    parse_api_time,
    pick_next_broadcast,
    subscriber_text,
)

NOW = datetime.datetime(2026, 9, 18, 9, 0, tzinfo=TZ)


def video(vid, start, *, ended=None, title='Sunday Worship', thumbs=None):
    details = {'scheduledStartTime': start}
    if ended:
        details['actualEndTime'] = ended
    return {
        'id': vid,
        'snippet': {'title': title,
                    'thumbnails': thumbs or {'maxres': {'url': f'https://x/{vid}.jpg'}}},
        'liveStreamingDetails': details,
    }


class ParseApiTime(unittest.TestCase):
    def test_utc_is_converted_to_church_time(self):
        # YouTube returns UTC; the card prints service times, so printing the
        # stamp as-is would advertise a 10:30am service as 5:30pm.
        dt = parse_api_time('2026-09-20T17:30:00Z')
        self.assertEqual(dt.strftime('%-I:%M %p'), '10:30 AM')
        self.assertEqual(dt.day, 20)

    def test_offset_form_is_handled(self):
        self.assertEqual(parse_api_time('2026-09-20T10:30:00-07:00').hour, 10)

    def test_junk_is_none(self):
        self.assertIsNone(parse_api_time('nope'))
        self.assertIsNone(parse_api_time(None))
        self.assertIsNone(parse_api_time(''))


class BestThumbnail(unittest.TestCase):
    def test_prefers_the_widescreen_sizes(self):
        # 'high' and below are 4:3 with the image letterboxed inside, which
        # looks like a mistake filling a 16:9 card.
        thumbs = {'high': {'url': 'h'}, 'maxres': {'url': 'm'},
                  'standard': {'url': 's'}}
        self.assertEqual(best_thumbnail(thumbs), 'm')
        del thumbs['maxres']
        self.assertEqual(best_thumbnail(thumbs), 's')

    def test_falls_back_rather_than_returning_nothing(self):
        # A broadcast whose custom thumbnail hasn't processed still gets one.
        self.assertEqual(best_thumbnail({'default': {'url': 'd'}}), 'd')

    def test_no_thumbnails_is_none(self):
        self.assertIsNone(best_thumbnail({}))
        self.assertIsNone(best_thumbnail(None))


class PickNextBroadcast(unittest.TestCase):
    def test_picks_the_soonest_upcoming(self):
        got = pick_next_broadcast([
            video('later', '2026-09-27T17:30:00Z'),
            video('sooner', '2026-09-20T17:30:00Z'),
        ], NOW)
        self.assertEqual(got['id'], 'sooner')

    def test_finished_broadcasts_are_ignored(self):
        # Last week's service has an actualEndTime; advertising it would put a
        # past date on the screens.
        got = pick_next_broadcast([
            video('done', '2026-09-20T17:30:00Z', ended='2026-09-20T19:00:00Z'),
        ], NOW)
        self.assertIsNone(got)

    def test_a_service_in_progress_still_shows(self):
        # Scheduled an hour ago, no end time: the screens should keep
        # advertising it rather than flipping to the channel card mid-worship.
        started = (NOW - datetime.timedelta(hours=1)).astimezone(datetime.timezone.utc)
        got = pick_next_broadcast([video('live', started.isoformat())], NOW)
        self.assertEqual(got['id'], 'live')

    def test_a_long_past_broadcast_is_dropped(self):
        stale = (NOW - GRACE - datetime.timedelta(hours=1)).astimezone(datetime.timezone.utc)
        self.assertIsNone(pick_next_broadcast([video('old', stale.isoformat())], NOW))

    def test_broadcasts_with_no_schedule_are_ignored(self):
        self.assertIsNone(pick_next_broadcast(
            [{'id': 'x', 'snippet': {}, 'liveStreamingDetails': {}}], NOW))

    def test_empty_playlist_is_none(self):
        self.assertIsNone(pick_next_broadcast([], NOW))
        self.assertIsNone(pick_next_broadcast(None, NOW))

    def test_carries_the_thumbnail_through(self):
        got = pick_next_broadcast([video('v', '2026-09-20T17:30:00Z')], NOW)
        self.assertEqual(got['thumbnail'], 'https://x/v.jpg')


class FormatWhen(unittest.TestCase):
    def test_today_and_tomorrow_read_naturally(self):
        self.assertTrue(format_when(NOW + datetime.timedelta(hours=2), NOW)
                        .startswith('Today at'))
        self.assertTrue(format_when(NOW + datetime.timedelta(days=1), NOW)
                        .startswith('Tomorrow at'))

    def test_further_out_names_the_day(self):
        sunday = datetime.datetime(2026, 9, 20, 10, 30, tzinfo=TZ)
        self.assertEqual(format_when(sunday, NOW), 'Sunday, September 20 at 10:30 AM')

    def test_missing_start_is_blank(self):
        self.assertEqual(format_when(None, NOW), '')


class SubscriberText(unittest.TestCase):
    def test_rounds_the_way_youtube_shows_it(self):
        self.assertEqual(subscriber_text('3410'), '3.4K subscribers')
        self.assertEqual(subscriber_text('1200000'), '1.2M subscribers')
        self.assertEqual(subscriber_text('2000'), '2K subscribers')

    def test_small_counts_and_singular(self):
        self.assertEqual(subscriber_text('980'), '980 subscribers')
        self.assertEqual(subscriber_text('1'), '1 subscriber')

    def test_junk_is_blank(self):
        self.assertEqual(subscriber_text(None), '')
        self.assertEqual(subscriber_text('lots'), '')


class Markup(unittest.TestCase):
    def test_stream_card_shows_the_thumbnail_and_time(self):
        svc = {'title': 'Sunday Worship', 'thumb_uri': 'data:image/jpeg;base64,AAA',
               'start': datetime.datetime(2026, 9, 20, 10, 30, tzinfo=TZ)}
        out = build_html(svc, None, NOW)
        self.assertIn('data:image/jpeg;base64,AAA', out)
        self.assertIn('Upcoming Service', out)
        self.assertIn('Sunday, September 20 at 10:30 AM', out)
        self.assertNotIn('class="channel"', out)

    def test_no_broadcast_falls_back_to_the_channel_card(self):
        out = build_html(None, {'title': 'First Church', 'handle': '@x',
                                'subscribers': '3.4K subscribers',
                                'avatar_uri': 'data:image/png;base64,BBB'}, NOW)
        self.assertIn('Subscribe', out)
        self.assertIn('First Church', out)
        self.assertIn('Next service to be announced', out)

    def test_a_broadcast_whose_thumbnail_failed_uses_the_channel_card(self):
        # Better a channel card than a card with a hole where the ad goes.
        svc = {'title': 'Sunday Worship', 'thumb_uri': '',
               'start': datetime.datetime(2026, 9, 20, 10, 30, tzinfo=TZ)}
        self.assertIn('Subscribe', build_html(svc, {}, NOW))

    def test_hidden_subscriber_count_is_not_printed(self):
        out = build_html(None, {'title': 'First Church', 'hidden_subs': True,
                                'subscribers': '3.4K subscribers'}, NOW)
        self.assertNotIn('3.4K', out)

    def test_channel_card_survives_a_missing_avatar(self):
        self.assertIn('avatar-blank', build_html(None, {'title': 'First Church'}, NOW))


class CanonicalCardName(unittest.TestCase):
    """cleanup_events_folder.py must never trash this card, and
    upload_queue_to_drive.py must replace it in place."""

    def setUp(self):
        workflows = os.path.abspath(os.path.join(
            os.path.dirname(__file__), os.pardir, os.pardir,
            'Worship Scripts', 'worship workflows'))
        if workflows not in sys.path:
            sys.path.insert(0, workflows)

    def test_cleanup_never_trashes_the_service_card(self):
        from cleanup_events_folder import is_protected_flyer
        self.assertTrue(is_protected_flyer(CARD_FILENAME),
                        f'cleanup_events_folder.py would auto-trash {CARD_FILENAME}')

    def test_uploader_replaces_it_in_place(self):
        from upload_queue_to_drive import is_protected_flyer
        self.assertTrue(is_protected_flyer(CARD_FILENAME),
                        f'upload_queue_to_drive.py would stack copies of {CARD_FILENAME}')

    def test_all_three_cards_have_distinct_filenames(self):
        # They share one Drive folder, so a collision would have one card
        # silently overwrite another.
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        for sub in ('Events Ad', 'Weather Ad'):
            sys.path.insert(0, os.path.join(base, sub))
        from generate_events_ad import CARD_FILENAME as events
        from generate_weather_ad import CARD_FILENAME as weather
        self.assertEqual(len({CARD_FILENAME, events, weather}), 3)


class SubscribeQr(unittest.TestCase):
    """Both cards carry a QR to the channel's subscribe dialog."""

    CH = {'handle': '@1stchurchla', 'id': 'UCabc123'}

    def test_url_opens_the_subscribe_dialog(self):
        # Without sub_confirmation=1 a scan just lands on the channel page,
        # which is the difference between a scan that subscribes and one
        # that doesn't.
        self.assertEqual(subscribe_url(self.CH),
                         'https://www.youtube.com/@1stchurchla?sub_confirmation=1')

    def test_falls_back_to_the_channel_id_without_a_handle(self):
        self.assertEqual(subscribe_url({'handle': '', 'id': 'UCabc123'}),
                         'https://www.youtube.com/channel/UCabc123?sub_confirmation=1')

    def test_no_channel_means_no_url(self):
        self.assertEqual(subscribe_url({}), '')
        self.assertEqual(subscribe_url(None), '')

    def test_qr_is_an_inline_svg_data_uri(self):
        uri = build_qr_data_uri(subscribe_url(self.CH))
        self.assertTrue(uri.startswith('data:image/svg+xml'), uri[:40])

    def test_qr_uri_is_attribute_safe(self):
        self.assertNotIn('"', build_qr_data_uri(subscribe_url(self.CH)))

    def test_no_url_means_no_qr(self):
        self.assertEqual(build_qr_data_uri(''), '')

    def test_missing_segno_degrades_instead_of_crashing(self):
        with mock.patch.dict(sys.modules, {'segno': None}):
            self.assertEqual(build_qr_data_uri(subscribe_url(self.CH)), '')

    def test_stream_card_carries_the_qr(self):
        svc = {'title': 'Sunday Worship', 'thumb_uri': 'data:image/jpeg;base64,AAA',
               'start': datetime.datetime(2026, 9, 20, 10, 30, tzinfo=TZ)}
        out = build_html(svc, self.CH, NOW)
        self.assertIn('Scan to Subscribe', out)
        self.assertIn('QR code to subscribe on YouTube', out)

    def test_channel_card_carries_the_qr(self):
        out = build_html(None, dict(self.CH, title='First Church'), NOW)
        self.assertIn('Scan to Subscribe', out)

    def test_card_renders_without_a_channel(self):
        # An API hiccup fetching the channel must cost the QR, not the card.
        svc = {'title': 'Sunday Worship', 'thumb_uri': 'data:image/jpeg;base64,AAA',
               'start': datetime.datetime(2026, 9, 20, 10, 30, tzinfo=TZ)}
        out = build_html(svc, None, NOW)
        self.assertIn('data:image/jpeg;base64,AAA', out)
        self.assertNotIn('Scan to Subscribe', out)


if __name__ == '__main__':
    unittest.main()
