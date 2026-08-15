#!/usr/bin/env python3
"""
Facebook public-group lead pipeline for the Daily Lead Machine board.

Scrapes PUBLIC Facebook groups (no login) for hiring posts, filters them with the SAME
rules as OnlineJobs (reject agencies / US-hours / off-role / bad verticals; keep your
core services), and returns board-ready leads for a rolling 7-day store.

Group posts are freeform text, so a post must first look like a HIRING post, then pass
the shared ICP filter. Company / salary are extracted best-effort and are often "Not listed".

Public functions:
  run(token) -> list[lead dict]
  merge_store(...) is reused from olj_scrape.
Env: APIFY_TOKEN.  Add group URLs to GROUP_URLS below.
"""
import os, re, json, sys, urllib.request
from datetime import datetime, timezone, timedelta
import olj_scrape as core   # shared rules: CORE, REJECT_ROLE, AGENCY_HINT, requires_us_hours, salary_monthly_usd, etc.

ACTOR = "apify~facebook-groups-scraper"

# >>> ADD YOUR PUBLIC FACEBOOK GROUP URLS HERE <<<
# Only PUBLIC groups work (readable while logged out). Private groups cannot be scraped.
GROUP_URLS = [
    # Public VA / remote-work groups (hiring-focused) — the productive ones.
    "https://www.facebook.com/groups/vaworkersph",
    "https://www.facebook.com/groups/544033120196337",
    "https://www.facebook.com/groups/126432684504000",
    # Public groups from the 49-list (only these 3 were public; the other 46 are PRIVATE and
    # cannot be scraped without a logged-in session). These are networking/promo groups, so
    # they rarely contain marketing hiring posts — kept per request; safe to remove.
    "https://www.facebook.com/groups/2815042615255352",
    "https://www.facebook.com/groups/496910621174485",
    "https://www.facebook.com/groups/MommiesOnAMissionNOW",
]

# An EMPLOYER hiring post (not a jobseeker advertising themselves, not chatter).
EMPLOYER_INTENT = ["hiring", "we're hiring", "we are hiring", "were hiring", "now hiring", "we need",
                   "looking for", "looking to hire", "in search of", "we are seeking", "we're seeking",
                   "join our team", "join our growing", "urgently need", "in need of a",
                   "adding to our team", "expanding our team", "to join our"]
# Signals the POSTER is offering themselves (a VA/freelancer) — reject; wrong direction.
JOBSEEKER = ["are you looking for", "looking for work", "looking for a job", "looking for job",
             "open to work", "open for work", "available for work", "available for hire", "hire me",
             "for hire", "my services", "i offer", "i'm a ", "i am a ", "i can help you", "let me help",
             "dm me for my portfolio", "freelancer available", "seeking work", "seeking a role",
             "seeking an opportunit", "seeking opportunit", "ready to work", "in search of a job",
             "i specialize in", "here's my portfolio", "check my portfolio"]

def _first_line(text):
    line = text.strip().splitlines()[0] if text.strip() else ""
    line = re.sub(r"\s+", " ", line).strip(" :·-—|")
    return (line[:80] + "…") if len(line) > 80 else line or "Facebook group post"

def _date_of(t):
    if not t: return "", ""
    s = str(t)
    m = re.search(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?", s)
    if m:
        v = m.group(0).replace("T", " ")
        return v[:10], (v if len(v) > 10 else v + " 00:00:00")
    if s.isdigit():  # epoch seconds
        dt = datetime.fromtimestamp(int(s), tz=timezone.utc)
        return dt.date().isoformat(), dt.strftime("%Y-%m-%d %H:%M:%S")
    return "", ""

def qualify(post):
    text = (post.get("text") or "").strip()
    if len(text) < 25: return None
    t = text.lower()
    title = _first_line(text).lower()                                # the role is usually stated first
    if any(k in t for k in JOBSEEKER): return None                   # poster is offering themselves
    if not any(k in t for k in EMPLOYER_INTENT): return None         # must be an employer hiring
    if any(k in t for k in core.REJECT_VERTICAL): return None
    if any(k in title for k in core.REJECT_ROLE): return None        # hiring an editor/designer/etc.
    if "virtual assistant" in title and not any(k in title for k in
           ["ai","automation","voice","chatbot","n8n","make.com","zapier"]):
        return None                                                  # generic VA, not AI automation
    if not any(k in title for k in core.CORE): return None           # the ROLE (first line) must be a [YOUR NAME] service
    if core.wants_form(t): return None                               # skip "fill out this form" applications
    company = core.company_from({"company": "", "snippet": text})
    ctype = core.company_type(t, company)
    if ctype == "Agency": return None                                # no agencies
    if core.requires_us_hours(t): return None                        # PH-time / async only
    country = core.country_of(t)
    sm = re.search(r"\$[\d,]+(?:\s*[-–]\s*\$?[\d,]+)?(?:\s*/?\s*(?:hour|hr|month|mo))?", text)
    salary_raw = sm.group(0) if sm else ""
    monthly = core.salary_monthly_usd(salary_raw) if salary_raw else None
    if monthly is None: base = 5
    elif monthly >= 1500: base = 9
    elif monthly >= 1000: base = 8
    elif monthly >= 800: base = 7
    elif monthly >= 600: base = 6
    else: base = 5
    if base >= 8 and ctype == "eCommerce/DTC": base = min(10, base + 1)
    prio = "urgent" if base == 10 else "high" if base >= 8 else "normal" if base >= 6 else "low"
    date, dtime = _date_of(post.get("time"))
    return dict(source="Facebook", jobTitle=_first_line(text), company=company or "Not listed",
                email=core.extract_email(text), companyType=ctype, salary=salary_raw or "Not listed", salaryUsd=monthly,
                datePosted=date, datetime=dtime, link=post.get("url", ""), score=base, priority=prio,
                industry="", service=core.services_for(t), country=country,
                salesStage="Lead Qualification", why="",
                notes=re.sub(r"\s+", " ", text)[:280],
                contactName=(post.get("user") or {}).get("name", "") if isinstance(post.get("user"), dict) else "",
                group=post.get("groupTitle", ""))

def scrape(token, since_days=1):
    if not GROUP_URLS: return []
    payload = {"startUrls": [{"url": u} for u in GROUP_URLS], "resultsLimit": 60,
               "viewOption": "CHRONOLOGICAL", "onlyPostsNewerThan": f"{since_days} days"}
    url = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?token={token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())

def run(token):
    if not GROUP_URLS:
        print("Facebook: no GROUP_URLS configured — skipping.")
        return []
    items = scrape(token)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    seen, leads = {}, []
    for p in items:
        _, dtime = _date_of(p.get("time"))
        if dtime and dtime < cutoff: continue
        url = p.get("url")
        if not url or url in seen: continue
        seen[url] = 1
        q = qualify(p)
        if q: leads.append(q)
    return leads

# offline qualifier test: `python fb_scrape.py <posts.json>`
if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))
    posts = data.get("items", data) if isinstance(data, dict) else data
    kept = [q for q in (qualify(p) for p in posts) if q]
    print(f"qualified {len(kept)} / {len(posts)}")
    for q in sorted(kept, key=lambda x: -x["score"]):
        print(f"[{q['score']}/{q['priority']:6}] {q['jobTitle'][:50]:50} | {q['salary'][:16]:16} | {q['companyType']:14} | {q['group'][:20]}")
