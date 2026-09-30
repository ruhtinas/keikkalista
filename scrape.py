#!/usr/bin/env python3
"""Keikkalista scraper: fetch upcoming events from Helsinki venues.

Usage:
    python3 scrape.py [--out docs/events.json] [--venues venues.json]

Each venue has its own parser. Failures are isolated per venue and
reported in the output JSON (venues[id].status), so one broken site
never breaks the daily update.
"""

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) keikkalista-bot/1.0 (+https://github.com/keikkalista)"
}
TIMEOUT = 30
HELSINKI = dt.timezone(dt.timedelta(hours=3))  # EEST; close enough for date logic

DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?")
# NOTE: times need ':' or 'klo'/'ovet' prefix so that dates like 2.10.
# are not misread as times.
TIME_RE = re.compile(
    r"(?:klo\s*(\d{1,2})[:.](\d{2})"
    r"|(?<!\d\.)\b(\d{1,2}):(\d{2})\b"
    r"|(?:ovet|doors)\s*(\d{1,2})[:.](\d{2}))", re.I)
DOORS_HOUR_RE = re.compile(r"(?:ovet|doors|klo)\s*(\d{1,2})(?![:.\d])", re.I)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def get(url):
    r = SESSION.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def today():
    return dt.datetime.now(HELSINKI).date()


def infer_year(day, month, ref=None):
    """Infer year for a day.month without year: nearest upcoming date."""
    ref = ref or today()
    try:
        cand = dt.date(ref.year, month, day)
    except ValueError:
        return ref.year
    if cand < ref - dt.timedelta(days=7):
        # already passed -> must be next year (handles Dec/Jan boundary)
        try:
            return ref.year + 1
        except ValueError:
            return ref.year
    return ref.year


def parse_fi_date(text, ref=None):
    """Parse first Finnish date (d.m[.yyyy]) in text. Returns date or None."""
    m = DATE_RE.search(text or "")
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    if month < 1 or month > 12 or day < 1 or day > 31:
        return None
    year = int(m.group(3)) if m.group(3) else infer_year(day, month, ref)
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def parse_time(text):
    m = TIME_RE.search(text or "")
    if m:
        h = int(m.group(1) or m.group(3) or m.group(5))
        mi = int(m.group(2) or m.group(4) or m.group(6))
        if h <= 23 and mi <= 59:
            return f"{h:02d}:{mi:02d}"
    # fallback: doors hour without minutes ("Ovet klo 19")
    m = DOORS_HOUR_RE.search(text or "")
    if m and int(m.group(1)) <= 23:
        return f"{int(m.group(1)):02d}:00"
    return None


def ev(venue_id, title, date, url, time_str=None, ticket_url=None):
    return {
        "venue": venue_id,
        "title": re.sub(r"\s+", " ", (title or "").strip()),
        "date": date.isoformat() if date else None,
        "time": time_str,
        "url": url,
        "ticket_url": ticket_url,
    }


# ---------------- individual venue parsers ----------------

def parse_kulttuuritalo(v):
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].rstrip("/")
        if not re.match(r"^https?://kulttuuritalo\.fi/tapahtuma/[^/]+$", href):
            continue
        if href in seen:
            continue
        seen.add(href)
        card = a.find_parent("div")
        card_text = card.get_text(" | ", strip=True) if card else a.get_text(" ", strip=True)
        parts = [p.strip() for p in card_text.split("|")]
        title = parts[0] if parts else a.get_text(strip=True)
        date = parse_fi_date(card_text)
        if title and date:
            out.append(ev(v["id"], title, date, href))
    return out


TAVASTIA_EV_RE = re.compile(r"/events/(\d{4})-(\d{2})-(\d{2})/[^/]+/\d+/?$")
TAVASTIA_PREFIX = re.compile(
    r"^(LIPUT MYYNNISSÄ NYT!|LOPPUUNMYYTY|TULOSSA MYYNTIIN:?)\s*", re.I)
TAVASTIA_DATEPREFIX = re.compile(
    r"^(?:ma|ti|ke|to|pe|la|su)[a-z]*\s+\d{1,2}\.\d{1,2}\.?\s+", re.I)


