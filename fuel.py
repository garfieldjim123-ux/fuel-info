"""Fuel reminder. Standard library only; no credentials are stored in this module."""
import argparse
import base64
import hashlib
import json
import os
import smtplib
import ssl
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

TZ = timezone(timedelta(hours=8))
TRUSTED = ('gov.cn', 'news.cn', 'xinhuanet.com', 'cnr.cn', 'chinanews.com',
           'people.com.cn', 'yicai.com', 'bjnews.com.cn', 'thepaper.cn',
           'smnews.com.cn', 'nbtv.cn', 'cnnb.com.cn')


def dec(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a price')
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError('Nonfinite price')
    return number


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    return result


def evidence(item, now, max_age):
    if not isinstance(item, dict):
        return False
    parsed = urlsplit(item.get('url', ''))
    host = parsed.hostname or ''
    if parsed.scheme != 'https' or parsed.username or parsed.password:
        return False
    if not any(host == h or host.endswith('.' + h) for h in TRUSTED):
        return False
    if not item.get('quote', '').strip():
        return False
    try:
        age = now - stamp(item['published_at'])
        return -timedelta(minutes=5) <= age <= max_age
    except (ValueError, TypeError, KeyError):
        return False


def validate(raw, now):
    """Require separately grounded calendar, local price, and per-litre delta."""
    if raw.get('region') != '宁波' or raw.get('grade') != '92':
        raise ValueError('Wrong region or grade')
    age = now - stamp(raw['checked_at'])
    if not -timedelta(minutes=5) <= age <= timedelta(hours=30):
        raise ValueError('Snapshot is stale or in future')
    window = raw.get('window_date')
    if window is None:
        return {**raw, 'window_date': None, 'price': None, 'delta': None}
    day = date.fromisoformat(window)
    if not -1 <= (day - now.astimezone(TZ).date()).days <= 30:
        raise ValueError('Window outside supported range')
    if not evidence(raw.get('calendar_source'), now, timedelta(days=90)):
        raise ValueError('Missing reliable window evidence')
    sources = raw.get('sources', {})
    price = raw.get('current_price')
    if price is not None:
        price = dec(price)
        if not 0 < price < 30 or not evidence(sources.get('price'), now, timedelta(days=45)):
            price = None
        if raw.get('price_region') not in ('浙江', '宁波'):
            price = None
        if raw.get('price_latest_verified') is not True:
            price = None
    delta = raw.get('delta_per_litre')
    status = raw.get('status')
    if status not in ('forecast', 'official', 'missing'):
        raise ValueError('Invalid status')
    if delta is not None:
        delta = dec(delta)
        source = sources.get('delta')
        if abs(delta) > 5 or not evidence(source, now, timedelta(hours=48)):
            delta = None
        if raw.get('delta_region') not in ('浙江', '宁波'):
            delta = None
        if raw.get('delta_window_date') != window:
            delta = None
        if raw.get('delta_unit') != '元/升' or status == 'missing':
            delta = None
        if status == 'official':
            host = urlsplit((source or {}).get('url', '')).hostname or ''
            if not (host.endswith('.gov.cn') or host == 'gov.cn'):
                delta = None
    return {**raw, 'price': price, 'delta': delta}


def advice(price, delta, threshold=Decimal('5')):
    threshold = dec(threshold)
    if threshold <= 0:
        raise ValueError('Threshold must be positive')
    if price is None or delta is None:
        return 'missing'
    price, delta = dec(price), dec(delta)
    if price <= 0 or price + delta <= 0:
        return 'missing'
    if delta < 0:
        return 'wait'
    if delta == 0:
        return 'normal'
    return 'early' if Decimal(200) * delta / price >= threshold else 'normal'


def eligible(snapshot, now, last_action=None):
    if not snapshot.get('window_date'):
        return False
    gap = (date.fromisoformat(snapshot['window_date']) - now.astimezone(TZ).date()).days
    return 0 <= gap <= 3 and snapshot['action'] != last_action


def render(s, now):
    day = date.fromisoformat(s['window_date'])
    when = f'{day.month}月{day.day}日24时'
    delta, action = s['delta'], s['action']
    prefix = '预计' if s['status'] != 'official' else '已确认'
    if delta is None:
        change = '92号汽油调价幅度暂缺可靠数据'
    elif delta == 0:
        change = f'92号汽油{prefix}不调整（0.00元/升）'
    else:
        direction = '上涨' if delta > 0 else '下降'
        change = f'92号汽油{prefix}{direction}{abs(delta):.2f}元/升'
    if action == 'early':
        suggestion = '若近期需要加油且油箱有余量，建议生效前加200元；预计同等油量价差达到设定门槛。'
    elif action == 'wait':
        suggestion = '油量够用就等生效后加200元；不足时先加50—100元过渡，具体金额取决于近期用车需要。'
    elif action == 'normal':
        suggestion = '正常按需加200元；本次价差较小，无需专程调整安排。'
    else:
        suggestion = '正常按需加油，每次不超过平时200元；可靠数据不足，暂不能判断等待或提前是否划算。'
    link = s.get('sources', {}).get('delta') if delta is not None else s.get('calendar_source')
    url = (link or {}).get('url', '')
    qualifier = '；以官方公布为准' if s['status'] != 'official' else ''
    body = f'{change}，{when}调价。\n建议：{suggestion}\n来源：{url}｜更新：{now:%Y-%m-%d %H:%M}北京时间{qualifier}'
    return f'{change}｜{when}调价', body


class StateStore:
    """CAS writes before SMTP prevent retries from silently duplicating a delivery."""
    path = '.service/state.json'

    def __init__(self):
        self.repo = os.environ['GITHUB_REPOSITORY']
        self.token = os.environ['GITHUB_TOKEN']
        self.branch = os.environ.get('GITHUB_REF_NAME', 'main')
        self.sha = None

    def request(self, method, data=None):
        url = f'https://api.github.com/repos/{self.repo}/contents/{self.path}'
        if method == 'GET':
            url += '?ref=' + self.branch
        request = Request(url, method=method, data=None if data is None else json.dumps(data).encode(),
                          headers={'Authorization': f'Bearer {self.token}',
                                   'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28'})
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    def load(self):
        try:
            result = self.request('GET')
        except HTTPError as error:
            if error.code != 404:
                raise
            return {'windows': {}, 'test': {}}
        self.sha = result['sha']
        return json.loads(base64.b64decode(result['content']))

    def save(self, state):
        data = {'message': 'Checkpoint notification state [skip ci]', 'branch': self.branch,
                'content': base64.b64encode(json.dumps(state, ensure_ascii=False, indent=2).encode()).decode()}
        if self.sha:
            data['sha'] = self.sha
        result = self.request('PUT', data)
        self.sha = result['content']['sha']


def make_mail(subject, body, event_id):
    recipient = os.environ['SMTP_USER'].strip()
    if any(c in recipient for c in '\r\n,;') or recipient.count('@') != 1:
        raise ValueError('Exactly one recipient required')
    sender = os.environ['SMTP_USER'].strip()
    if any(c in sender for c in '\r\n,;') or sender.count('@') != 1:
        raise ValueError('Invalid sender')
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = sender, recipient, subject
    msg['Message-ID'] = f'<fuel-{event_id}@qq.com>'
    msg['Date'] = datetime.now(TZ).strftime('%a, %d %b %Y %H:%M:%S %z')
    msg.set_content(body)
    return msg, sender, recipient


def deliver(subject, body, record, persist, factory=smtplib.SMTP_SSL, sleeper=time.sleep):
    """At most 3 safe attempts; never retry an ambiguous post-DATA disconnect."""
    msg, sender, recipient = make_mail(subject, body, record['event_id'])
    if record.get('status') in ('sending', 'uncertain', 'accepted', 'failed'):
        return record['status']
    while record.get('attempts', 0) < 3:
        record['attempts'] = record.get('attempts', 0) + 1
        record['status'] = 'sending'
        persist()  # if durable checkpoint fails, do not contact SMTP
        in_data = False
        try:
            with factory('smtp.qq.com', 465, context=ssl.create_default_context(), timeout=30) as client:
                client.login(sender, os.environ['SMTP_PASSWORD'])
                in_data = True
                refused = client.send_message(msg, from_addr=sender, to_addrs=[recipient])
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
                record['status'] = 'accepted'  # This is not inbox delivery confirmation.
                persist()
                return 'accepted'
        except smtplib.SMTPResponseException as error:
            retryable = 400 <= error.smtp_code < 500
            record['status'] = 'retryable' if retryable else 'failed'
        except smtplib.SMTPRecipientsRefused:
            record['status'] = 'failed'
        except (OSError, smtplib.SMTPException):
            record['status'] = 'uncertain' if in_data else 'retryable'
        persist()
        if record['status'] != 'retryable':
            return record['status']
        if record['attempts'] < 3:
            sleeper(2 ** record['attempts'])
    record['status'] = 'failed'
    persist()
    return 'failed'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', default='data/check.json')
    parser.add_argument('--test-email', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    now = datetime.now(TZ)
    config = json.loads(Path('config.json').read_text(encoding='utf-8'))
    if args.test_email:
        subject = '【测试邮件】宁波92号汽油调价提醒｜非真实调价通知'
        body = '这是一封服务连通性测试邮件，不代表任何实际油价预测或加油建议。\n请确认是否收到。'
        key = 'test'
        action = 'test'
    else:
        s = validate(json.loads(Path(args.snapshot).read_text(encoding='utf-8')), now)
        s['action'] = advice(s['price'], s['delta'], config['significant_difference_yuan'])
        if not eligible(s, now):
            print('NO_NOTIFICATION: outside 3-natural-day window or calendar unavailable')
            return
        subject, body = render(s, now)
        key, action = s['window_date'], s['action']
    if args.dry_run:
        print(subject + '\n' + body)
        return
    required = ('SMTP_USER', 'SMTP_PASSWORD', 'GITHUB_TOKEN')
    absent = [name for name in required if not os.environ.get(name)]
    if absent:
        raise RuntimeError('Missing encrypted configuration: ' + ', '.join(absent))
    store = StateStore()
    state = store.load()
    group = state.setdefault('test', {}) if args.test_email else state.setdefault('windows', {}).setdefault(key, {})
    if group.get('last_action') == action:
        print('NO_NOTIFICATION: recommendation unchanged')
        return
    pending = group.get('pending')
    if pending and pending.get('status') in ('sending', 'uncertain'):
        raise RuntimeError('Ambiguous earlier attempt; inspect before retrying')
    seq = group.get('sequence', 0) + 1
    event_id = hashlib.sha256(f'{key}/{seq}/{action}'.encode()).hexdigest()[:32]
    if not pending or pending.get('event_id') != event_id:
        pending = {'event_id': event_id, 'attempts': 0, 'status': 'new'}
        group['pending'] = pending
    result = deliver(subject, body, pending, lambda: store.save(state))
    if result == 'accepted':
        group['last_action'] = action
        group['sequence'] = seq
        group['last_accepted_at'] = now.isoformat()
        group.pop('pending', None)
        store.save(state)
        print('SMTP_ACCEPTED: inbox delivery has NOT been confirmed')
    else:
        raise RuntimeError('Mail attempt ended with status: ' + result)


if __name__ == '__main__':
    main()
