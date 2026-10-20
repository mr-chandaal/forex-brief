"""FX, gold and oil brief for WhatsApp (free, runs on GitHub Actions).

Two editions, both written to brief.txt (the weekly is also saved as weekly.txt):

DAILY  (Mon-Fri)  price snapshot with daily % change, AI market picture,
                  high/medium-impact events in the next 24h, last ~30h of news with links.
WEEKLY (Sunday)   week-in-review prices with weekly % change, AI week review + week-ahead
                  outlook, the FULL calendar for the coming week, the whole week's news with
                  links, and week-ahead previews.

Both editions: a Politics & geopolitics section (war, sanctions, tariffs, elections,
budgets/debt, central-bank independence, intervention), and every story rated by AI as an
expert FX trader would: 1-5 stars for market impact plus the instruments it moves. A Top-rated
section leads; within sections stories are sorted by rating; noise is dropped.

Run:  python forex_brief.py                (daily)
      python forex_brief.py --weekly       (weekly)
      add --dry-run to print without saving

Settings (GitHub Secrets / workflow env)
  GEMINI_API_KEY    free key from https://aistudio.google.com (AI sections)
  GEMINI_MODEL      optional, default gemini-2.5-flash
  CURRENCIES        calendar currencies, default USD,EUR,GBP,JPY,AUD,NZD,CAD,CHF,CNY
  CHANNEL_NAME      title at the top of the message
  NEWS_PER_SECTION  stories per section, default 6 daily / 10 weekly
  TZ_LABEL, TZ_OFFSET_MIN  time zone for event times, default NPT / 345 (UTC+5:45)
"""
import email.utils
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

WEEKLY = "--weekly" in sys.argv or os.getenv("MODE", "").lower() == "weekly"
LOCAL = timezone(timedelta(minutes=int(os.getenv("TZ_OFFSET_MIN", "345"))))
TZL = os.getenv("TZ_LABEL", "NPT")
UTC = timezone.utc
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) forex-brief/4.0"}
NEWS_WINDOW_H = 8 * 24 if WEEKLY else 30


def gnews(q, days):
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{q} when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"})


def feeds():
    d = 7 if WEEKLY else 1
    f = [
        ("https://www.fxstreet.com/rss/news", None),
        ("https://www.investing.com/rss/news_1.rss", "fx"),
        ("https://www.investing.com/rss/news_11.rss", None),
        ("https://oilprice.com/rss/main", "oil"),
        (gnews('forex OR "US dollar" OR "Federal Reserve" OR ECB OR "Bank of Japan" OR "Bank of England"', d), "fx"),
        (gnews('"gold price" OR bullion OR XAU', d), "gold"),
        (gnews('"crude oil" OR Brent OR WTI OR OPEC', d), "oil"),
        # politics & geopolitics that can move FX, gold, oil
        (gnews('(Hormuz OR Iran OR Israel OR Russia OR Ukraine OR Taiwan OR "Middle East") (oil OR markets OR dollar)', d), "politics"),
        (gnews('(sanctions OR tariffs OR "trade war" OR "trade deal") (dollar OR yuan OR oil OR markets)', d), "politics"),
        (gnews('(election OR "no-confidence" OR coalition OR protests OR "snap election") (currency OR bonds OR euro OR yen OR pound)', d), "politics"),
        (gnews('("Fed independence" OR "debt ceiling" OR "government shutdown" OR "credit rating" OR downgrade OR "budget deficit") (dollar OR bonds OR markets)', d), "politics"),
        (gnews('("currency intervention" OR "currency manipulation" OR "verbal intervention") (yen OR yuan OR franc OR won)', d), "politics"),
    ]
    if WEEKLY:
        f += [
            (gnews('"week ahead" forex OR currencies OR "dollar outlook"', 3), "ahead"),
            (gnews('"week ahead" gold OR oil OR commodities', 3), "ahead"),
            (gnews('"Commitments of Traders" OR "CFTC positioning" currencies OR gold', 7), "ahead"),
            (gnews('"next week" (election OR summit OR "tariff deadline" OR "budget vote" OR "OPEC meeting")', 4), "politics"),
        ]
    return f