def parse_tavastia(v):
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = TAVASTIA_EV_RE.search(a["href"].split("?")[0])
        if not m:
            continue
        href = a["href"].split("?")[0]
        if href in seen:
            continue
        seen.add(href)
        date = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        text = a.get_text(" ", strip=True)
        text = TAVASTIA_PREFIX.sub("", text)
        text = TAVASTIA_DATEPREFIX.sub("", text).strip()
        # link text often contains the whole card ("Title ke 30.9. Liput 30€ ..."):
        # cut at the first weekday+date or ticket/doors keyword.
        cut = re.search(
            r"\s+(?:ma|ti|ke|to|pe|la|su)[a-z]*\s+\d{1,2}\.\d{1,2}\.?|\s+(?:Liput|Ovet|Show|Doors|Tickets)\b",
            text, re.I)
        title = (text[:cut.start()] if cut else text).strip()
        if not title:
            continue
        # ticket link: nearest "Osta liput" anchor in same card
        ticket = None
        card = a.find_parent(["div", "article", "li"])
        card_text = card.get_text(" ", strip=True) if card else text
        if card:
            t = card.find("a", href=lambda h: h and "tiketti" in h)
            if t:
                ticket = t["href"]
        out.append(ev(v["id"], title, date, href,
                       time_str=parse_time(card_text), ticket_url=ticket))
    return out


def parse_glivelab(v):
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    out, seen = [], set()
    items = soup.select("ul.listing > li.item")
    links = items if items else soup.find_all("a", href=re.compile(r"/events/"))
    for node in links:
        a = node if node.name == "a" else node.find("a", href=re.compile(r"/events/"))
        if not a:
            continue
        href = urljoin(v["url"], a["href"]).split("?")[0]
        if "gift-ticket" in href or href in seen:
            continue
        seen.add(href)
        title_el = (node.select_one(".title") if node.name != "a" else None)
        title = title_el.get_text(strip=True) if title_el else a.get_text(" ", strip=True).split("|")[0].strip()
        scope = node.get_text(" | ", strip=True)
        date = parse_fi_date(scope)
        time_str = parse_time(scope)
        if title and date and "lahjakortti" not in title.lower():
            out.append(ev(v["id"], title, date, href, time_str=time_str))
    return out


def parse_korjaamo(v):
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    out = []
    for card in soup.select(".gt-event-style-1"):
        link = card.find("a", href=lambda h: h and "/event/" in h)
        if not link:
            continue
        href = link["href"].split("?")[0]
        segs = [s.strip() for s in card.get_text(" | ", strip=True).split("|")]
        date = None
        title = None
        for i, s in enumerate(segs):
            if re.search(r"\d{1,2}\.\d{1,2}\.\d{4}", s):
                date = parse_fi_date(s)
                # title = previous segment that isn't just a price
                for prev in reversed(segs[:i]):
                    if prev and not re.fullmatch(r"[0-9\s/€.,-]+", prev):
                        title = prev
                        break
                break
        if title and date:
            out.append(ev(v["id"], title, date, href,
                           time_str=parse_time(" | ".join(segs))))
    return out


LEPIS_EV_RE = re.compile(r"https?://www\.(?:lepis|rocks)\.fi/tapahtumat/[^/]+/?$")
LEPIS_DATE_RE = re.compile(
    r"(?:ma|ti|ke|to|pe|la|su|maanantai|tiistai|keskiviikko|torstai|perjantai|lauantai|sunnuntai)[a-z]*\s+(\d{1,2}\.\d{1,2}\.\d{4})",
    re.I)


def parse_lepis(v):
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    out, seen = [], set()
    for art in soup.find_all("article"):
        link = art.find("a", href=lambda h: h and "/tapahtumat/" in h and h.rstrip("/") != v["url"].rstrip("/"))
        if not link:
            continue
        href = link["href"].split("?")[0]
        if href in seen:
            continue
        seen.add(href)
        text = art.get_text(" | ", strip=True)
        parts = [p.strip() for p in text.split("|")]
        # format: "Klubi | Title | ke 30.9.2026 / ovet klo 20:00 | ..."
        title = parts[1] if len(parts) > 2 else parts[0]
        date = parse_fi_date(text)
        ticket = art.find("a", href=lambda h: h and "tiketti" in h)
        if title and date:
            out.append(ev(v["id"], title, date, href,
                           time_str=parse_time(text),
                           ticket_url=ticket["href"] if ticket else None))
    return out


def parse_jaahalli(v):
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    links = []
    for a in soup.find_all("a", href=re.compile(r"/events/")):
        href = urljoin(v["url"], a["href"]).split("?")[0].rstrip("/")
        if href not in links:
            links.append(href)
    out = []
    for href in links:
        try:
            d = BeautifulSoup(get(href).text, "lxml")
            h1 = d.find("h1")
            title = h1.get_text(strip=True) if h1 else d.title.string
            page_text = d.get_text(" ", strip=True)
            date = parse_fi_date(page_text)
            ticket = d.find("a", href=lambda h: h and "ticketmaster" in h)
            # detail pages contain lots of unrelated times (box office hours),
            # so only trust a time that appears in the event title itself.
            if title and date:
                out.append(ev(v["id"], title, date, href,
                               time_str=parse_time(title),
                               ticket_url=ticket["href"] if ticket else None))
            time.sleep(0.3)
        except Exception as e:
            print(f"  [jaahalli] detail failed {href}: {e}", file=sys.stderr)
    return out


