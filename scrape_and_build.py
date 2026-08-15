#!/usr/bin/env python3
"""
Daily Lead Machine — daily builder.

Pipeline (run once per day by GitHub Actions):
  1. Scrape the last 24h of X job posts via the Apify REST API.
  2. Classify each post with deterministic ICP rules (see classify()).
  3. Merge into store.json, dedupe by URL, keep a rolling 7-day window.
  4. Render public/index.html from template.html.

Never destroys the last good board: if the Apify call fails hard, the script
exits non-zero WITHOUT writing, so the workflow skips the commit/deploy.

Env:
  APIFY_TOKEN  (required)  — Apify API token, provided as a GitHub Actions secret.
  WINDOW_DAYS  (optional)  — rolling retention window, default 7.
"""
import os, sys, json, re, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template.html")
STORE = os.path.join(HERE, "store.json")
OUT_DIR = os.path.join(HERE, "public")
OUT_X = os.path.join(OUT_DIR, "signal.html")      # X Signal board
OUT_OLJ = os.path.join(OUT_DIR, "onlinejobs.html")   # OnlineJobs leads
TEMPLATE_ALL = os.path.join(HERE, "template_all.html")
OUT_ALL = os.path.join(OUT_DIR, "index.html")        # consolidated "All Leads" = landing page
TEMPLATE_OLJ = os.path.join(HERE, "template_olj.html")
OLJ_STORE = os.path.join(HERE, "olj_leads.json")
TEMPLATE_FB = os.path.join(HERE, "template_fb.html")
FB_STORE = os.path.join(HERE, "fb_leads.json")
OUT_FB = os.path.join(OUT_DIR, "facebook.html")   # Facebook public-group leads
TEMPLATE_UPWORK = os.path.join(HERE, "template_upwork.html")
UPWORK_STORE = os.path.join(HERE, "upwork_leads.json")
OUT_UPWORK = os.path.join(OUT_DIR, "upwork.html")   # Upwork gig leads
TEMPLATE_LINKEDIN = os.path.join(HERE, "template_linkedin.html")
LINKEDIN_STORE = os.path.join(HERE, "linkedin_leads.json")
OUT_LINKEDIN = os.path.join(OUT_DIR, "linkedin.html")   # LinkedIn job leads
TEMPLATE_MYLEADS = os.path.join(HERE, "my_leads.html")
OUT_MYLEADS = os.path.join(OUT_DIR, "my_leads.html")    # client-side "My Leads" tracker
TEMPLATE_AGENCY = os.path.join(HERE, "template_agency.html")
OUT_AGENCY = os.path.join(OUT_DIR, "agency.html")       # small-business "Agency Fit" board

WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "7"))  # legacy; boards are now fresh-daily
ACTOR = "apidojo~tweet-scraper"
MAX_ITEMS = 200

# Shared client-side libraries, injected into every board via placeholders.
try:
    EXPORT_JS = open(os.path.join(HERE, "xlsx_export.js"), encoding="utf-8").read()
except Exception:
    EXPORT_JS = ""
try:
    TRACK_JS = open(os.path.join(HERE, "tracker.js"), encoding="utf-8").read()  # __TRACK_JS__
except Exception:
    TRACK_JS = ""
# Optional Cloudflare Worker sync for My Leads (repo Variables SYNC_URL / SYNC_TOKEN; "" = local only).
SYNC_URL = os.environ.get("SYNC_URL", "").strip()
SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "").strip()

def _sig(l):
    """Content signature for de-duplication: normalized job title + company. Two posts of the
    same job (even at different URLs) collapse to one; distinct titles stay separate."""
    t = re.sub(r"[^a-z0-9]+", " ", (l.get("jobTitle") or "").lower()).strip()
    c = re.sub(r"[^a-z0-9]+", " ", (l.get("company") or "").lower()).strip()
    return (t + "|" + c) if t else ""

def _dedup(leads):
    """Drop later leads whose content signature already appeared (keeps the first, so sort by
    score first to keep the best-scored copy)."""
    seen, out = set(), []
    for l in leads:
        s = _sig(l)
        if s and s in seen:
            continue
        if s:
            seen.add(s)
        out.append(l)
    return out