SECTIONS = [
    ("fx", "💱 FX & central banks"),
    ("gold", "🥇 Gold"),
    ("oil", "🛢️ Oil & energy"),
    ("politics", "🏛️ Politics & geopolitics"),
    ("ahead", "🔭 Week-ahead previews & positioning"),
]
KEYWORDS = {
    "gold": r"\bgold\b|xau|bullion|silver|precious metal",
    "oil": r"\boil\b|crude|brent|wti|opec|\blng\b|natural gas|energy",
    "fx": r"usd|eur|gbp|jpy|aud|nzd|cad|chf|cny|yuan|yen|dollar|euro|sterling|pound|franc|"
          r"\bfed\b|federal reserve|ecb|boj|boe|rba|rbnz|boc|snb|pboc|forex|currency|treasury|yield|inflation|cpi|payroll",
}
EXCLUDE = re.compile(r"nepal|nepali|\bnpr\b|kathmandu|rastra bank", re.I)

PRICES = [  # (label, Yahoo symbol, decimals)
    ("DXY", "DX-Y.NYB", 2), ("EUR/USD", "EURUSD=X", 4), ("GBP/USD", "GBPUSD=X", 4),
    ("USD/JPY", "USDJPY=X", 2), ("AUD/USD", "AUDUSD=X", 4), ("NZD/USD", "NZDUSD=X", 4),
    ("USD/CAD", "USDCAD=X", 4), ("USD/CHF", "USDCHF=X", 4), ("EUR/JPY", "EURJPY=X", 2),
    ("GBP/JPY", "GBPJPY=X", 2), ("AUD/JPY", "AUDJPY=X", 2), ("NZD/JPY", "NZDJPY=X", 2),
    ("Gold", "GC=F", 1), ("Silver", "SI=F", 2), ("WTI", "CL=F", 2), ("Brent", "BZ=F", 2),
    ("US 10Y %", "^TNX", 3), ("S&P 500", "^GSPC", 0), ("VIX", "^VIX", 2),
]
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def fetch(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean(text, n=None):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:n].rsplit(" ", 1)[0] + "…" if n and len(text) > n else text


