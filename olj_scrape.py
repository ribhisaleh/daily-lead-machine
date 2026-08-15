#!/usr/bin/env python3
"""
OnlineJobs.ph lead pipeline for the Daily Lead Machine board.

Mirrors the X pipeline: scrapes the search pages (last 24h of posts), qualifies +
scores each against your specialist profile, and returns board-ready leads that
merge into a rolling 7-day store. No ClickUp.

Public functions:
  run(token) -> list[lead dict]              # scrape + parse + qualify
  merge_store(new, store, today_iso, window) # rolling dedup/expire
Env: APIFY_TOKEN. Test offline: `python olj_scrape.py <crawled_html.json>`
"""
import os, re, json, sys, urllib.request, urllib.parse, html as htmlmod
from datetime import datetime, timezone, timedelta

SEARCH_KEYWORDS = [
    "ai automation specialist", "voice ai agent", "chatbot developer",
    "make.com automation expert", "zapier automation expert", "n8n automation",
    "ai appointment setter", "ai receptionist", "conversational ai developer",
    "workflow automation specialist", "crm automation expert",
    "arabic voice ai", "arabic chatbot developer", "arabic call center automation",
]
ACTOR = "apify~website-content-crawler"
BASE = "https://www.onlinejobs.ph/jobseekers/jobsearch?jobkeyword="

# ---- role gating (your core services vs reject list) ----
# These are fallback defaults, used only if config/keywords.py fails to import below.
CORE = ["ai automation","voice agent","voice ai","ai receptionist","ai cold caller",
        "chatbot developer","conversational ai","make.com","zapier","n8n automation",
        "workflow automation","ivr","call automation","crm automation","arabic voice ai"]
REJECT_ROLE = ["video editor","graphic designer","data entry","bookkeeper",
               "web developer","wordpress developer","seo specialist",
               "content writer","photographer"]
REJECT_VERTICAL = ["crypto","cryptocurrency","web3","nft","casino","gambling","betting","igaming","sportsbook",
                   "adult","onlyfans","escort"]
AGENCY_HINT = ["agency","marketing company","consultancy","consulting","we help our clients","for our clients",
               "multiple clients","client accounts","media company","growth partner"]
ECOM_HINT = ["shopify","dtc","d2c","ecommerce","e-commerce","our store","our brand","product","skincare",
             "apparel","jewelry","jewellery","supplement","cosmetic","beauty","fashion","retail",
             "call center","customer experience","clinic","real estate","hospitality","restaurant chain"]
SERVED = {"US":["us-based","u.s.","united states","america","us hours","est ","pst ","american"],
          "UK":["uk-based","united kingdom","u.k.","british","gmt"],
          "Canada":["canada","canadian"],"Australia":["australia","australian","aud","sydney","melbourne"],
          "New Zealand":["new zealand","nz-based"," nz "],
          "UAE":["uae","dubai","abu dhabi","sharjah","emirates"],
          "Saudi Arabia":["saudi","ksa","riyadh","jeddah"],
          "Qatar":["qatar","doha"],"Kuwait":["kuwait"],"Bahrain":["bahrain","manama"],
          "Oman":["oman","muscat"]}
FX = {"AUD":0.66,"SGD":0.74,"GBP":1.27,"CAD":0.73,"EUR":1.08,"USD":1.0,"PHP":0.017,"NZD":0.61}

# ---- user settings from keywords.py (edit THAT file, not this one) ----
CORE_GULF = []
try:
    import keywords as _kw
    CORE = _kw.CORE
    CORE_GULF = getattr(_kw, "CORE_GULF", [])
    REJECT_ROLE = _kw.REJECT
    SERVED = {k: v for k, v in SERVED.items() if k in _kw.PLACES}
except Exception:
    pass