def agency_fit(l):
    """Leads [YOUR NAME] could take on as your business: a small business hiring directly — a named
    employer, a real brand type, and no recruiter/agency/staffing middleman."""
    company = (l.get("company") or "").strip()
    if not company or company.lower() in ("not listed", "unknown", "—", ""):
        return False                                  # must know the direct employer
    if (l.get("companyType") or "") not in ("eCommerce/DTC", "Local Business", "Coach/Education", "Other"):
        return False                                  # a real brand, not an agency
    blob = ((l.get("jobTitle") or "") + " " + (l.get("notes") or "") + " " + company).lower()
    if any(k in blob for k in ("recruit", "staffing", "talent acquisition", "on behalf of",
                               "our client", "client of", "agency", "headhunt", "bpo", "outsourc")):
        return False                                  # indirect (middleman) or an agency
    return True

def snapshot(new_leads, path, today_iso):
    """Fresh-daily store: dedup today's scrape by link AND content signature, then OVERWRITE the
    file — no 7-day accumulation. The file is kept only as a same-source failsafe: if tomorrow's
    scrape fails, the build falls back to this last-good snapshot instead of blanking the board."""
    seen, sigs, leads = set(), set(), []
    for l in new_leads:
        k = l.get("link")
        s = _sig(l)
        if not k or k in seen or (s and s in sigs):
            continue
        seen.add(k)
        if s:
            sigs.add(s)
        l["isNew"] = False
        leads.append(l)
    try:
        json.dump({"updated": today_iso, "leads": leads}, open(path, "w"), indent=2, ensure_ascii=False)
    except Exception:
        pass
    return leads

def _autodraft(leads, token, path, label, today_iso):
    """Optional: bake an application draft into each Hot lead via the Anthropic API, then
    re-write the snapshot so build_all picks the drafts up too. No-op if ANTHROPIC_API_KEY
    is unset; any failure degrades gracefully to no drafts."""
    if not token or not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return
    try:
        import draft_gen
        n = draft_gen.attach_drafts(leads, os.environ["ANTHROPIC_API_KEY"].strip())
        if n:
            json.dump({"updated": today_iso, "leads": leads}, open(path, "w"), indent=2, ensure_ascii=False)
            print(f"{label}: auto-drafted {n} hot leads.")
    except Exception as e:
        print(f"{label}: auto-draft skipped ({e}).")

SEARCH_TERMS = [
    '("hiring" OR "looking for" OR "need") ("ai automation" OR "voice ai" OR "voice agent") -is:retweet',
    '("looking for" OR "we need" OR "we\'re hiring" OR "looking to hire") ("chatbot developer" OR "conversational ai" OR "ai receptionist") -is:retweet',
    '"hiring" ("make.com expert" OR "zapier expert" OR "n8n automation" OR "workflow automation") -is:retweet',
    '("looking for" OR "need") ("ai cold caller" OR "ai appointment setter" OR "ai sdr") -is:retweet',
    '("looking for" OR "recommend" OR "need") ("automation agency" OR "voice ai agency" OR "ai automation agency") -is:retweet',
    '("hiring" OR "looking for" OR "need") ("arabic voice ai" OR "arabic chatbot" OR "gulf dialect voice") -is:retweet',
]

# ---------- classification rules (your business ICP; mirror of the manual rubric) ----------
OFF_GEO = ["nigeria","lagos","abuja","ibadan","ghana","accra","kenya","nairobi","uganda","kampala",
           "cameroon","tanzania","pretoria","south africa","johannesburg","india","bangalore","mumbai",
           "gujarat","pakistan","karachi","lahore","bangladesh","dhaka","indonesia","jakarta","kampala"]
PH = ["philippines","manila","makati","cebu","quezon","taguig","bgc, taguig","davao","pasig"," ph "," ph|"]
SUPP = ["supplement","supplements","nutra","peptide","peptides","nutrition","creatine","vitamin",
        "gummies","protein powder"]
