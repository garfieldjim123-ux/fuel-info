import copy
import hashlib
import os
import smtplib
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from fuel import TZ, advice, deliver, eligible, make_mail, render, validate

NOW = datetime(2026, 11, 9, 9, tzinfo=TZ)


def snapshot():
    source = {'url': 'https://www.ndrc.gov.cn/example', 'published_at': NOW.isoformat(),
              'quote': '合成测试证据，不是真实新闻。'}
    return {'region': '宁波', 'grade': '92', 'checked_at': NOW.isoformat(),
            'window_date': '2026-11-12', 'calendar_source': source,
            'current_price': '8.00', 'price_region': '浙江', 'price_latest_verified': True,
            'delta_per_litre': '0.30', 'delta_region': '浙江', 'delta_unit': '元/升',
            'delta_window_date': '2026-11-12', 'status': 'forecast',
            'sources': {'price': source, 'delta': source}}


class Rules(unittest.TestCase):
    def test_up(self): self.assertEqual(advice('8', '.3'), 'early')
    def test_down(self): self.assertEqual(advice('8', '-.2'), 'wait')
    def test_small(self): self.assertEqual(advice('8.85', '.2'), 'normal')
    def test_missing_delta(self): self.assertEqual(advice('8', None), 'missing')
    def test_missing_price(self): self.assertEqual(advice(None, '.3'), 'missing')
    def test_exact_threshold(self): self.assertEqual(advice('8', '.2'), 'early')
    def test_below_threshold(self): self.assertEqual(advice('8', '.1999'), 'normal')
    def test_custom_threshold(self): self.assertEqual(advice('8', '.2', '6'), 'normal')
    def test_zero(self): self.assertEqual(advice('8', '0'), 'normal')
    def test_invalid_price(self): self.assertEqual(advice('0', '.3'), 'missing')
    def test_invalid_future_price(self): self.assertEqual(advice('8', '-9'), 'missing')

    def test_timing_and_dedupe(self):
        s = {**snapshot(), 'action': 'early'}
        self.assertTrue(eligible(s, NOW))
        self.assertFalse(eligible(s, NOW - timedelta(days=1)))
        self.assertFalse(eligible(s, NOW, 'early'))
        self.assertTrue(eligible(s, NOW, 'normal'))
        self.assertFalse(eligible(s, NOW + timedelta(days=4)))

    def test_china_midnight(self):
        s = {**snapshot(), 'action': 'early'}
        from datetime import timezone
        utc = datetime(2026, 11, 8, 16, tzinfo=timezone.utc)
        self.assertTrue(eligible(s, utc))

    def test_render_conditional_and_short(self):
        s = validate(snapshot(), NOW)
        for delta, action in [(Decimal('.3'), 'early'), (Decimal('-.2'), 'wait'),
                              (Decimal('.01'), 'normal'), (None, 'missing')]:
            s.update(delta=delta, action=action)
            subject, body = render(s, NOW)
            self.assertEqual(len(body.splitlines()), 3)
            self.assertIn('以官方公布为准', body)
            self.assertNotIn('加满', body)
            if action == 'wait':
                self.assertIn('50—100', body)
                self.assertIn('近期用车需要', body)