def track_for(text):
    """Which ICP track this post matches: the niche Gulf Arabic voice AI
    track if any CORE_GULF term hit, otherwise the general AI automation
    track. Tagged into the 'service' field so it's visible/filterable/
    exportable on the board without any template changes."""
    t = text.lower()
    if any(k.lower() in t for k in CORE_GULF):
        return "Gulf Arabic Voice AI"
    return "AI Automation & Voice Agents"

# ---- shared enrichment/reject helpers (reused by fb/upwork/linkedin scrapers) ----
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
def extract_email(text):
    """First email address in the post text, or "" if none."""
    m = EMAIL_RE.search(text or "")
    return m.group(0) if m else ""

# Posts that route the application through a form get dropped (per your rule).
FORM_HINT = ["fill out this form","fill up this form","fill out the form","fill up the form",
             "fill out our form","fill in the form","fill out the application","fill up the application",
             "complete this form","complete the form","complete our application","google form",
             "forms.gle","docs.google.com/forms","typeform","jotform","fill out a form",
             "apply via this form","apply through this form","apply using this form","submit this form",
             "application form","fill this form","fill the form"]
def wants_form(text):
    return any(k in (text or "").lower() for k in FORM_HINT)

def strip_tags(s):
    return re.sub(r"\s+"," ",htmlmod.unescape(re.sub(r"<[^>]+>"," ",s or ""))).strip()

def parse_listings(html):
    out=[]
    for b in re.split(r'jobpost-cat-box latest-job-post', html)[1:]:
        m=re.search(r'/jobseekers/job/([^"?#\s]+)', b)
        if not m: continue
        alt=re.search(r'employer_logos[^>]*?alt="([^"]*)"', b)
        h4=re.search(r'<h4[^>]*>(.*?)</h4>', b, re.S)
        traw=h4.group(1) if h4 else ""
        badge=re.search(r'<span class="badge ([a-z-]+)', traw)
        dt=re.search(r'data-temp="(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', b)
        sal=re.search(r'icon-round-dollar.*?<dd[^>]*>(.*?)</dd>', b, re.S)
        desc=re.search(r'class="desc[^"]*">(.*?)See More', b, re.S)
        out.append(dict(
            url="https://www.onlinejobs.ph/jobseekers/job/"+m.group(1),
            company=(alt.group(1).strip() if alt else ""),
            title=strip_tags(re.sub(r'<span class="badge.*?</span>','',traw,flags=re.S)),
            wtype={"full-time":"Full Time","part-time":"Part Time","gig":"Gig","any":"Any"}.get(badge.group(1),"") if badge else "",
            datetime=(dt.group(1) if dt else ""),
            date=(dt.group(1)[:10] if dt else ""),
            salary_raw=strip_tags(sal.group(1)) if sal else "",
            snippet=strip_tags(desc.group(1))[:240] if desc else ""))
    return out