def parse_barlose(v):
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    out = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict) or it.get("@type") != "Event":
                continue
            try:
                start = dt.datetime.fromisoformat(it["startDate"])
                date = start.date()
                time_str = start.strftime("%H:%M")
            except (KeyError, ValueError):
                continue
            title = BeautifulSoup(it.get("name", ""), "html.parser").get_text(strip=True)
            out.append(ev(v["id"], title, date, it.get("url") or v["url"],
                           time_str=time_str))
    return out


def parse_aaniwalli(v):
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    out = []
    for wrap in soup.select(".event-wrapper"):
        title_el = wrap.select_one(".event-title")
        pills = [p.get_text(strip=True) for p in wrap.select(".pill")]
        link = wrap.select_one("a.event")
        ticket = wrap.select_one("a.buy-button")
        if not title_el or not pills or not link:
            continue
        date = parse_fi_date(pills[0])
        if not title_el.get_text(strip=True) or not date:
            continue
        out.append(ev(v["id"], title_el.get_text(" ", strip=True), date,
                       urljoin(v["url"], link["href"]),
                       ticket_url=ticket["href"] if ticket else None))
    return out


def parse_kuudeslinja(v):
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    out = []
    for art in soup.select("article.event"):
        pvm = art.select_one(".pvm")
        title_el = art.select_one(".title")
        if not pvm or not title_el:
            continue
        date = parse_fi_date(pvm.get_text(strip=True))
        btns = art.select("a.event-button")
        ticket = btns[0]["href"] if btns else None
        if title_el.get_text(strip=True) and date:
            out.append(ev(v["id"], title_el.get_text(" ", strip=True), date,
                           time_str=parse_time(art.get_text(" ", strip=True)),
                           ticket_url=ticket,
                           url=ticket or v["url"]))
    return out


OLARI_PAIR_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})\s*\|\s*([^|]+?)\s*\|\s*Lue lisää")


def parse_olarinpanimo(v):
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    article = None
    for art in soup.find_all("article"):
        if "Tulevia tapahtumia" in art.get_text():
            article = art
            break
    if not article:
        return []
    text = article.get_text(" | ", strip=True)
    pairs = OLARI_PAIR_RE.findall(text)
    links = [a["href"].split("?")[0] for a in article.find_all("a", href=re.compile(r"/tapahtuma/"))]
    # links repeat (title + "Lue lisää"); keep order, dedupe consecutively
    deduped = []
    for h in links:
        if not deduped or deduped[-1] != h:
            deduped.append(h)
    out = []
    for i, (dstr, tstr, title) in enumerate(pairs):
        try:
            date = dt.datetime.strptime(dstr, "%d.%m.%Y").date()
        except ValueError:
            continue
        url = deduped[i] if i < len(deduped) else v["url"]
        out.append(ev(v["id"], title, date, url, time_str=tstr))
    return out


MUSIIKKITALO_CATS = [
    "klassinen-musiikki", "pop-ja-rock", "viihdemusiikki", "jazz-ja-blues",
    "kansanmusiikki-ja-global-music", "muut-tapahtumat", "esittelykierrokset",
]


def _extract_calendar_json(html):
    """Extract the eventCalendarParams object from page HTML."""
    i = html.find("const eventCalendarParams =")
    if i < 0:
        return None
    i += len("const eventCalendarParams =")
    depth, instr, esc, start = 0, False, False, None
    for k in range(i, len(html)):
        ch = html[k]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        elif ch == '"':
            instr = True
        elif ch == "{":
            if depth == 0:
                start = k
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html[start:k + 1])
    return None


def parse_musiikkitalo(v):
    base = v["url"].rstrip("/")
    urls = [base] + [f"https://musiikkitalo.fi/kategoria/{c}" for c in MUSIIKKITALO_CATS]
    by_id = {}
    for url in urls:
        try:
            data = _extract_calendar_json(get(url).text)
            time.sleep(0.3)
        except Exception as e:
            print(f"  [musiikkitalo] {url} failed: {e}", file=sys.stderr)
            continue
        if not data:
            continue
        for month, md in (data.get("events") or {}).items():
            for day, dd in (md.get("days") or {}).items():
                try:
                    date = dt.date.fromisoformat(day)
                except ValueError:
                    continue
                for e in dd.get("events") or []:
                    eid = e.get("id", (e.get("title"), day))
                    if eid in by_id:
                        continue
                    title = (e.get("title") or "").strip()
                    if not title or not date:
                        continue
                    start = (e.get("startTime") or "").strip()
                    m = re.fullmatch(r"(\d{1,2})\.(\d{2})", start)
                    if m:
                        start = f"{int(m.group(1)):02d}:{m.group(2)}"
                    by_id[eid] = ev(v["id"], title, date,
                                     e.get("permalink") or url,
                                     time_str=start or None,
                                     ticket_url=e.get("ticketsUrl") or None)
    return list(by_id.values())