# Above your business's $250k/mo client ceiling. NOTE: "7-figure" (annual revenue) is NOT here —
# ~$80k-800k/mo is often in-band. Only 8-fig+ and explicit MONTHLY $1M+ signal too-big.
BIG = ["8-figure","8 figure","9-figure","9 figure","$1m/month","$1m/mo","$1m per month","$1m+/month",
       "$1m+/mo","$4m/month","$4m per month","$100m","250k/month","$500k/month","8-9 figure"]
AGENCY_HINTS = ["agency","recruit","recruiter","talent","staffing","job board","job alert","we help brands",
                "vetted","headhunt","hr consult","outsourc","bpo","marketplace where"]
ROLE_NOT_SOLD = ["video editor","creative strategist","copywriter","graphic designer","email designer",
                 "ugc creator","ugc creators","operations manager","ops manager","photographer",
                 "web designer","animator","designer –","designer -"]
SPAM = ["crypto","web3","blockchain","nft","token","airdrop","domain","for sale on atom",
        "$msft","$odd","$lspd","day trader","defi","igaming","casino","betting","learn more:","daily digest",
        "prompt finds","here's how to grow","every year of my work life","20 years old","turned 22"]
SERVED = ["united states","us","u.s","usa","america","new york","los angeles","san francisco","miami","texas",
          "chicago","seattle","boston","fort lauderdale","denver","austin","united kingdom","uk","london",
          "manchester","canada","toronto","vancouver","ottawa","australia","sydney","melbourne","brisbane",
          "new zealand","auckland","ireland","dublin",
          "uae","dubai","abu dhabi","sharjah","emirates","saudi","ksa","riyadh","jeddah",
          "qatar","doha","kuwait","bahrain","manama","oman","muscat","gcc"]
ROLE_MAP = [
    (["arabic voice ai","arabic ivr","arabic chatbot","gulf dialect","arabic call center"], "Gulf Arabic Voice AI"),
    (["voice ai","voice agent","ivr","retell","vapi","voiceflow","call automation","ai receptionist"], "Voice AI"),
    (["chatbot","conversational ai","gpt integration"], "Chatbot / Conversational AI"),
    (["make.com","zapier","n8n","workflow automation","crm automation"], "Workflow Automation"),
    (["ai cold caller","ai appointment setter","ai sdr","lead follow-up"], "AI Outreach / SDR"),
]
COUNTRY_LABELS = [
    (["nigeria","lagos","abuja","ibadan"], "Nigeria"),
    (["philippines","manila","makati","cebu","taguig","davao"], "Philippines"),
    (["india","bangalore","mumbai","gujarat"], "India"),
    (["united states","new york","los angeles","san francisco","miami","texas","chicago","seattle",
      "fort lauderdale","denver","austin","washington","boston","atlanta","usa"], "US"),
    (["united kingdom","london","manchester","england","uk"], "UK"),
    (["canada","toronto","vancouver","ottawa"], "Canada"),
    (["australia","sydney","melbourne","brisbane"], "Australia"),
    (["uae","dubai","abu dhabi","sharjah","emirates"], "UAE"),
    (["saudi","ksa","riyadh","jeddah"], "Saudi Arabia"),
    (["qatar","doha"], "Qatar"), (["kuwait"], "Kuwait"),
    (["bahrain","manama"], "Bahrain"), (["oman","muscat"], "Oman"),
    (["south africa","pretoria","johannesburg"], "South Africa"),
    (["cameroon"], "Cameroon"), (["uganda","kampala"], "Uganda"),
]

def has(hay, needles): return any(n in hay for n in needles)

def guess_country(location, text):
    hay = (location + " " + text).lower()
    for keys, label in COUNTRY_LABELS:
        if has(hay, keys): return label
    return "Unknown"

def guess_role(text, search_term):
    t = text.lower()
    for keys, label in ROLE_MAP:
        if has(t, keys): return label
    st = search_term.lower()
    if "arabic" in st or "gulf" in st: return "Gulf Arabic Voice AI"
    if "voice" in st or "ivr" in st or "receptionist" in st: return "Voice AI"
    if "chatbot" in st or "conversational" in st: return "Chatbot / Conversational AI"
    if "make.com" in st or "zapier" in st or "n8n" in st: return "Workflow Automation"
    return "Multi / Other"

