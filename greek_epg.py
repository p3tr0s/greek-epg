#!/usr/bin/env python3
"""Greek EPG scraper -> XMLTV.

Sources:
  * Digea.gr JSON API  - every DTT channel, nationwide + all 12 regional zones
  * ERT.gr program pages - ERT1/2/3, ERT News, Sports, Vouli, World, ...
  * Cosmote TV API       - ~110 pay-TV/satellite channels (Cosmote Sport, Action 24, ...)
  * Novasports.gr        - Novasports 1-6, Eurosport etc.

Usage:
  pip install requests lxml
  python3 greek_epg.py -o greece.xml            # 3 days, all regions
  python3 greek_epg.py -d 7 --regions ee,pz9    # 7 days, nationwide + Attica
  python3 greek_epg.py -o greece.xml --gz       # also writes greece.xml.gz

Channel ids look like "mega.gr" / "ert1.gr"; regional duplicates get a
"-pz9" style suffix unless the channel is nationwide (those are deduped).
"""
import argparse, gzip, re, sys, time, unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import requests
from lxml import html

TZ = ZoneInfo("Europe/Athens")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
DIGEA = "https://www.digea.gr/el/api/epg"
ERT = "https://www.ert.gr/tv/program/"
ERT_FALLBACK = ["ert1", "ert2", "ert3", "ertnews", "ert-sports-2",
                "ert-sports-3", "vouli-tv", "ert-world", "ert-world-cyprus"]

S = requests.Session()
S.headers["User-Agent"] = UA


def log(*a):
    print(*a, file=sys.stderr)


