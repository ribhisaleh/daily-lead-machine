#!/usr/bin/env python3
"""Enrich agency-fit leads with the decision-maker to reach out to, via the Prospeo API.

Two calls per lead:
  1. search-person: find the decision-maker at the lead's company (filter by company name +
     senior seniority levels). 1 credit per search that returns results. Email/mobile are NOT
     returned here, only a person_id.
  2. enrich-person: reveal that person's verified email (free) and mobile (enrich_mobile=true,
     +10 credits) from the person_id.

Off unless PROSPEO_API_KEY is set. Capped at MAX_ENRICH search attempts per run so credit
spend is bounded. Any failure degrades gracefully to no contact on that lead.

Env:
  PROSPEO_API_KEY  (required to enrich; absent = feature off)
  PROSPEO_MOBILE   (optional, default "1"; "0" = email only, skip the 10-credit mobile lookup)
  MAX_ENRICH       (optional, default "25"; hard ceiling of leads searched per daily run)
"""
import os, json, urllib.request, urllib.error

SEARCH_URL = "https://api.prospeo.io/search-person"
ENRICH_URL = "https://api.prospeo.io/enrich-person"
WANT_MOBILE = os.environ.get("PROSPEO_MOBILE", "1").strip().lower() not in ("0", "false", "no", "")
_BUDGET = [int(os.environ.get("MAX_ENRICH", "25"))]

# Decision-maker seniority levels Prospeo recognises. We pull owners/founders and senior leaders,
# then rank client-side so the true decision-maker (owner/founder/CEO) wins over a junior manager.
SENIORITY = ["Founder/Owner", "C-Suite", "Partner", "Vice President", "Head", "Director"]
_RANK = [
    ("owner", "founder", "co-founder", "cofounder", "ceo", "chief executive",
     "president", "managing director", "proprietor", "principal"),
    ("cmo", "chief marketing", "vp marketing", "vp of marketing", "head of marketing",
     "marketing director", "director of marketing", "head of growth", "growth"),
    ("marketing", "brand", "ecommerce", "e-commerce", "digital", "demand"),
]
BAD_COMPANY = ("", "not listed", "unknown", "n/a", "none", "-", "—", "confidential", "private")

_LAST_ERR = [None]


def last_error():
    """The most recent per-lead error string (e.g. an HTTP 401 for a bad key), or None."""
    return _LAST_ERR[0]


def _post(url, key, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-KEY": key})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = " " + e.read().decode()[:200]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} from {url.rsplit('/', 1)[-1]}{detail}")


def _rank(title):
    t = (title or "").lower()
    for i, kws in enumerate(_RANK):
        if any(k in t for k in kws):
            return i
    return len(_RANK)


def _find_person(lead, key):
    """search-person for the best decision-maker at the lead's company; None if none found."""
    company = (lead.get("company") or "").strip()
    if company.lower() in BAD_COMPANY:
        return None
    body = {"page": 1, "filters": {
        "company": {"names": {"include": [company]}},
        "person_seniority": {"include": SENIORITY}}}
    data = _post(SEARCH_URL, key, body)
    if data.get("error"):
        return None
    people = [r.get("person", {}) for r in (data.get("results") or []) if r.get("person")]
    if not people:
        return None
    people.sort(key=lambda p: _rank(p.get("job_title")))
    return people[0]


def _enrich(person, key):
    """enrich-person by person_id -> verified email + (optionally) mobile."""
    pid = person.get("person_id")
    if not pid:
        return None
    body = {"only_verified_email": True, "enrich_mobile": WANT_MOBILE, "data": {"person_id": pid}}
    data = _post(ENRICH_URL, key, body)
    if data.get("error"):
        return None
    return data.get("person") or {}


def _contact_from(person, enriched):
    e = enriched or {}
    email = ((e.get("email") or {}).get("email")) or ""
    mob = e.get("mobile") or {}
    phone = mob.get("mobile_international") or mob.get("mobile") or ""
    name = (e.get("full_name") or person.get("full_name")
            or (str(person.get("first_name") or "") + " " + str(person.get("last_name") or "")).strip())
    title = e.get("current_job_title") or person.get("job_title") or ""
    linkedin = e.get("linkedin_url") or person.get("linkedin_url") or ""
    return {"name": name.strip(), "title": title.strip(), "email": email.strip(),
            "phone": phone.strip(), "linkedin": linkedin.strip()}


def enrich_leads(leads, key):
    """Attach lead['contact'] = {name,title,email,phone,linkedin} to agency leads with a real
    company name, highest score first, until the MAX_ENRICH search budget is spent. Records the
    person we found even when no email/phone was revealed (so you still know who to reach).
    Returns the count enriched."""
    if not key or _BUDGET[0] <= 0:
        return 0
    n = 0
    for l in sorted(leads, key=lambda x: -(x.get("score") or 0)):
        if _BUDGET[0] <= 0:
            break
        if l.get("contact") or (l.get("company") or "").strip().lower() in BAD_COMPANY:
            continue
        _BUDGET[0] -= 1  # the search below costs a credit whether or not it resolves
        try:
            person = _find_person(l, key)
            if not person:
                continue
            enriched = _enrich(person, key) or {}
        except Exception as e:
            _LAST_ERR[0] = str(e)
            continue
        l["contact"] = _contact_from(person, enriched)
        n += 1
    return n
