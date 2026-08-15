#!/usr/bin/env python3
"""
LinkedIn Jobs lead pipeline for the Daily Lead Machine board.

Scrapes the PUBLIC LinkedIn jobs search (no login) for remote marketing roles posted in the
last 24h, qualifies + scores with the SAME rules (reject agencies/recruiters, US-hours,
off-role, off-geo, bad verticals; keep your services), rolling 7-day store.

LinkedIn skews to full-time employee roles and hides salary, so the search is biased to
REMOTE and contract/part-time roles are rewarded. Named employer + posted date + often the
hiring person's name make these strong agency-pitch leads too.

Public: run(token) -> list[lead dict].  Env: APIFY_TOKEN.  Test: python linkedin_scrape.py <jobs.json>
"""
import os, re, json, sys, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
import olj_scrape as core

ACTOR = "curious_coder~linkedin-jobs-scraper"
# f_TPR=r86400 -> posted last 24h · f_WT=2 -> remote · sortBy=DD -> newest first
SEARCH_BASE = "https://www.linkedin.com/jobs/search/?sortBy=DD&f_TPR=r86400&f_WT=2&keywords="
SEARCH_KEYWORDS = [
    "ai automation manager", "voice ai", "conversational ai", "chatbot developer",
    "workflow automation specialist", "customer experience automation",
    "arabic voice ai", "arabic customer experience", "gulf call center automation",
]
OFFSHORE = ["india", "philippines", "pakistan", "bangladesh", "nigeria", "indonesia",
            "sri lanka", "egypt", "kenya", "vietnam", "ukraine"]
SERVED_LOC = ["united states", "united kingdom", "canada", "australia", "new zealand",
              "england", "london", "metropolitan area", ", ny", ", ca", ", tx", ", fl",
              ", il", ", pa", ", nc", ", dc", ", ga", ", wa", ", ma", ", co", ", az",
              "united arab emirates", "dubai", "abu dhabi", "saudi arabia", "riyadh",
              "jeddah", "qatar", "doha", "kuwait", "bahrain", "oman", "gcc"]

def _served(loc, text):
    lt, tt = loc.lower(), (loc + " " + text).lower()
    if any(k in tt for k in OFFSHORE) and "remote" not in lt: return False
    if "remote" in tt: return True
    if core.country_of(loc) in ("US", "UK", "Canada", "Australia", "New Zealand",
                                 "UAE", "Saudi Arabia", "Qatar", "Kuwait", "Bahrain", "Oman"): return True
    return any(k in lt for k in SERVED_LOC)

def _salary_monthly(s):
    if not s: return None
    t = s.replace(",", "").lower()
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t) if float(x) > 0]
    if not nums: return None
    val = sum(nums[:2]) / len(nums[:2])
    if "/hr" in t or "hour" in t: val *= 160
    elif "/yr" in t or "year" in t or "/annum" in t or val >= 15000: val /= 12   # annual -> monthly
    return round(val) if 200 <= val <= 60000 else None

def qualify(job):
    title = (job.get("title") or "").strip()
    desc = job.get("descriptionText") or ""
    loc = job.get("location") or ""
    if not title: return None
    text = (title + " " + desc).lower(); tl = title.lower()
    if not _served(loc, text): return None                     # served geo / remote only
    if any(k in text for k in core.REJECT_VERTICAL): return None
    if any(k in tl for k in core.REJECT_ROLE): return None
    if not any(k in text for k in core.CORE): return None
    if core.wants_form(text): return None                      # skip "fill out this form" applications
    company = job.get("companyName") or ""
    ctype = core.company_type(text, company)
    if ctype == "Agency" or any(k in company.lower() for k in ["recruit", "staffing", "talent"]):
        return None                                            # no agencies / recruiters
    if core.requires_us_hours(text): return None
    monthly = _salary_monthly(job.get("salary"))
    if monthly is None: base = 5
    elif monthly >= 8000: base = 9
    elif monthly >= 5000: base = 8
    elif monthly >= 3500: base = 7
    elif monthly >= 2000: base = 6
    else: base = 5
    et = (job.get("employmentType") or "").lower()
    if any(k in et for k in ("contract", "part-time", "temporary", "freelance")): base = min(10, base + 1)
    if ctype == "eCommerce/DTC": base = min(10, base + 1)
    prio = "urgent" if base == 10 else "high" if base >= 8 else "normal" if base >= 6 else "low"
    pd = (job.get("postedAt") or "")[:10]
    link = (job.get("link") or job.get("applyUrl") or "").split("?")[0]
    ctry = core.country_of(loc)
    return dict(source="LinkedIn", jobTitle=title, company=company or "Not listed",
                email=core.extract_email(desc), companyType=ctype,
                salary=job.get("salary") or (job.get("employmentType") or "Not listed"),
                salaryUsd=monthly, datePosted=pd, datetime=(pd + " 00:00:00" if pd else ""),
                link=link, score=base, priority=prio, industry=job.get("industries", "") or "",
                service=core.services_for(text),
                country=ctry if ctry != "Unknown" else ("US" if "remote" in loc.lower() else "Unknown"),
                salesStage="Lead Qualification", why="",
                notes=re.sub(r"\s+", " ", desc)[:280] or title,
                contactName=job.get("jobPosterName", "") or "", tier=job.get("seniorityLevel", ""), location=loc)

def scrape(token):
    starts = [SEARCH_BASE + urllib.parse.quote(k) for k in SEARCH_KEYWORDS]
    payload = {"urls": starts, "scrapeCompany": False, "count": 120}
    url = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?token={token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())

def run(token):
    items = scrape(token)                                      # f_TPR=r86400 already limits to last 24h
    seen, leads = set(), []
    for j in items:
        jid = j.get("id") or (j.get("link") or "").split("?")[0]
        if not jid or jid in seen: continue
        seen.add(jid)
        q = qualify(j)
        if q: leads.append(q)
    return leads

if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))
    jobs = data.get("items", data) if isinstance(data, dict) else data
    kept = [q for q in (qualify(j) for j in jobs) if q]
    print(f"qualified {len(kept)} / {len(jobs)}")
    for q in sorted(kept, key=lambda x: -x["score"]):
        print(f"[{q['score']}/{q['priority']:6}] {q['jobTitle'][:40]:40} | {str(q['salary'])[:16]:16} | {q['companyType']:14} | {q['company'][:20]} | {q['location'][:16]}")