class Data(unittest.TestCase):
    def test_valid(self): self.assertEqual(validate(snapshot(), NOW)['delta'], Decimal('.3'))

    def test_wrong_grade(self):
        s = snapshot(); s['grade'] = '95'
        with self.assertRaises(ValueError): validate(s, NOW)

    def test_stale_snapshot(self):
        with self.assertRaises(ValueError): validate(snapshot(), NOW + timedelta(hours=31))

    def test_untrusted_source(self):
        s = copy.deepcopy(snapshot()); s['sources']['delta']['url'] = 'https://ndrc.gov.cn.evil.example/x'
        with self.assertRaises(ValueError): validate(s, NOW)

    def test_generic_national_delta_not_local(self):
        s = snapshot(); s['delta_region'] = '全国'
        self.assertIsNone(validate(s, NOW)['delta'])

    def test_tonnes_not_litres(self):
        s = snapshot(); s['delta_unit'] = '元/吨'
        self.assertIsNone(validate(s, NOW)['delta'])

    def test_other_window(self):
        s = snapshot(); s['delta_window_date'] = '2026-11-26'
        self.assertIsNone(validate(s, NOW)['delta'])

    def test_old_forecast(self):
        s = copy.deepcopy(snapshot())
        s['sources']['delta'] = {**s['sources']['delta'], 'published_at': (NOW - timedelta(hours=49)).isoformat()}
        self.assertIsNone(validate(s, NOW)['delta'])

    def test_official_requires_government(self):
        s = copy.deepcopy(snapshot()); s['status'] = 'official'
        s['sources']['delta'] = {**s['sources']['delta'], 'url': 'https://www.yicai.com/x'}
        self.assertIsNone(validate(s, NOW)['delta'])

    def test_current_price_must_be_verified(self):
        s = snapshot(); s['price_latest_verified'] = False
        self.assertIsNone(validate(s, NOW)['price'])

    def test_no_window_no_guess(self):
        s = snapshot(); s['window_date'] = None
        self.assertFalse(eligible({**validate(s, NOW), 'action': 'missing'}, NOW))


class Mail(unittest.TestCase):
    def setUp(self):
        self.recipient = 'fixture@example.com'
        self.env = patch.dict(os.environ, {'SMTP_USER': self.recipient, 'SMTP_PASSWORD': 'synthetic'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.record = {'event_id': 'fixture', 'status': 'new', 'attempts': 0}
        self.saved = []

    def save(self): self.saved.append(copy.deepcopy(self.record))

    def factory(self, failures=None):
        owner = self
        class SMTP:
            def __init__(self, host, port, **kwargs):
                owner.assertEqual((host, port), ('smtp.qq.com', 465))
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def login(self, *args): pass
            def send_message(self, msg, from_addr, to_addrs):
                owner.assertEqual(to_addrs, [owner.recipient])
                owner.assertIsNone(msg['Cc']); owner.assertIsNone(msg['Bcc'])
                if failures: raise failures.pop(0)
                return {}
        return SMTP

    def test_accepted_not_inbox(self):
        result = deliver('test', 'test', self.record, self.save, self.factory(), lambda _: None)
        self.assertEqual(result, 'accepted')
        self.assertEqual(self.saved[0]['status'], 'sending')

    def test_no_retry_ambiguous(self):
        result = deliver('test', 'test', self.record, self.save,
                         self.factory([smtplib.SMTPServerDisconnected('synthetic')]), lambda _: None)
        self.assertEqual(result, 'uncertain'); self.assertEqual(self.record['attempts'], 1)

    def test_three_safe_retries(self):
        failures = [smtplib.SMTPDataError(451, b'synthetic') for _ in range(3)]
        self.assertEqual(deliver('test', 'test', self.record, self.save, self.factory(failures), lambda _: None), 'failed')
        self.assertEqual(self.record['attempts'], 3)

    def test_permanent_failure_once(self):
        self.assertEqual(deliver('test', 'test', self.record, self.save,
            self.factory([smtplib.SMTPDataError(550, b'synthetic')]), lambda _: None), 'failed')
        self.assertEqual(self.record['attempts'], 1)

    def test_failed_checkpoint_never_sends(self):
        def no_save(): raise OSError('synthetic checkpoint failure')
        with self.assertRaises(OSError):
            deliver('test', 'test', self.record, no_save, self.factory(), lambda _: None)

    def test_no_replay_after_accept(self):
        self.record['status'] = 'accepted'
        self.assertEqual(deliver('test', 'test', self.record, self.save), 'accepted')
        self.assertEqual(self.record['attempts'], 0)

    def test_reject_multiple_recipients(self):
        os.environ['SMTP_USER'] = 'one@example.com,two@example.com'
        with self.assertRaises(ValueError): make_mail('test', 'test', 'fixture')

    def test_recipient_only_from_encrypted_configuration(self):
        msg, sender, recipient = make_mail('test', 'test', 'fixture')
        self.assertEqual(recipient, os.environ['SMTP_USER'])
        self.assertEqual(sender, recipient)
        self.assertEqual(msg['To'], recipient)


if __name__ == '__main__': unittest.main()