def parse_date(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.strip().replace(" ", "T"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


# ---------- Prices ----------
def get_prices():
    rows = []
    rng = "1mo" if WEEKLY else "5d"
    back = 6 if WEEKLY else 2  # weekly: vs close 5 sessions earlier; daily: vs previous close
    for label, sym, dp in PRICES:
        try:
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
                   f"?range={rng}&interval=1d")
            res = json.loads(fetch(url))["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            closes = [c for c in q["close"] if c is not None]
            last = res["meta"].get("regularMarketPrice") or closes[-1]
            prev = closes[-back] if len(closes) >= back else None
            chg = f"{(last - prev) / prev * 100:+.2f}%" if prev else ""
            row = f"{label}: {last:,.{dp}f} {chg}".rstrip()
            if WEEKLY:
                hi = [h for h in q.get("high", []) if h is not None][-5:]
                lo = [l for l in q.get("low", []) if l is not None][-5:]
                if hi and lo:
                    row += f" (wk range {min(lo):,.{dp}f}–{max(hi):,.{dp}f})"
            rows.append(row)
        except Exception as e:
            print(f"price failed {sym}: {e}", file=sys.stderr)
    return rows


# ---------- Calendar ----------
def get_calendar():
    wanted = {c.strip().upper() for c in
              os.getenv("CURRENCIES", "USD,EUR,GBP,JPY,AUD,NZD,CAD,CHF,CNY").split(",")}
    now = datetime.now(LOCAL)
    start, end = (now, now + timedelta(days=7)) if WEEKLY else (now - timedelta(hours=2), now + timedelta(hours=24))
    try:
        data = json.loads(fetch(CALENDAR_URL))
    except Exception as e:
        print(f"calendar failed: {e}", file=sys.stderr)
        return None
    events = []
    for ev in data:
        impact = ev.get("impact")
        if impact not in (("High", "Medium") if not WEEKLY else ("High", "Medium")):
            continue
        if (ev.get("country") or "").upper() not in wanted:
            continue
        try:
            t = datetime.fromisoformat(ev["date"]).astimezone(LOCAL)
        except Exception:
            continue
        if not (start <= t <= end):
            continue
        mark = "🔴" if impact == "High" else "🟠"
        extra = ""
        if ev.get("forecast") or ev.get("previous"):
            extra = f" (exp. {ev.get('forecast') or '–'}, prev. {ev.get('previous') or '–'})"
        events.append((t, mark, f"{t:%I:%M %p} — {ev['country']} {ev['title']}{extra}"))
    events.sort()
    if not WEEKLY:
        return [f"{m} {t:%a} {line}" for t, m, line in events]
    # weekly: group by day
    out, day = [], None
    for t, m, line in events:
        if t.date() != day:
            day = t.date()
            out.append(f"\n_{t:%A %d %b}_")
        out.append(f"{m} {line}")
    return [l for l in out]


# ---------- News ----------
def classify(title, desc):
    text = f"{title} {desc}".lower()
    for key in ("gold", "oil", "fx"):
        if re.search(KEYWORDS[key], text):
            return key
    return None


def get_news():
    cutoff = datetime.now(UTC) - timedelta(hours=NEWS_WINDOW_H)
    seen, items = set(), []
    for url, default in feeds():
        try:
            root = ET.fromstring(fetch(url))
        except Exception as e:
            print(f"feed failed {url[:70]}: {e}", file=sys.stderr)
            continue
        for it in root.iter("item"):
            title = clean(it.findtext("title"))
            link = (it.findtext("link") or "").strip()
            if not title or not link:
                continue
            source = clean(it.findtext("source")) or urllib.parse.urlparse(url).netloc.replace("www.", "")
            if source and title.endswith(" - " + source):
                title = title[: -len(source) - 3]
            d_el = it.find("description")
            desc = clean("".join(d_el.itertext()) if d_el is not None else "", 220)
            if "news.google.com" in url:
                desc = ""  # Google News descriptions only repeat the title
            if EXCLUDE.search(f"{title} {desc} {source}"):
                continue
            key = re.sub(r"\W+", "", title.lower())[:60]
            if key in seen:
                continue
            published = parse_date(it.findtext("pubDate"))
            if published and published < cutoff:
                continue
            if default in (None, "fx"):
                section = classify(title, desc) or default
            else:
                section = default
            if section is None:
                continue
            seen.add(key)
            items.append({"title": title, "link": link, "source": source, "desc": desc,
                          "section": section, "published": published or datetime.now(UTC)})
    items.sort(key=lambda x: x["published"], reverse=True)
    return items


# ---------- AI ----------
def gemini(prompt, json_mode=False, timeout=180):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    out = json.loads(fetch(url, data=json.dumps(body).encode(),
                           headers={"Content-Type": "application/json"}, timeout=timeout))
    return out["candidates"][0]["content"]["parts"][0]["text"].strip()


def rate_news(news):
    """Ask the AI, acting as an expert FX trader, to rate each story. Adds 'stars' and 'moves'."""
    if not os.getenv("GEMINI_API_KEY") or not news:
        return False
    per_sec = 25 if WEEKLY else 15
    cands = []
    for sec, _ in SECTIONS:
        cands += [x for x in news if x["section"] == sec][:per_sec]
    lines = [f'{i}|{n["section"]}|{n["title"]}' + (f' — {n["desc"]}' if n["desc"] else "")
             for i, n in enumerate(cands)]
    prompt = (
        "You are an expert FX, gold and oil trader. Rate each news item below for its likely impact on "
        "major currency pairs (USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF), gold or oil over the next "
        + ("week" if WEEKLY else "1-2 days") + ".\n"
        "Stars: 5 = can move majors/gold/oil sharply or change the market regime; 4 = significant for a "
        "specific currency or commodity; 3 = relevant context; 2 = minor; 1 = noise (opinion with no new "
        "facts, unrelated industries, local politics with no market channel).\n"
        "For political stories judge only the market channel (rates expectations, fiscal/bond risk, trade "
        "flows, energy supply, risk appetite); never express political opinions.\n"
        "'moves' = the instruments affected and direction, very short, using ↑ ↓ ↔ and codes, e.g. "
        "'Oil ↑ | JPY, EUR ↓' or 'Gold ↑ | USD ↓' (max 40 characters).\n"
        'Return ONLY a JSON array: [{"id": 0, "stars": 4, "moves": "..."}] covering every id.\n\n'
        "ITEMS (id|section|headline — summary):\n" + "\n".join(lines)
    )
    try:
        raw = gemini(prompt, json_mode=True) or ""
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
        data = json.loads(raw)
        if isinstance(data, dict):  # e.g. {"items": [...]}
            data = next((v for v in data.values() if isinstance(v, list)), [])
        by_id = {}
        for r in data:
            try:
                by_id[int(r["id"])] = r
            except Exception:
                continue
    except Exception as e:
        print(f"rating failed (news shown unrated): {e}", file=sys.stderr)
        return False
    if not by_id:
        print("rating returned nothing usable (news shown unrated)", file=sys.stderr)
        return False
    for i, n in enumerate(cands):
        r = by_id.get(i)
        if not r:
            continue
        try:
            n["stars"] = max(1, min(5, int(float(r.get("stars", 0) or 0))))
        except Exception:
            continue
        n["moves"] = clean(str(r.get("moves", "")), 60)
    return True


def ai_summary(prices, events, news):
    key = os.getenv("GEMINI_API_KEY")
    if not key or not news:
        return None
    lines = []
    for sec, name in SECTIONS:
        for n in [x for x in news if x["section"] == sec][: (15 if WEEKLY else 10)]:
            stars = f" ({n['stars']}★)" if n.get("stars") else ""
            lines.append(f"[{name}]{stars} {n['published']:%a %d %b}: {n['title']}" + (f" — {n['desc']}" if n["desc"] else ""))
    rules = ("Use ONLY the prices, events and news below. Do not invent numbers, levels, dates or events. "
             "No buy/sell recommendations. Plain text, no markdown headings; put each label on its own line "
             "followed by a colon.\n")
    if WEEKLY:
        task = (
            "You are a senior FX and commodities strategist writing the weekly edition of a WhatsApp brief for "
            "an active trader who follows CFTC COT positioning on the majors, gold and oil.\n" + rules +
            "Structure:\n"
            "Week in review – USD & majors: 3-4 sentences (dollar trend, central-bank themes, best/worst pairs using the weekly % changes).\n"
            "Week in review – Gold: 2 sentences.\n"
            "Week in review – Oil: 2 sentences.\n"
            "Week in review – Risk mood: 1-2 sentences (yields, equities, VIX).\n"
            "Week in review – Political risk: 1-2 sentences (which political/geopolitical stories moved markets and how).\n"
            "Week ahead – Key events: the 4-6 most important scheduled releases/meetings with day and why they matter.\n"
            "Week ahead – Political events: elections, summits, budget/tariff deadlines, OPEC or geopolitical flashpoints mentioned in the news, with the market channel for each.\n"
            "Week ahead – Themes & risks: 3-4 short items for FX, gold and oil.\n"
            "Political items: market impact only, never opinions on parties or politicians.\n"
            "Maximum 480 words."
        )
    else:
        task = (
            "You are a senior FX and commodities analyst writing the 'Market picture' of a morning WhatsApp brief "
            "for a trader who follows COT positioning on the majors, gold and oil.\n" + rules +
            "Structure:\n"
            "USD & majors: 2-3 sentences.\nGold: 1-2 sentences.\nOil: 1-2 sentences.\n"
            "Risk mood: 1 sentence (yields, equities, VIX).\n"
            "Political risk: 1 sentence on the political/geopolitical story most likely to move markets and the channel (market impact only, no opinions).\n"
            "Watch today: 2-3 short items.\n"
            "Maximum 260 words."
        )
    prompt = (task + f"\n\nPRICES (latest, % change {'over the week' if WEEKLY else 'vs previous close'}):\n"
              + ("\n".join(prices) or "n/a")
              + f"\n\nEVENTS ({'coming week' if WEEKLY else 'next 24h'}, {TZL}):\n"
              + ("\n".join(l for l in (events or []) if l.strip()) or "none")
              + "\n\nNEWS:\n" + "\n".join(lines))
    try:
        text = gemini(prompt)
        return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)  # markdown bold -> WhatsApp bold
    except Exception as e:
        print(f"AI summary failed (sending without it): {e}", file=sys.stderr)
        return None


# ---------- Build ----------
def fmt_story(n, i):
    stars = "⭐" * n["stars"] + " " if n.get("stars") else ""
    num = f"{i}. " if i else ""
    stamp = f"{n['published'].astimezone(LOCAL):%a} · " if WEEKLY else ""
    line = f"{num}{stars}{stamp}{n['title']} _({n['source']})_"
    if n.get("moves"):
        line += f"\n   📊 {n['moves']}"
    if n["desc"]:
        line += f"\n   {n['desc']}"
    return line + f"\n   {n['link']}"


def build_post():
    now = datetime.now(LOCAL)
    per = int(os.getenv("NEWS_PER_SECTION", "10" if WEEKLY else "6"))
    name = os.getenv("CHANNEL_NAME", "FX • Gold • Oil Brief")
    prices, events, news = get_prices(), get_calendar(), get_news()
    if not (prices or events or news):
        raise SystemExit("No data fetched; keeping the previous brief unchanged.")
    rated = rate_news(news)
    if rated:
        # drop noise: 1-star stories everywhere, and political stories below 3 stars
        news = [n for n in news if n.get("stars", 0) >= 2 and
                (n["section"] != "politics" or n.get("stars", 0) >= 3)]
        news.sort(key=lambda n: (n.get("stars", 0), n["published"]), reverse=True)

    edition = "WEEKLY EDITION — review & week ahead" if WEEKLY else "Daily"
    parts = [f"*{name}* | {edition}\n{now:%A, %d %B %Y} • {now:%I:%M %p} {TZL}"]
    if prices:
        hdr = "*📊 Week in review — prices* (weekly change)" if WEEKLY else "*📊 Price snapshot* (vs prev. close)"
        parts.append(hdr + "\n" + "\n".join(prices))
    summary = ai_summary(prices, events, news)
    if summary:
        parts.append(("*🧭 Review & outlook*\n" if WEEKLY else "*🧭 Market picture*\n") + summary)
    shown = set()
    if rated:
        top_n = 5 if WEEKLY else 3
        top = [n for n in news if n.get("stars", 0) >= 4][:top_n]
        if top:
            parts.append(("*🔥 Top stories of the week*" if WEEKLY else "*🔥 Top-rated stories*") + "\n"
                         + "\n\n".join(fmt_story(n, None) for n in top))
            shown = {id(n) for n in top}
    if WEEKLY:
        if events is None or not events:
            cal = ("Next week's calendar is not published yet. Check "
                   "https://www.forexfactory.com/calendar")
        else:
            cal = "\n".join(events).strip()
        parts.append(f"*🗓️ Coming week — high & medium-impact events ({TZL})*\n{cal}")
    else:
        parts.append(f"*🗓️ Next 24h — key events ({TZL})*\n"
                     + ("\n".join(events) if events else "No high/medium-impact releases scheduled."))
    for sec, title in SECTIONS:
        sec_items = [x for x in news if x["section"] == sec and id(x) not in shown][:per]
        if not sec_items:
            continue
        lines = [fmt_story(n, i) for i, n in enumerate(sec_items, 1)]
        label = f"{title}{' — this week' if WEEKLY and sec != 'ahead' else ''}"
        parts.append(f"*{label}*\n" + "\n\n".join(lines))
    parts.append("_Sources: Yahoo Finance, Forex Factory, FXStreet, Investing.com, OilPrice.com, "
                 "Google News. ⭐ ratings and summaries are AI-generated. "
                 "Information only, not investment advice._")
    return "\n\n".join(parts)


if __name__ == "__main__":
    try:
        post = build_post()
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        raise SystemExit("Brief failed — see the error above.")
    print(post)
    print(f"\n[{'weekly' if WEEKLY else 'daily'} • {len(post)} characters]", file=sys.stderr)
    if "--dry-run" not in sys.argv:
        with open("brief.txt", "w", encoding="utf-8") as f:
            f.write(post + "\n")
        if WEEKLY:
            with open("weekly.txt", "w", encoding="utf-8") as f:
                f.write(post + "\n")
