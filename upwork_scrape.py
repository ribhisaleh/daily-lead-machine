#!/usr/bin/env python3
"""
Upwork job-post lead pipeline for the Daily Lead Machine board.

Searches Upwork for marketing gigs (last 24h), qualifies + scores with the SAME rules as
OnlineJobs/Facebook (reject agencies / US-hours / off-role / bad verticals; keep your
core services), and returns board-ready leads for a rolling 7-day store.

Upwork search is already role-targeted, so nearly every post is a real marketing gig — the
filter mostly drops agencies, US-hours-required, and design/editor roles. Apply direct via
the job link. Client company is usually anonymous (shows "Not listed").

Public: run(token) -> list[lead dict].  Env: APIFY_TOKEN.  Test: python upwork_scrape.py <jobs.json>
"""
import os, re, json, sys, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
import olj_scrape as core   # shared CORE/REJECT_ROLE/AGENCY_HINT/requires_us_hours/company_type/etc.

ACTOR = "curious_coder~upwork-jobs-scraper"
SEARCH_BASE = "https://www.upwork.com/nx/search/jobs/?sort=recency&nbs=1&q="
SEARCH_QUERIES = [
    "ai automation", "voice ai agent", "ai receptionist", "chatbot developer",
    "make.com automation", "zapier expert", "n8n automation", "ai appointment setter",
    "ai cold caller", "conversational ai", "arabic voice ai", "arabic chatbot",
]

def _budget(job):
    h = job.get("hourly") or {}
    lo, hi = h.get("min"), h.get("max")
    if lo or hi:
        disp = f"${lo}-{hi}/hr" if (lo and hi) else f"${hi or lo}/hr"
        try: rate = float(hi or lo)
        except Exception: rate = None
        return disp, (round(rate * 160) if rate else None)   # rough monthly-equiv for tiering
    fp = job.get("fixedPrice")
    if fp:
        amt = fp.get("amount") if isinstance(fp, dict) else fp
        try: amt = float(re.sub(r"[^\d.]", "", str(amt)))
        except Exception: amt = None
        return (f"${amt:.0f} fixed" if amt else "Fixed price"), (round(amt) if amt else None)
    if job.get("weeklyRetainerBudget"):
        return "Weekly retainer", None
    return "Not listed", None

def qualify(job):
    title = (job.get("title") or "").strip()
    desc = job.get("description") or ""
    if not title: return None
    text = (title + " " + desc).lower()
    tl = title.lower()
    skills = [str(s).lower() for s in (job.get("skills") or [])]
    if any(k in text for k in core.REJECT_VERTICAL): return None
    if any(k in tl for k in core.REJECT_ROLE): return None
    if "designer" in tl and any(s in ("graphic design", "adobe illustrator", "adobe photoshop") for s in skills):
        return None                                              # ad-design role, not media buying
    if not any(k in text for k in core.CORE): return None
    if core.wants_form(text): return None                        # skip "fill out this form" applications
    if any(k in text for k in core.AGENCY_HINT): return None     # no agencies
    if core.requires_us_hours(text): return None                 # PH-time / async only
    company = core.company_from({"company": "", "snippet": desc})
    ctype = core.company_type(text, company)
    country = core.country_of(text)
    disp, monthly = _budget(job)
    if monthly is None:                                          # budget sometimes only in the description
        sm = re.search(r"\$[\d,]+(?:\s*[-–]\s*\$?[\d,]+)?\s*/?\s*(?:month|mo|hour|hr|week|/hr|/mo)?", desc)
        if sm:
            m2 = core.salary_monthly_usd(sm.group(0))
            if m2:
                monthly = m2
                if disp == "Not listed": disp = sm.group(0).strip()
    if monthly is None: base = 5
    elif monthly >= 6400: base = 9    # ~$40/hr+
    elif monthly >= 4000: base = 8    # ~$25/hr
    elif monthly >= 2400: base = 7    # ~$15/hr
    elif monthly >= 1000: base = 6
    else: base = 5
    if ctype == "eCommerce/DTC" and base >= 7: base = min(10, base + 1)
    prio = "urgent" if base == 10 else "high" if base >= 8 else "normal" if base >= 6 else "low"
    pt = job.get("publishTime", "") or ""
    return dict(source="Upwork", jobTitle=title, company=company or "Not listed",
                email=core.extract_email(desc), companyType=ctype,
                salary=disp, salaryUsd=monthly, datePosted=pt[:10],
                datetime=pt.replace("T", " ")[:19], link=job.get("url", ""), score=base, priority=prio,
                industry="", service=core.services_for(text), country=country,
                salesStage="Lead Qualification", why="", notes=re.sub(r"\s+", " ", desc)[:280],
                contactName="", tier=job.get("contractorTier", ""))

def scrape(token):
    starts = [{"url": SEARCH_BASE + urllib.parse.quote(q)} for q in SEARCH_QUERIES]
    payload = {"startUrls": starts, "sort": "recency", "maxItems": 200}
    url = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?token={token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())

def run(token):
    items = scrape(token)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    seen, leads = {}, []
    for j in items:
        pt = j.get("publishTime", "") or ""
        if pt and pt[:19] < cutoff: continue
        u = j.get("url")
        if not u or u in seen: continue
        seen[u] = 1
        q = qualify(j)
        if q: leads.append(q)
    return leads

if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))
    jobs = data.get("items", data) if isinstance(data, dict) else data
    kept = [q for q in (qualify(j) for j in jobs) if q]
    print(f"qualified {len(kept)} / {len(jobs)}")
    for q in sorted(kept, key=lambda x: -x["score"]):
        print(f"[{q['score']}/{q['priority']:6}] {q['jobTitle'][:46]:46} | {q['salary'][:14]:14} | {q['companyType']:14} | {q['company'][:18]}")