def salary_monthly_usd(raw):
    if not raw: return None
    s=raw.replace(",","")
    if re.search(r"negotiab|tbd|depend|proposal", s, re.I): return None
    cur="USD"
    for c in FX:
        if c.lower() in s.lower() or (c=="PHP" and ("php" in s.lower() or "₱" in s or re.search(r'p\d',s.lower()))): cur=c
    if "aud" in s.lower(): cur="AUD"
    if "sgd" in s.lower(): cur="SGD"
    nums=[float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    nums=[n for n in nums if n>2]  # drop stray "3"
    if not nums: return None
    val=sum(nums[:2])/len(nums[:2])  # midpoint of first 1-2 numbers
    if re.search(r"/hr|/hour|hour|hourly|an hr", s, re.I):
        if cur=="USD" and val>120: return None   # implausible USD/hr -> currency unknown
        val*=160
    elif val<50:
        return None                              # bare tiny number, not a real monthly wage
    res=round(val*FX.get(cur,1.0))
    return res if 50<=res<=8000 else None        # clamp absurd values to "unknown"

def company_from(listing):
    if listing["company"]: return listing["company"]
    sn=listing["snippet"]
    m=re.search(r"\bAbout\s+([A-Z][\w&.'-]+(?:\s[A-Z][\w&.'-]+){0,2})", sn)
    if m: return m.group(1).strip()
    m=re.match(r"([A-Z][\w&.'-]+(?:\s[A-Z][\w&.'-]+){0,2})\s+is\s+(?:a|an|the|looking|hiring|one|now)\b", sn)
    if m:
        cand=m.group(1).strip()
        if cand.lower().split()[0] not in ("we","our","this","the","us","i","you","are","hi","it","he","she") and len(cand)>3:
            return cand
    return ""

def country_of(text):
    t=text.lower()
    for c,keys in SERVED.items():
        if any(k in t for k in keys): return c
    return "Unknown"

def company_type(text, company):
    t=(text+" "+company).lower()
    if any(k in t for k in AGENCY_HINT): return "Agency"
    if any(k in t for k in ECOM_HINT): return "eCommerce/DTC"
    if any(k in t for k in ["coach","course","education","info product","webinar"]): return "Coach/Education"
    if any(k in t for k in ["restaurant","clinic","med spa","real estate","local","dealership","law firm"]): return "Local Business"
    return "Other"

def services_for(text):
    t=text.lower(); s=[]
    if any(k in t for k in ["voice ai","voice agent","ivr","retell","vapi","voiceflow","call automation","receptionist"]): s.append("Voice AI")
    if any(k in t for k in ["chatbot","conversational ai","gpt integration"]): s.append("Chatbot / Conversational AI")
    if any(k in t for k in ["make.com","zapier","n8n","workflow automation","crm automation"]): s.append("Workflow Automation")
    if any(k in t for k in ["cold caller","appointment setter","sdr","lead follow-up"]): s.append("AI Outreach / SDR")
    s.append(track_for(text))
    return s or ["Workflow Automation"]

def requires_us_hours(text):
    """True only if the role requires WORKING American shift hours. Occasional meetings
    during US hours, small overlap, and async/flexible schedules are fine (kept)."""
    t=text.lower()
    if re.search(r"meeting|1-2 ?hour|1 to 2 hour|few hours overlap|some overlap|couple (?:of )?hours|"
                 r"async|asynchronous|flexible (?:hours|schedule|time)|your own hours|any ?time ?zone|work anytime", t):
        return False
    pats=[r"graveyard", r"night ?shift",
          r"\b9\s*(?:am|:00)?\s*[-–to]{1,3}\s*[56]\s*(?:pm|:00)?\s*(?:est|pst|cst|edt|pdt|eastern|pacific)",
          r"(?:work|working|available|shift)\b.{0,25}\b(?:us|u\.s\.|american|eastern|pacific|est|pst|cst)\b.{0,12}(?:hours|time|business)",
          r"\b(?:est|pst|cst|edt|pdt)\b.{0,10}(?:hours|time zone|timezone|shift)",
          r"must be (?:available|online).{0,25}(?:us|est|pst|american)",
          r"overlap.{0,25}(?:full|entire|whole|8\s*hours?|8-hour|complete)"]
    return any(re.search(p, t) for p in pats)

def qualify(l):
    text=(l["title"]+" "+l["snippet"]).lower()
    title=l["title"].lower()
    # hard rejects
    if any(k in text for k in REJECT_VERTICAL): return None
    if any(k in title for k in REJECT_ROLE): return None            # editor/design/scriptwriter/uploader/etc in title
    # generic VA with no marketing focus in the title
    if re.search(r"virtual assistant|executive assistant", title) and \
       not any(k in title for k in ["ai","automation","voice","chatbot","n8n","make.com","zapier"]):
        return None
    if not any(k in text for k in CORE): return None
    if wants_form(text): return None                  # skip "fill out this form" style applications
    if int(l.get("employer_posts") or 0) > 10: return None  # OLJ high-volume employer (needs employer_posts; see note in run())
    monthly=salary_monthly_usd(l["salary_raw"])
    company=company_from(l); ctype=company_type(text, company); country=country_of(text)
    email=extract_email(l.get("snippet",""))
    if ctype=="Agency": return None                  # [YOUR NAME] doesn't work under/for agencies
    if requires_us_hours(text): return None           # PH-time / async only (meetings during US hours are fine)
    # score 4-10
    if monthly is None: base=5
    elif monthly>=1500: base=9
    elif monthly>=1000: base=8
    elif monthly>=800: base=7
    elif monthly>=600: base=6
    else: base=5
    multi=sum(1 for k in ["voice ai","chatbot","make.com","zapier","n8n","ivr","crm automation"] if k in text)
    if base>=8 and multi>=2: base=min(10,base+1)
    if "assistant" in title or "junior" in title: base=max(4,base-1)
    prio="urgent" if base==10 else "high" if base>=8 else "normal" if base>=6 else "low"
    return dict(source="OnlineJobs.ph", jobTitle=l["title"], company=company or "Not listed", email=email,
                companyType=ctype, salary=l["salary_raw"] or "Not listed",
                salaryUsd=monthly, datePosted=l["date"], datetime=l["datetime"], link=l["url"],
                score=base, priority=prio, industry="", service=services_for(text),
                country=country, salesStage="Lead Qualification", why="", notes=l["snippet"])

# ---- Apify scrape ----
def scrape(token):
    starts=[{"url":BASE+urllib.parse.quote(k)} for k in SEARCH_KEYWORDS]
    payload={"startUrls":starts,"crawlerType":"playwright:firefox","maxCrawlDepth":0,
             "maxCrawlPages":len(starts)+2,"proxyConfiguration":{"useApifyProxy":True,"apifyProxyGroups":["RESIDENTIAL"]},
             "respectRobotsTxtFile":True,"saveHtml":True,"htmlTransformer":"none"}
    url=f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?token={token}"
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=300) as r:
        return json.loads(r.read().decode())

