#!/usr/bin/env python3
"""Builds orders.json for the app's "Track my order" screen.

Reads the last 90 days of online (shipping) orders from Square and publishes ONLY:
order date, status, carrier, tracking number, item count and total.
No names, emails, phone numbers or addresses are published. Each order is filed under
SHA-256 keys made from (email + postcode) and (order number + postcode), so a customer
must know both to find it.

Run by .github/workflows/orders.yml with the SQUARE_TOKEN secret.
Test offline: build_orders.py --local orders.json
"""
import json, os, sys, hashlib, datetime, urllib.request
from zoneinfo import ZoneInfo

LOC = 'LYWGATNGEFJTN'
DAYS = 90


def api(path, body):
    req = urllib.request.Request(
        'https://connect.squareup.com/v2/' + path, data=json.dumps(body).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + os.environ['SQUARE_TOKEN'],
                 'Square-Version': '2025-01-23', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch():
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS)).strftime('%Y-%m-%dT%H:%M:%SZ')
    out, cursor = [], None
    while True:
        b = {"location_ids": [LOC], "limit": 500,
             "query": {"filter": {"fulfillment_filter": {"fulfillment_types": ["SHIPMENT"]},
                                  "date_time_filter": {"created_at": {"start_at": since}}},
                       "sort": {"sort_field": "CREATED_AT", "sort_order": "DESC"}}}
        if cursor: b["cursor"] = cursor
        d = api('orders/search', b)
        out += d.get('orders', [])
        cursor = d.get('cursor')
        if not cursor: break
    return out


def key(a, pc):
    return hashlib.sha256(('pgcc|' + a.strip().lower() + '|' + pc.strip()).encode()).hexdigest()[:24]


def build(orders):
    res = {}
    for o in orders:
        if o.get('location_id') != LOC: continue
        for f in o.get('fulfillments') or []:
            if f.get('type') != 'SHIPMENT': continue
            sd = f.get('shipment_details') or {}
            rc = sd.get('recipient') or {}
            pc = ((rc.get('address') or {}).get('postal_code') or '').strip()
            em = (rc.get('email_address') or '').strip().lower()
            if not pc: continue
            fs, os_ = f.get('state'), o.get('state')
            if fs == 'CANCELED' or os_ == 'CANCELED' or sd.get('canceled_at'):
                st = 'cx'
            elif sd.get('tracking_number') or sd.get('shipped_at') or fs == 'COMPLETED':
                st = 'sh'
            elif sd.get('in_progress_at') or sd.get('packed_at') or fs in ('RESERVED', 'PREPARED'):
                st = 'pk'
            else:
                st = 'rc'
            e = {'d': (sd.get('placed_at') or o.get('created_at') or '')[:10], 'st': st,
                 'n': sum(int(float(li.get('quantity', '0'))) for li in o.get('line_items') or []),
                 'tot': round((o.get('total_money') or {}).get('amount', 0) / 100, 2)}
            if sd.get('shipped_at'): e['sd'] = sd['shipped_at'][:10]
            if sd.get('tracking_number'):
                e['t'] = sd['tracking_number'].strip()
                e['c'] = sd.get('carrier') or 'Australia Post'
            keys = set()
            if em: keys.add(key(em, pc))
            ref = (o.get('reference_id') or '').strip()
            if ref: keys.add(key(ref.lstrip('#'), pc))
            for k in keys:
                lst = res.setdefault(k, [])
                if e not in lst: lst.append(e)
    for k in res: res[k].sort(key=lambda x: x['d'], reverse=True)
    return res


def main():
    if '--local' in sys.argv:
        orders = json.load(open(sys.argv[sys.argv.index('--local') + 1]))['orders']
    else:
        orders = fetch()
    res = build(orders)
    now = datetime.datetime.now(ZoneInfo('Australia/Melbourne')).strftime('%Y-%m-%d %H:%M')
    json.dump({'v': 1, 'updated': now, 'o': res}, open('orders.json', 'w'), separators=(',', ':'))
    print('orders', len(orders), 'keys', len(res))


if __name__ == '__main__':
    main()
