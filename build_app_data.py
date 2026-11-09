#!/usr/bin/env python3
"""Nightly build of the PGCC Islamic Store app data from Square.

Writes (in the repo root):
  prices.json  - every in-store item by barcode/SKU, for the in-app price scanner
  data.json    - offers + new arrivals for the Home and Deals screens

Run by .github/workflows/app-data.yml with the SQUARE_TOKEN secret (read-only token).
Can also run offline for testing: build_app_data.py --local p1.json p2.json ... --rules rules.json --counts counts.json
"""
import json, os, re, sys, datetime, urllib.request

LOC = 'LYWGATNGEFJTN'
SITE = 'https://www.pgccislamicstore.com.au'
IMG = 'https://items-images-production.s3.us-west-2.amazonaws.com/files/'
NEW_DAYS = 30
NEW_MAX = 12
WELCOME = {
    "tag": "WELCOME OFFER",
    "title": "5% off your first online order",
    "text": "Use code WELCOME5 at checkout.",
    "link": SITE + "/",
    "img": "https://www.pgccislamicstore.com.au/uploads/b/4ae7d650-ceb4-11eb-9077-d1bc653220ff/icon_512x512_android_NDUzND.png?width=512",
}


def api(path, body=None):
    req = urllib.request.Request(
        'https://connect.squareup.com/v2/' + path,
        data=json.dumps(body).encode() if body is not None else None,
        method='POST' if body is not None else 'GET',
        headers={'Authorization': 'Bearer ' + os.environ['SQUARE_TOKEN'],
                 'Square-Version': '2025-01-23', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch():
    items, rel, cursor = [], {}, None
    while True:
        b = {"object_types": ["ITEM"], "limit": 1000, "include_related_objects": True}
        if cursor: b["cursor"] = cursor
        d = api('catalog/search', b)
        items += d.get('objects', [])
        for o in d.get('related_objects', []): rel[o['id']] = o
        cursor = d.get('cursor')
        if not cursor: break
    rules, cursor = [], None
    while True:
        b = {"object_types": ["PRICING_RULE", "PRODUCT_SET", "DISCOUNT"], "limit": 1000}
        if cursor: b["cursor"] = cursor
        d = api('catalog/search', b)
        rules += d.get('objects', [])
        cursor = d.get('cursor')
        if not cursor: break
    counts, cursor = {}, None
    while True:
        b = {"location_ids": [LOC], "states": ["IN_STOCK"], "limit": 1000}
        if cursor: b["cursor"] = cursor
        d = api('inventory/counts/batch-retrieve', b)
        for c in d.get('counts', []): counts[c['catalog_object_id']] = float(c.get('quantity', 0))
        cursor = d.get('cursor')
        if not cursor: break
    return items, rel, rules, counts


def here(o):
    return (not o.get('is_deleted')
            and (o.get('present_at_all_locations') or LOC in (o.get('present_at_location_ids') or []))
            and LOC not in (o.get('absent_at_location_ids') or []))


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def money(c):
    s = '%.2f' % (c / 100)
    return '$' + (s[:-3] if s.endswith('.00') else s)


def build(items, rel, rules, counts, today):
    items = [o for o in items if here(o) and not o['item_data'].get('is_archived')]
    byid = {o['id']: o for o in items}
    var2item = {}
    bycat, catname = {}, {}
    for o in items:
        for v in o['item_data'].get('variations', []): var2item[v['id']] = o['id']
        for c in o['item_data'].get('categories', []) or []:
            bycat.setdefault(c['id'], set()).add(o['id'])
    for o in list(rel.values()) + rules:
        if o.get('type') == 'CATEGORY': catname[o['id']] = o['category_data'].get('name', '')
    robj = {o['id']: o for o in rules}

    def link(o):
        it = o['item_data']
        if it.get('ecom_visibility') != 'VISIBLE': return None
        pl = (it.get('ecom_seo_data') or {}).get('permalink')
        return f"{SITE}/product/{pl}/{o['id']}" if pl else it.get('ecom_uri')

    def image(o, v=None):
        ids = ((v or {}).get('item_variation_data', {}).get('image_ids') or []) + (o['item_data'].get('image_ids') or [])
        for i in ids:
            u = rel.get(i, {}).get('image_data', {}).get('url')
            if u: return u
        return None

    def price(o):
        ps = [v['item_variation_data'].get('price_money', {}).get('amount') for v in o['item_data'].get('variations', [])
              if not v.get('is_deleted') and v['item_variation_data'].get('price_money')]
        return min(ps) if ps else None

    # ---- automatic deals ----
    deal, multi, offers = {}, {}, []
    for o in rules:
        if o.get('type') != 'PRICING_RULE' or o.get('is_deleted'): continue
        p = o['pricing_rule_data']
        if p.get('application_mode') != 'AUTOMATIC': continue
        vf, vu = p.get('valid_from_date'), p.get('valid_until_date')
        if (vf and vf > today) or (vu and vu < today): continue
        disc = robj.get(p.get('discount_id'), {}).get('discount_data', {})
        ps = robj.get(p.get('match_products_id'), {}).get('product_set_data', {})
        anyids, allids = ps.get('product_ids_any') or [], ps.get('product_ids_all') or []
        exp = []
        for i in anyids + allids:
            for x in sorted(bycat.get(i, {i})):
                if x not in exp: exp.append(x)
        if disc.get('discount_type') == 'FIXED_PERCENTAGE':
            pct = float(disc['percentage']); lab = f"{pct:g}% OFF"; val = ('pct', pct)
        elif disc.get('amount_money'):
            a = disc['amount_money']['amount']; lab = f"{money(a)} OFF"; val = ('amt', a)
        else:
            continue
        q = ps.get('quantity_exact') or ps.get('quantity_min')
        name = (p.get('name') or '').strip()
        if allids:
            for i in exp: multi[i] = 'Bundle deal: ' + (name or lab)
        elif q and int(q) > 1:
            t = f"Buy {q}, save {money(val[1])}" if val[0] == 'amt' else f"Buy {q}, {lab}"
            for i in exp: multi[i] = t
        else:
            for i in exp: deal[i] = (val, lab)
        # named deals become offers on the Home / Deals screens
        if not name: continue
        its = [byid[var2item.get(i, i)] for i in exp if var2item.get(i, i) in byid]
        its = [x for x in its if link(x)]
        if not its: continue
        first = its[0]
        cats = [c for c in anyids if c in bycat]
        lnk = f"{SITE}/shop/{slug(catname.get(cats[0], 'shop'))}/{cats[0]}" if cats else link(first)
        if allids:
            total = sum(price(x) or 0 for x in its)
            now = round(total * (1 - val[1] / 100)) if val[0] == 'pct' else total - val[1]
            offers.append({"tag": "BUNDLE & SAVE " + lab.replace(' OFF', ''),
                           "title": f"{name.split('–')[0].strip()}: was {money(total)}, now {money(now)}",
                           "text": ' + '.join(x['item_data']['name'] for x in its) + '.',
                           "link": lnk, "img": image(first) or WELCOME['img']})
        else:
            offers.append({"tag": lab, "title": name,
                           "text": ', '.join(x['item_data']['name'] for x in its[:3]) + (' and more.' if len(its) > 3 else '.') + ' Discount taken off in your cart.',
                           "link": lnk, "img": image(first) or WELCOME['img']})

    def sale_of(o, v, p):
        dl = deal.get(v['id']) or deal.get(o['id'])
        if not dl: return None, None
        (t, x), lab = dl
        return (round(p * (1 - x / 100)) if t == 'pct' else max(0, p - x)), lab

    # ---- prices.json (scanner) ----
    out = {}
    for o in items:
        it = o['item_data']
        vs = [v for v in it.get('variations', []) if not v.get('is_deleted') and here(v)]
        lk = link(o)
        for v in vs:
            vd = v['item_variation_data']; pm = vd.get('price_money')
            if not pm or vd.get('pricing_type') == 'VARIABLE_PRICING': continue
            p = pm['amount']
            name = it['name'].strip()
            vn = (vd.get('name') or '').strip()
            if len(vs) > 1 and vn and vn != 'Regular' and vn.lower() not in name.lower(): name += ' – ' + vn
            e = {'n': name, 'p': p}
            s, lab = sale_of(o, v, p)
            if s is not None: e['s'] = s; e['d'] = lab
            m = multi.get(v['id']) or multi.get(o['id'])
            if m: e['m'] = m
            u = image(o, v)
            if u: e['i'] = u.replace(IMG, '')
            if lk: e['u'] = lk.replace(SITE + '/product/', '')
            codes = set()
            for c in (vd.get('upc'), vd.get('sku')):
                c = (c or '').strip().upper().replace(' ', '')
                if not c: continue
                codes.add(c)
                if c.isdigit():
                    codes.add(c.lstrip('0') or c)
                    if len(c) == 12: codes.add('0' + c)
            for c in codes:
                cur = out.get(c)
                if cur is None: out[c] = e
                elif isinstance(cur, list):
                    if e not in cur: cur.append(e)
                elif cur != e: out[c] = [cur, e]
    prices = {'v': 1, 'updated': today, 'img': IMG, 'url': SITE + '/product/', 'items': out}

    # ---- new arrivals: newest items, on the website, in stock, with a photo ----
    cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=NEW_DAYS)).isoformat()
    cand = []
    for o in items:
        it = o['item_data']
        if (o.get('created_at') or '')[:10] < cutoff or not link(o) or not image(o): continue
        vs = [v for v in it.get('variations', []) if not v.get('is_deleted')]
        instock = any(counts.get(v['id'], 0) > 0 or not v['item_variation_data'].get('track_inventory', True) for v in vs)
        if not instock or price(o) is None: continue
        cand.append(o)
    cand.sort(key=lambda o: o.get('created_at', ''), reverse=True)
    newarr = []
    for o in cand[:NEW_MAX]:
        v = next(v for v in o['item_data']['variations'] if not v.get('is_deleted'))
        p = price(o); s, lab = sale_of(o, v, p)
        e = {"name": o['item_data']['name'], "price": round(p / 100, 2), "img": image(o), "link": link(o)}
        if lab: e['badge'] = lab
        newarr.append(e)
    data = {"updated": today, "offers": [WELCOME] + offers, "newArrivals": newarr}
    return prices, data