def req(method, url, retries=3, **kw):
    for i in range(retries):
        try:
            r = S.request(method, url, timeout=30, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            log(f"  retry {i+1}/{retries} {url}: {e}")
            time.sleep(2 * (i + 1))
    return None


_M = {"α":"a","β":"b","γ":"g","δ":"d","ε":"e","ζ":"z","η":"i","θ":"th","ι":"i","κ":"k",
      "λ":"l","μ":"m","ν":"n","ξ":"x","ο":"o","π":"p","ρ":"r","σ":"s","ς":"s","τ":"t",
      "υ":"y","φ":"f","χ":"ch","ψ":"ps","ω":"o"}
GREEK = {ord(k): v for k, v in _M.items()}


def slug(name):
    n = "".join(c for c in unicodedata.normalize("NFD", name.lower()) if not unicodedata.combining(c)).translate(GREEK)
    n = re.sub(r"[^a-z0-9]+", "", n)
    n = re.sub(r"(?<=.)(fhd|uhd|hd)$", "", n)
    return n or "ch"


def ts(dt):
    return dt.astimezone(TZ).strftime("%Y%m%d%H%M%S %z")


def parse_dt(raw):
    raw = raw.strip()
    for f in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
              "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            d = datetime.strptime(raw, f)
            return d.replace(tzinfo=TZ) if d.tzinfo is None else d.astimezone(TZ)
        except ValueError:
            pass
    return datetime.fromisoformat(raw).replace(tzinfo=TZ)


# ---------------------------------------------------------------- Digea
def scrape_digea(days, regions):
    chans, progs = {}, []
    r = req("POST", f"{DIGEA}/get-perioxes", data={"action": "get_perioxes", "lang": "el"})
    zones = {z["id"]: z.get("name", z["id"]) for z in r.json()} if r else {}
    r = req("POST", f"{DIGEA}/get-channels", data={"action": "get_chanels", "lang": "el"})
    if not r:
        log("Digea: channel list failed"); return chans, progs
    raw = r.json()
    log(f"Digea: {len(raw)} channels, {len(zones)} zones")

    by_id = {}
    for c in raw:
        zone = str(c.get("regional_zone_id", ""))
        if regions and zone not in regions:
            continue
        name = (c.get("name") or "").strip()
        if not name:
            continue
        cid = f"{slug(name)}.gr" + ("" if zone == "ee" else f"-{zone}")
        logo = c.get("img") or ""
        if logo and logo.startswith("/"):
            logo = "https://www.digea.gr" + logo
        by_id[str(c["id"])] = cid
        chans.setdefault(cid, {"name": name + ("" if zone == "ee" else f" ({zones.get(zone, zone)})"),
                               "logo": logo})

    today = date.today()
    for n in range(days):
        d = today + timedelta(days=n)
        r = req("POST", f"{DIGEA}/get-events",
                data={"action": "get_events", "date": f"{d.year}-{d.month}-{d.day}"})
        if not r:
            continue
        ev = r.json()
        log(f"Digea {d}: {len(ev)} events")
        for e in ev:
            cid = by_id.get(str(e.get("channel_id", "")).strip())
            if not cid or not e.get("actual_time"):
                continue
            start = parse_dt(e["actual_time"])
            stop = parse_dt(e["end_time"]) if e.get("end_time") else None
            progs.append({
                "ch": cid, "start": start, "stop": stop,
                "title": (e.get("title") or "").strip() or "Χωρίς τίτλο",
                "desc": (e.get("long_synopsis") or e.get("synopsis") or "").strip(),
            })
        time.sleep(0.5)
    return chans, progs


# ------------------------------------------------------------------ ERT
def scrape_ert():
    chans, progs = {}, []
    r = req("GET", ERT)
    slugs = []
    if r:
        for h in html.fromstring(r.content).xpath("//a/@href"):
            m = re.match(r"^(?:https?://www\.ert\.gr)?/tv/program/([^/?#]+)/?$", h)
            if m and m.group(1) != "program" and m.group(1) not in slugs:
                slugs.append(m.group(1))
    slugs = slugs or ERT_FALLBACK
    log(f"ERT: {len(slugs)} channels")
    for sl in slugs:
        r = req("GET", f"{ERT}{sl}/")
        if not r:
            continue
        doc = html.fromstring(r.content)
        h1 = (doc.xpath("string(//h1)") or "").strip()
        name = re.sub(r"^ΠΡΟΓΡΑΜΜΑ\s*", "", h1).strip() or sl.upper()
        logo = (doc.xpath("//div[contains(@class,'broadcast')]//img[contains(@src,'logo')]/@src") or [""])[0]
        if logo:
            logo = requests.compat.urljoin(r.url, logo)
        cid = f"{slug(sl)}.gr"
        chans[cid] = {"name": name, "logo": logo}
        for a in doc.xpath("//article[@data-start-time]"):
            st = a.get("data-start-time")
            if not st:
                continue
            en = a.get("data-end-time")
            title = a.xpath("string(.//strong[contains(@class,'section-title')])").strip()
            desc = " ".join(t.strip().replace("\xa0", " ") for t in
                            a.xpath(".//*[self::em or self::span][contains(@class,'fs-ms')]/text()")
                            if t.strip())
            progs.append({"ch": cid, "start": parse_dt(st), "stop": parse_dt(en) if en else None,
                          "title": title or "Χωρίς τίτλο", "desc": desc})
        time.sleep(0.5)
    return chans, progs



# ------------------------------------------------- Cosmote TV (pay TV, ~110 ch)
COSMOTE = "https://mwapi-prod.cosmotetvott.gr/api/v3.4/epg"
CH_HDR = {"referer": "https://www.cosmotetv.gr/", "Origin": "https://www.cosmotetv.gr"}


def scrape_cosmote(days):
    chans, progs = {}, []
    r = req("GET", f"{COSMOTE}/channels/all/el", headers=CH_HDR)
    if not r:
        log("Cosmote: channel list failed"); return chans, progs
    raw = r.json().get("channels", [])
    log(f"Cosmote: {len(raw)} channels")
    names = {}
    for c in raw:
        cs, name = c.get("callSign"), (c.get("title") or "").strip()
        if cs and name:
            cid = f"{slug(name)}.gr"
            names[cs] = cid
            logo = (c.get("logos") or {}).get("square") or "" if isinstance(c.get("logos"), dict) else ""
            chans.setdefault(cid, {"name": name, "logo": logo})
    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    jobs = [(cs, today + timedelta(days=n)) for n in range(days) for cs in names]

    def one(job):
        cs, d0 = job
        a, b = int(d0.timestamp()), int((d0 + timedelta(days=1)).timestamp()) - 1
        r = req("GET", f"{COSMOTE}/listings/el", headers=CH_HDR,
                params={"from": a, "to": b, "callSigns": cs, "endingIncludedInRange": "false"})
        out = []
        for ch in (r.json().get("channels", []) if r else []):
            for it in ch.get("items", []):
                if not it.get("startTime"):
                    continue
                out.append({
                    "ch": names[cs], "start": parse_dt(it["startTime"].replace("Z", "+00:00")),
                    "stop": parse_dt(it["endTime"].replace("Z", "+00:00")) if it.get("endTime") else None,
                    "title": (it.get("title") or "").strip() or "Χωρίς τίτλο",
                    "desc": (it.get("description") or "").strip(),
                    "cat": ((it.get("qoe") or {}).get("genre") or "").strip()})
        return out

    with ThreadPoolExecutor(6) as ex:  # API takes one callSign per request
        for out in ex.map(one, jobs):
            progs += out
    log(f"Cosmote: {len(progs)} programmes")
    return chans, progs


# ------------------------------------------- Novasports / Eurosport (pay TV)
def scrape_nova(days):
    chans, progs = {}, []
    today = date.today()
    for n in range(days):
        d = today + timedelta(days=n)
        r = req("GET", "https://www.novasports.gr/wp-admin/admin-ajax.php",
                params={"action": "nova_get_template", "template": "tv-program/broadcast",
                        "dt": d.isoformat()})
        if not r:
            continue
        doc = html.fromstring(r.text)
        for blk in doc.xpath("//div[.//*[contains(@class,'channel-program')] and .//*[contains(@class,'channel')]]"):
            if blk.xpath("count(.//*[contains(@class,'channel-program')])") != 1:
                continue
            name = blk.xpath("string(.//*[contains(concat(' ',normalize-space(@class),' '),' channel ')][1])").strip()
            if not name:
                continue
            cid = f"{slug(name)}.gr"
            chans.setdefault(cid, {"name": name, "logo": ""})
            day_off, prev = 0, None
            for it in blk.xpath(".//*[contains(@class,'channel-program')]/div"):
                t = it.xpath("string(.//*[contains(@class,'time')])").strip()
                if not re.match(r"^\d{1,2}:\d{2}$", t):
                    continue
                start = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ) + timedelta(days=day_off)
                if prev and start < prev["start"]:
                    start += timedelta(days=1); day_off += 1
                if prev:
                    prev["stop"] = start
                prev = {"ch": cid, "start": start, "stop": None,
                        "title": it.xpath("string(.//*[contains(@class,'title')])").strip() or "Χωρίς τίτλο",
                        "desc": it.xpath("string(.//*[contains(@class,'subtitle')])").strip()}
                progs.append(prev)
        time.sleep(0.5)
    log(f"Nova: {len(chans)} channels, {len(progs)} programmes")
    return chans, progs