def parse_vernissa(v):
    # Vernissa has no working own calendar (tapahtumat.vantaa.fi venue page
    # is broken); use Stadissa's server-rendered venue page instead.
    soup = BeautifulSoup(get(v["url"]).text, "lxml")
    out = []
    box = soup.select_one("div.relatedEvents")
    if not box:
        return []
    for p in box.find_all("p"):
        a = p.find("a", href=True)
        if not a:
            continue
        text = p.get_text(" ", strip=True)
        date = parse_fi_date(text)
        title = a.get_text(" ", strip=True)
        if title and date:
            out.append(ev(v["id"], title, date,
                           urljoin(v["url"], a["href"]),
                           time_str=parse_time(text)))
    return out


def parse_linkedevents(v):
    events = []
    url = "https://api.hel.fi/linkedevents/v1/event/"
    params = {"location": v["place_id"], "start": "today",
              "sort": "start_time", "page_size": 100}
    while url:
        r = SESSION.get(url, params=params if "?" not in url else None,
                        timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        params = None
        for e in data.get("data", []):
            try:
                start = dt.datetime.fromisoformat(e["start_time"])
            except (KeyError, ValueError):
                continue
            name = (e.get("name") or {})
            title = name.get("fi") or name.get("en") or name.get("sv") or "?"
            eid = e.get("id", "")
            info = (e.get("info_url") or {})
            page = info.get("fi") or info.get("en") or f"https://tapahtumat.hel.fi/event/{eid}"
            events.append(ev(v["id"], title, start.date(), page,
                              time_str=start.strftime("%H:%M")))
        url = data.get("meta", {}).get("next")
    return events


PARSERS = {
    "kulttuuritalo": parse_kulttuuritalo,
    "tavastia": parse_tavastia,
    "glivelab": parse_glivelab,
    "korjaamo": parse_korjaamo,
    "lepis": parse_lepis,
    "jaahalli": parse_jaahalli,
    "barloose": parse_barlose,
    "aaniwalli": parse_aaniwalli,
    "kuudeslinja": parse_kuudeslinja,
    "olarinpanimo": parse_olarinpanimo,
    "linkedevents": parse_linkedevents,
    "musiikkitalo": parse_musiikkitalo,
    "vernissa": parse_vernissa,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venues", default="venues.json")
    ap.add_argument("--out", default="docs/events.json")
    ap.add_argument("--only", default=None, help="comma-separated venue ids")
    args = ap.parse_args()

    venues = json.loads(Path(args.venues).read_text(encoding="utf-8"))
    if args.only:
        only = set(args.only.split(","))
        venues = [x for x in venues if x["id"] in only]

    ref = today()
    all_events = []
    venue_info = {}
    for v in venues:
        vid = v["id"]
        print(f"[{vid}] {v['name']} ...", flush=True)
        try:
            if v["method"] == "manual":
                venue_info[vid] = {"name": v["name"], "url": v["url"],
                                   "count": 0, "status": "manual: " + v.get("note", "")}
                print("  manual (skipped)")
                continue
            parser = PARSERS[v["method"]]
            found = parser(v)
            # drop past events, dedupe, sort
            fresh = [e for e in found
                     if e["date"] and dt.date.fromisoformat(e["date"]) >= ref - dt.timedelta(days=1)]
            seen, uniq = set(), []
            for e in sorted(fresh, key=lambda e: (e["date"], e["title"])):
                key = (e["date"], e["title"].lower())
                if key not in seen:
                    seen.add(key)
                    uniq.append(e)
            all_events.extend(uniq)
            venue_info[vid] = {"name": v["name"], "url": v["url"],
                               "count": len(uniq), "status": "ok"}
            print(f"  {len(found)} found -> {len(uniq)} upcoming")
        except Exception as e:
            venue_info[vid] = {"name": v["name"], "url": v["url"],
                               "count": 0, "status": f"error: {e}"}
            print(f"  ERROR: {e}", file=sys.stderr)
        time.sleep(0.5)

    all_events.sort(key=lambda e: (e["date"], e["title"]))
    payload = {
        "updated": dt.datetime.now(HELSINKI).isoformat(timespec="seconds"),
        "venues": venue_info,
        "events": all_events,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nWrote {len(all_events)} events -> {out}")


if __name__ == "__main__":
    main()