def classify(text, desc, location):
    """Return dict with role-independent fields: posterType, country, status, rejectReasons, hook."""
    t = text.lower(); d = desc.lower(); loc = location.lower(); both = t + " " + d + " " + loc
    reasons = []
    poster = "brand"
    if has(both, AGENCY_HINTS): poster = "agency"
    if has(d, ["job board","job alert","vacancies","we post","remote jobs","career tips","cv "]) or \
       re.search(r"jobs?\b", d) and "hiring" not in t:
        poster = "recruiter/jobboard"

    # spam / not-a-real-role
    if has(both, SPAM) or "affiliate link" in t or "disclaimer" in t:
        reasons.append("spam"); poster = "spam"
    # geography (hard)
    if has(both, PH): reasons.append("ph-local")
    if has(both, OFF_GEO) or "₦" in text or "ngn" in t or "must reside in lagos" in t:
        reasons.append("off-geo")
    # vertical / size
    if has(t, SUPP): reasons.append("supplement-health")
    if has(t, BIG): reasons.append("too-big")
    # role not sold
    if has(t, ROLE_NOT_SOLD): reasons.append("role-not-sold")
    # agency / recruiter hiring for themselves or clients-of-clients
    if poster in ("agency","recruiter/jobboard"):
        reasons.append("agency-competitor")
    # offering services / advice, not hiring
    if ("hiring" not in t and "we're hiring" not in t and "looking for" not in t
        and "we need" not in t and "looking to hire" not in t and "recommend" not in t):
        if any(k in t for k in ["dm me","my dms","i help","i edit","feel free to reach","book a call","let's connect"]):
            reasons.append("not-hiring")

    reasons = list(dict.fromkeys(reasons))  # de-dupe, keep order

    hard = {"spam","off-geo","ph-local","supplement-health","too-big","role-not-sold","agency-competitor","not-hiring"}
    if reasons and (set(reasons) & hard):
        status = "rejected"; hook = ""
    else:
        served = has(both, SERVED)
        country = guess_country(location, text)
        # brand, right kind of role, no hard rejects
        if served and country in ("US","UK","Canada","Australia",
                                   "UAE","Saudi Arabia","Qatar","Kuwait","Bahrain","Oman"):
            status = "prospect"
            hook = "Real brand hiring in-market for a your business-mapped role — verify size & contact route, then pitch agency-vs-hire."
        else:
            status = "review"
            hook = "Plausible fit but country/brand unconfirmed — verify it's served-geo and a real product brand before outreach."
    return {"posterType": poster, "status": status, "rejectReasons": reasons, "hook": hook}

# ---------- Apify ----------
def scrape(token, since_date):
    payload = {
        "searchTerms": SEARCH_TERMS, "sort": "Latest", "tweetLanguage": "en",
        "maxItems": MAX_ITEMS, "start": since_date, "includeSearchTerms": True,
    }
    url = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?token={token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())

# ---------- transform ----------
def parse_dt(s):
    try: return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except Exception: return None

def to_post(item, today_iso):
    dt = parse_dt(item.get("createdAt", ""))
    if not dt: return None
    text = (item.get("text") or item.get("fullText") or "").strip()
    if not text: return None
    a = item
    handle = a.get("author.userName") or (a.get("author") or {}).get("userName") or ""
    loc = a.get("author.location") or (a.get("author") or {}).get("location") or ""
    desc = a.get("author.description") or (a.get("author") or {}).get("description") or ""
    fol = a.get("author.followers") or (a.get("author") or {}).get("followers") or 0
    st = a.get("searchTerm", "")
    role = guess_role(text, st)
    c = classify(text, desc, loc)
    snippet = re.sub(r"\s+", " ", text)[:220].strip()
    return {
        "date": dt.strftime("%b %-d"), "iso": dt.date().isoformat(), "platform": "X",
        "handle": handle, "location": (loc or "").strip(), "followers": int(fol or 0),
        "role": role, "posterType": c["posterType"], "country": guess_country(loc, text),
        "status": c["status"], "rejectReasons": c["rejectReasons"], "snippet": snippet,
        "hook": c["hook"], "url": a.get("url", ""), "firstSeen": today_iso,
    }