# ---------------------------------------------------------------- XMLTV
def build(chans, progs):
    # dedupe + fill missing stop times from next programme
    seen, out = set(), []
    for p in sorted(progs, key=lambda p: (p["ch"], p["start"])):
        k = (p["ch"], p["start"])
        if k not in seen:
            seen.add(k); out.append(p)
    for i, p in enumerate(out):
        nxt = out[i + 1] if i + 1 < len(out) and out[i + 1]["ch"] == p["ch"] else None
        if not p["stop"] or p["stop"] <= p["start"]:
            p["stop"] = nxt["start"] if nxt else p["start"] + timedelta(minutes=30)
    used = {p["ch"] for p in out}
    x = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
         '<tv generator-info-name="greek_epg.py">']
    for cid, c in sorted(chans.items()):
        if cid not in used:
            continue
        x.append(f'<channel id="{escape(cid)}"><display-name lang="el">{escape(c["name"])}</display-name>'
                 + (f'<icon src="{escape(c["logo"])}"/>' if c["logo"] else "") + "</channel>")
    for p in out:
        x.append(f'<programme start="{ts(p["start"])}" stop="{ts(p["stop"])}" channel="{escape(p["ch"])}">'
                 f'<title lang="el">{escape(p["title"])}</title>'
                 + (f'<desc lang="el">{escape(p["desc"])}</desc>' if p["desc"] else "")
                 + (f'<category lang="el">{escape(p["cat"])}</category>' if p.get("cat") else "")
                 + "</programme>")
    x.append("</tv>")
    return "\n".join(x), len(used), len(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default="greece.xml")
    ap.add_argument("-d", "--days", type=int, default=3, help="days from Digea (default 3)")
    ap.add_argument("--regions", default="", help="Digea zone ids, e.g. ee,pz9 (default: all)")
    ap.add_argument("--no-ert", action="store_true")
    ap.add_argument("--no-digea", action="store_true")
    ap.add_argument("--no-cosmote", action="store_true")
    ap.add_argument("--no-nova", action="store_true")
    ap.add_argument("--gz", action="store_true", help="also write .gz")
    a = ap.parse_args()
    regions = {r.strip() for r in a.regions.split(",") if r.strip()}

    chans, progs = {}, []
    if not a.no_digea:
        c, p = scrape_digea(a.days, regions); chans.update(c); progs += p
    if not a.no_ert:
        c, p = scrape_ert(); chans.update(c); progs += p
    def merge(c, p):  # add only channels no earlier source already covers
        have = {x["ch"] for x in progs}
        for k, v in c.items():
            chans.setdefault(k, v)
            if not chans[k].get("logo") and v.get("logo"):
                chans[k]["logo"] = v["logo"]
        progs.extend(x for x in p if x["ch"] not in have)
    if not a.no_cosmote:
        merge(*scrape_cosmote(a.days))
    if not a.no_nova:
        merge(*scrape_nova(min(a.days, 2)))
    if not progs:
        log("No programmes scraped - aborting (keeping previous file)."); sys.exit(1)

    xml, nc, np_ = build(chans, progs)
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(xml)
    if a.gz:
        with gzip.open(a.output + ".gz", "wt", encoding="utf-8") as f:
            f.write(xml)
    log(f"Wrote {a.output}: {nc} channels, {np_} programmes")


if __name__ == "__main__":
    main()
