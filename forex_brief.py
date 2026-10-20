"""Free daily forex brief for a WhatsApp Channel.

Runs on GitHub Actions (free) and writes the finished post to brief.txt in the repo.
An Android home-screen button (HTTP Shortcuts app) fetches that file and opens WhatsApp
to share it to your channel.

Sources:
  - FX headlines: FXStreet RSS
  - Economic calendar: Forex Factory weekly JSON (high/medium impact, today, Nepal time)
  - AI summary: Google Gemini free tier (needs GEMINI_API_KEY secret)

Settings (GitHub Secrets / workflow env):
  GEMINI_API_KEY   free key from https://aistudio.google.com
  GEMINI_MODEL     optional, default gemini-2.5-flash
  CURRENCIES       optional, default USD,EUR,GBP,JPY,CNY,INR
  CHANNEL_NAME     optional, shown in the post header
"""
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

NPT = timezone(timedelta(hours=5, minutes=45))
UA = {"User-Agent": "Mozilla/5.0 (forex-brief)"}

RSS_FEEDS = ["https://www.fxstreet.com/rss/news"]
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
MAX_HEADLINES = 6


def fetch(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_headlines(limit=15):
    items = []
    for url in RSS_FEEDS:
        try:
            for it in ET.fromstring(fetch(url)).iter("item"):
                t = (it.findtext("title") or "").strip()
                if t and t not in items:
                    items.append(t)
        except Exception as e:
            print(f"feed failed {url}: {e}", file=sys.stderr)
    return items[:limit]


def get_calendar():
    wanted = {c.strip().upper() for c in os.getenv("CURRENCIES", "USD,EUR,GBP,JPY,CNY,INR").split(",")}
    today = datetime.now(NPT).date()
    events = []
    try:
        data = json.loads(fetch(CALENDAR_URL))
    except Exception as e:
        print(f"calendar failed: {e}", file=sys.stderr)
        return events
    for ev in data:
        if ev.get("impact") not in ("High", "Medium") or (ev.get("country") or "").upper() not in wanted:
            continue
        try:
            t = datetime.fromisoformat(ev["date"]).astimezone(NPT)
        except Exception:
            continue
        if t.date() != today:
            continue
        mark = "🔴" if ev["impact"] == "High" else "🟠"
        extra = ""
        if ev.get("forecast"):
            extra += f" (exp. {ev['forecast']}"
            extra += f", prev. {ev['previous']})" if ev.get("previous") else ")"
        events.append((t, f"{mark} {t:%I:%M %p} — {ev['country']} {ev['title']}{extra}"))
    return [line for _, line in sorted(events)]


def ai_summary(headlines, events):
    key = os.getenv("GEMINI_API_KEY")
    if not key or not headlines:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    prompt = (
        "Write the summary section of a morning forex brief for a WhatsApp channel read by "
        "business people in Nepal. Use only the headlines and calendar below; do not invent "
        "numbers or events. Format: 3 to 4 short bullet lines starting with '•', each one idea "
        "(US dollar/Fed, other majors, gold and oil, risk mood). Then one line starting "
        "'INR/NPR:' on what it may mean for the Indian rupee and therefore the Nepali rupee, "
        "which is pegged to it. Under 110 words total. Plain text, no markdown, no advice to "
        "buy or sell.\n\nHeadlines:\n- " + "\n- ".join(headlines)
        + "\n\nToday's events (Nepal time):\n" + ("\n".join(events) or "none")
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        out = json.loads(fetch(url, data=body, headers={"Content-Type": "application/json"}, timeout=90))
        return out["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"AI summary failed (posting without it): {e}", file=sys.stderr)
        return None


def build_post():
    now = datetime.now(NPT)
    name = os.getenv("CHANNEL_NAME", "Forex Morning Brief")
    headlines, events = get_headlines(), get_calendar()
    if not headlines and not events:
        raise SystemExit("No data fetched; keeping yesterday's brief unchanged.")
    parts = [f"*{name}* | {now:%a, %d %b %Y}"]
    summary = ai_summary(headlines, events)
    if summary:
        parts.append("*Market picture*\n" + summary)
    parts.append("*Today's key events (Nepal time)*\n"
                 + ("\n".join(events) if events else "No major scheduled releases today."))
    if headlines:
        parts.append("*Top headlines*\n" + "\n".join(f"• {h}" for h in headlines[:MAX_HEADLINES]))
    parts.append("_Sources: FXStreet, Forex Factory. For information only, not investment advice._")
    return "\n\n".join(parts)


if __name__ == "__main__":
    post = build_post()
    print(post)
    if "--dry-run" not in sys.argv:
        with open("brief.txt", "w", encoding="utf-8") as f:
            f.write(post + "\n")