def main():
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=10))).date().isoformat()
    if '--local' in sys.argv:
        a = sys.argv
        files = a[a.index('--local') + 1:a.index('--rules')]
        items, rel = [], {}
        for f in files:
            d = json.load(open(f)); items += d['objects']
            for o in d.get('related_objects', []): rel[o['id']] = o
        rules = json.load(open(a[a.index('--rules') + 1]))['objects']
        counts = {}
        if '--counts' in a:
            for c in json.load(open(a[a.index('--counts') + 1])).get('counts', []):
                counts[c['catalog_object_id']] = float(c.get('quantity', 0))
        outdir = a[a.index('--out') + 1] if '--out' in a else '.'
    else:
        items, rel, rules, counts = fetch()
        outdir = '.'
    prices, data = build(items, rel, rules, counts, today)
    if len(prices['items']) < 500:
        sys.exit('Too few items from Square - not overwriting the app data')
    json.dump(prices, open(os.path.join(outdir, 'prices.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    json.dump(data, open(os.path.join(outdir, os.environ.get('DATA_FILE', 'data.json')), 'w'), ensure_ascii=False, indent=1)
    print('codes', len(prices['items']), 'offers', len(data['offers']), 'new', len(data['newArrivals']))


if __name__ == '__main__':
    main()