def run(token, since_hours=24):
    items=scrape(token)
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=since_hours)).strftime("%Y-%m-%d %H:%M:%S")
    seen={}; leads=[]
    for it in items:
        for l in parse_listings(it.get("html") or ""):
            if not l["datetime"] or l["datetime"]<cutoff: continue   # last 24h only
            if l["url"] in seen: continue
            seen[l["url"]]=1
            q=qualify(l)
            if q: leads.append(q)
    return leads

def merge_store(new, store_path, today_iso, window_days=7):
    store={"leads":[]}
    if os.path.exists(store_path):
        try: store=json.load(open(store_path))
        except Exception: pass
    by={l["link"]:l for l in store.get("leads",[]) if l.get("link")}
    for l in new:
        if l["link"] in by: continue
        l["firstSeen"]=today_iso
        by[l["link"]]=l
    cutoff=(datetime.strptime(today_iso,"%Y-%m-%d")-timedelta(days=window_days)).strftime("%Y-%m-%d")
    leads=[l for l in by.values() if l.get("firstSeen","0")>=cutoff or l.get("datePosted","0")>=cutoff]
    for l in leads: l["isNew"]=(l.get("firstSeen")==today_iso)
    leads.sort(key=lambda l:(-l.get("score",0), l.get("datePosted","")), reverse=False)
    return {"windowDays":window_days,"updated":today_iso,"leads":leads}

# ---- offline test ----
if __name__=="__main__":
    data=json.load(open(sys.argv[1]))
    items=data.get("items",[data]) if isinstance(data,dict) else data
    kept=[]; rej=0
    for it in items:
        for l in parse_listings(it.get("html") or ""):
            q=qualify(l)
            if q: kept.append(q)
            else: rej+=1
    kept.sort(key=lambda x:-x["score"])
    print(f"qualified {len(kept)}  rejected {rej}\n")
    for q in kept:
        print(f"[{q['score']}/{q['priority']:6}] {q['jobTitle'][:46]:46} | {q['salary'][:20]:20} | ~${q['salaryUsd'] or '?'}/mo | {q['companyType']:14} | {q['country']:9} | {q['company'][:22]}")