def recount(posts):
    def tally(key):
        d = {}
        for p in posts: d[p[key]] = d.get(p[key], 0) + 1
        return dict(sorted(d.items(), key=lambda kv: -kv[1]))
    by_status = {"prospect": 0, "review": 0, "rejected": 0}
    for p in posts: by_status[p["status"]] = by_status.get(p["status"], 0) + 1
    by_reason = {}
    for p in posts:
        for r in p.get("rejectReasons", []): by_reason[r] = by_reason.get(r, 0) + 1
    by_reason = dict(sorted(by_reason.items(), key=lambda kv: -kv[1]))
    country = dict(list(tally("country").items())[:12])
    return {"byStatus": by_status, "byRole": tally("role"),
            "byRejectReason": by_reason, "byCountryTop": country}

def main():
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        print("ERROR: APIFY_TOKEN not set — refusing to run (keeps last good board).", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    today_iso = now.date().isoformat()
    since = (now - timedelta(days=1)).date().isoformat()

    try:
        items = scrape(token, since)
    except Exception as e:
        print(f"ERROR: Apify scrape failed ({e}). Keeping last good board.", file=sys.stderr)
        sys.exit(1)
    print(f"Scraped {len(items)} raw items since {since}.")

    # fresh daily: keep only THIS run's scrape (last 24h), dedup by url, no history kept
    seen, posts = set(), []
    for it in items:
        p = to_post(it, today_iso)
        if not p or not p["url"] or p["url"] in seen: continue
        seen.add(p["url"]); p["isNew"] = False
        posts.append(p)
    # sort: prospect/review first, then newest
    rank = {"prospect": 0, "review": 1, "rejected": 2}
    posts.sort(key=lambda p: p["iso"], reverse=True)
    posts.sort(key=lambda p: rank.get(p["status"], 3))
    new_today = len(posts)

    counts = recount(posts)
    window = _fmt(today_iso)
    data = {"generatedNote": "X job posts, last 24h (fresh daily)",
            "totalPosts": len(posts), "newToday": new_today, "counts": counts, "posts": posts}

    # write today's snapshot (failsafe only) + html
    json.dump({"updated": today_iso, "posts": posts},
              open(STORE, "w"), indent=2, ensure_ascii=False)
    tpl = open(TEMPLATE, encoding="utf-8").read()
    html = (tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
               .replace("__UPDATED__", _fmt(today_iso))
               .replace("__WINDOW__", window)
               .replace("__EXPORT_JS__", EXPORT_JS)
               .replace("__TRACK_JS__", TRACK_JS).replace("__SYNC_URL__", SYNC_URL).replace("__SYNC_TOKEN__", SYNC_TOKEN))
    os.makedirs(OUT_DIR, exist_ok=True)
    open(OUT_X, "w", encoding="utf-8").write(html)
    print(f"Built {OUT_X}: {len(posts)} posts (last 24h), {counts['byStatus']}.")
    build_olj(os.environ.get("APIFY_TOKEN", "").strip(), today_iso)
    build_fb(os.environ.get("APIFY_TOKEN", "").strip(), today_iso)
    build_upwork(os.environ.get("APIFY_TOKEN", "").strip(), today_iso)
    build_linkedin(os.environ.get("APIFY_TOKEN", "").strip(), today_iso)
    build_all(today_iso)
    build_myleads(today_iso)
    build_agency(today_iso)

def build_myleads(today_iso):
    """Static 'My Leads' page (my_leads.html). No server data: it renders entirely from the
    visitor's own localStorage via tracker.js. We just fill the shared libs + the date."""
    if not os.path.exists(TEMPLATE_MYLEADS):
        print("My Leads page skipped (my_leads.html missing).")
        return
    tpl = open(TEMPLATE_MYLEADS, encoding="utf-8").read()
    html = (tpl.replace("__UPDATED__", _fmt(today_iso))
               .replace("__EXPORT_JS__", EXPORT_JS).replace("__TRACK_JS__", TRACK_JS).replace("__SYNC_URL__", SYNC_URL).replace("__SYNC_TOKEN__", SYNC_TOKEN))
    os.makedirs(OUT_DIR, exist_ok=True)
    open(OUT_MYLEADS, "w", encoding="utf-8").write(html)
    print(f"Built {OUT_MYLEADS}: My Leads (client-side localStorage).")

def build_agency(today_iso):
    """'Agency Fit' board (agency.html): the subset of leads that are small businesses hiring
    directly, which you could take on as a retainer. Read-only over the per-source stores."""
    if not os.path.exists(TEMPLATE_AGENCY):
        print("Agency board skipped (template_agency.html missing).")
        return
    leads = []
    for p in (OLJ_STORE, UPWORK_STORE, LINKEDIN_STORE, FB_STORE):
        try:
            leads += json.load(open(p)).get("leads", [])
        except Exception:
            pass
    leads = [l for l in leads if agency_fit(l)]
    leads.sort(key=lambda l: (-l.get("score", 0), l.get("datePosted", "")))
    leads = _dedup(leads)
    pkey = os.environ.get("PROSPEO_API_KEY", "").strip()
    if not pkey:
        print("Agency: Prospeo enrichment OFF (PROSPEO_API_KEY not set / empty in this run).")
    else:
        try:
            import prospeo_enrich
            n = prospeo_enrich.enrich_leads(leads, pkey)
            msg = f"Agency: Prospeo enriched {n} of {len(leads)} leads with a decision-maker."
            if not n:
                msg += f" 0 matched; last error: {prospeo_enrich.last_error() or 'none (companies not found in Prospeo)'}."
            print(msg)
        except Exception as e:
            print(f"Agency: Prospeo enrichment failed ({e}).")
    tok = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if tok:
        try:
            import draft_gen
            n = draft_gen.attach_pitches(leads, tok)
            if n:
                print(f"Agency: auto-drafted {n} agency pitches.")
        except Exception as e:
            print(f"Agency: pitch auto-draft skipped ({e}).")
    data = {"leads": leads, "newToday": sum(1 for l in leads if l.get("isNew"))}
    tpl = open(TEMPLATE_AGENCY, encoding="utf-8").read()
    html = (tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
               .replace("__UPDATED__", _fmt(today_iso)).replace("__EXPORT_JS__", EXPORT_JS)
               .replace("__TRACK_JS__", TRACK_JS).replace("__SYNC_URL__", SYNC_URL).replace("__SYNC_TOKEN__", SYNC_TOKEN))
    os.makedirs(OUT_DIR, exist_ok=True)
    open(OUT_AGENCY, "w", encoding="utf-8").write(html)
    print(f"Built {OUT_AGENCY}: {len(leads)} agency-fit leads.")

def build_all(today_iso):
    """Consolidated 'All Leads' landing page (index.html): merges the lead stores from every
    source into one ranked board. Read-only over the per-source stores; writes index.html."""
    if not os.path.exists(TEMPLATE_ALL):
        print("All-Leads board skipped (template_all.html missing).")
        return
    leads = []
    for p in (OLJ_STORE, UPWORK_STORE, LINKEDIN_STORE, FB_STORE):
        try:
            leads += json.load(open(p)).get("leads", [])
        except Exception:
            pass
    try:  # fold in X's actionable (prospect/review) posts as leads
        for x in json.load(open(STORE)).get("posts", []):
            if x.get("status") in ("prospect", "review"):
                leads.append({
                    "source": "X", "jobTitle": (x.get("snippet") or x.get("role") or "X hiring post")[:80],
                    "company": ("@" + x.get("handle", "")) if x.get("handle") else "Not listed",
                    "companyType": x.get("posterType", "") or "", "salary": "—", "salaryUsd": None,
                    "datePosted": x.get("iso", ""), "link": x.get("url", ""),
                    "score": 8 if x.get("status") == "prospect" else 6,
                    "priority": "high" if x.get("status") == "prospect" else "normal",
                    "service": [x.get("role", "")] if x.get("role") else [], "country": x.get("country", ""),
                    "salesStage": "Lead Qualification", "why": x.get("hook", ""),
                    "notes": x.get("snippet", ""), "isNew": x.get("isNew", False)})
    except Exception:
        pass
    leads.sort(key=lambda l: (-l.get("score", 0), l.get("datePosted", "")))
    leads = _dedup(leads)  # collapse the same job scraped from more than one source
    data = {"leads": leads, "newToday": sum(1 for l in leads if l.get("isNew"))}
    tpl = open(TEMPLATE_ALL, encoding="utf-8").read()
    html = (tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
               .replace("__UPDATED__", _fmt(today_iso)).replace("__EXPORT_JS__", EXPORT_JS)
               .replace("__TRACK_JS__", TRACK_JS).replace("__SYNC_URL__", SYNC_URL).replace("__SYNC_TOKEN__", SYNC_TOKEN))
    open(OUT_ALL, "w", encoding="utf-8").write(html)
    by = {}
    for l in leads:
        by[l["source"]] = by.get(l["source"], 0) + 1
    print(f"Built {OUT_ALL}: {len(leads)} total leads across {by}.")

def build_linkedin(token, today_iso):
    """LinkedIn job-leads board (linkedin.html). Same rolling 7-day pattern; searches remote
    marketing roles via linkedin_scrape.SEARCH_KEYWORDS. Falls back to existing store on failure."""
    if not os.path.exists(TEMPLATE_LINKEDIN):
        print("LinkedIn board skipped (template_linkedin.html missing).")
        return
    leads = []
    try:
        import linkedin_scrape
        new = linkedin_scrape.run(token) if token else []
        leads = snapshot(new, LINKEDIN_STORE, today_iso)
        print(f"LinkedIn: {len(leads)} leads (last 24h, fresh daily).")
        _autodraft(leads, token, LINKEDIN_STORE, "LinkedIn", today_iso)
    except Exception as e:
        print(f"LinkedIn scrape failed ({e}); rendering existing store.")
        try:
            leads = json.load(open(LINKEDIN_STORE)).get("leads", [])
        except Exception:
            leads = []
    for l in leads:
        l.setdefault("source", "LinkedIn")
    data = {"leads": leads, "newToday": sum(1 for l in leads if l.get("isNew"))}
    tpl = open(TEMPLATE_LINKEDIN, encoding="utf-8").read()
    html = (tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
               .replace("__UPDATED__", _fmt(today_iso)).replace("__EXPORT_JS__", EXPORT_JS)
               .replace("__TRACK_JS__", TRACK_JS).replace("__SYNC_URL__", SYNC_URL).replace("__SYNC_TOKEN__", SYNC_TOKEN))
    open(OUT_LINKEDIN, "w", encoding="utf-8").write(html)
    print(f"Built {OUT_LINKEDIN}: {len(leads)} LinkedIn leads.")

def build_upwork(token, today_iso):
    """Upwork gig-leads board (upwork.html). Same rolling 7-day pattern; searches marketing
    keywords via upwork_scrape.SEARCH_QUERIES. Falls back to the existing store on failure."""
    if not os.path.exists(TEMPLATE_UPWORK):
        print("Upwork board skipped (template_upwork.html missing).")
        return
    leads = []
    try:
        import upwork_scrape
        new = upwork_scrape.run(token) if token else []
        leads = snapshot(new, UPWORK_STORE, today_iso)
        print(f"Upwork: {len(leads)} leads (last 24h, fresh daily).")
        _autodraft(leads, token, UPWORK_STORE, "Upwork", today_iso)
    except Exception as e:
        print(f"Upwork scrape failed ({e}); rendering existing store.")
        try:
            leads = json.load(open(UPWORK_STORE)).get("leads", [])
        except Exception:
            leads = []
    for l in leads:
        l.setdefault("source", "Upwork")
    data = {"leads": leads, "newToday": sum(1 for l in leads if l.get("isNew"))}
    tpl = open(TEMPLATE_UPWORK, encoding="utf-8").read()
    html = (tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
               .replace("__UPDATED__", _fmt(today_iso)).replace("__EXPORT_JS__", EXPORT_JS)
               .replace("__TRACK_JS__", TRACK_JS).replace("__SYNC_URL__", SYNC_URL).replace("__SYNC_TOKEN__", SYNC_TOKEN))
    open(OUT_UPWORK, "w", encoding="utf-8").write(html)
    print(f"Built {OUT_UPWORK}: {len(leads)} Upwork leads.")

def build_fb(token, today_iso):
    """Facebook public-group leads board (facebook.html). Same rolling 7-day pattern as OnlineJobs;
    scrapes the groups listed in fb_scrape.GROUP_URLS. Falls back to the existing store on failure."""
    if not os.path.exists(TEMPLATE_FB):
        print("Facebook board skipped (template_fb.html missing).")
        return
    leads = []
    try:
        import fb_scrape
        new = fb_scrape.run(token) if token else []
        leads = snapshot(new, FB_STORE, today_iso)
        print(f"Facebook: {len(leads)} leads (last 24h, fresh daily).")
        _autodraft(leads, token, FB_STORE, "Facebook", today_iso)
    except Exception as e:
        print(f"Facebook scrape failed ({e}); rendering existing store.")
        try:
            leads = json.load(open(FB_STORE)).get("leads", [])
        except Exception:
            leads = []
    for l in leads:
        l.setdefault("source", "Facebook")
    data = {"leads": leads, "newToday": sum(1 for l in leads if l.get("isNew"))}
    tpl = open(TEMPLATE_FB, encoding="utf-8").read()
    html = (tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
               .replace("__UPDATED__", _fmt(today_iso)).replace("__EXPORT_JS__", EXPORT_JS)
               .replace("__TRACK_JS__", TRACK_JS).replace("__SYNC_URL__", SYNC_URL).replace("__SYNC_TOKEN__", SYNC_TOKEN))
    open(OUT_FB, "w", encoding="utf-8").write(html)
    print(f"Built {OUT_FB}: {len(leads)} Facebook leads.")

def build_olj(token, today_iso):
    """OnlineJobs leads board (index.html / landing page). Mirrors X: scrape the last 24h,
    qualify + score, merge into a rolling 7-day store, render. Falls back to the existing
    store if the scrape fails, so a bad day never blanks the board."""
    if not os.path.exists(TEMPLATE_OLJ):
        print("OnlineJobs board skipped (template_olj.html missing).")
        return
    leads = []
    try:
        import olj_scrape
        new = olj_scrape.run(token) if token else []
        leads = snapshot(new, OLJ_STORE, today_iso)
        print(f"OnlineJobs: {len(leads)} leads (last 24h, fresh daily).")
        _autodraft(leads, token, OLJ_STORE, "OnlineJobs", today_iso)
    except Exception as e:
        print(f"OnlineJobs scrape failed ({e}); rendering existing store.")
        try:
            leads = json.load(open(OLJ_STORE)).get("leads", [])
        except Exception:
            leads = []
    for l in leads:
        l.setdefault("source", "OnlineJobs.ph")
    data = {"leads": leads, "newToday": sum(1 for l in leads if l.get("isNew"))}
    tpl = open(TEMPLATE_OLJ, encoding="utf-8").read()
    html = (tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
               .replace("__UPDATED__", _fmt(today_iso)).replace("__EXPORT_JS__", EXPORT_JS)
               .replace("__TRACK_JS__", TRACK_JS).replace("__SYNC_URL__", SYNC_URL).replace("__SYNC_TOKEN__", SYNC_TOKEN))
    open(OUT_OLJ, "w", encoding="utf-8").write(html)
    print(f"Built {OUT_OLJ}: {len(leads)} OnlineJobs leads.")

def _fmt(iso):
    try: return datetime.strptime(iso, "%Y-%m-%d").strftime("%b %-d, %Y")
    except Exception: return iso

if __name__ == "__main__":
    main()
