#!/usr/bin/env python3
"""Quick local smoke test for Prospeo decision-maker enrichment.

Runs the SAME search -> rank -> enrich path the daily build uses (prospeo_enrich.py), against
a few company names you pass on the command line, and prints who it found + their email/phone.
Use it to sanity-check match quality before the 7am build runs it at scale.

Your key stays on your machine (read from the environment, never printed).

Usage:
  PROSPEO_API_KEY=your_key python3 prospeo_test.py "Acme Co." "Northwind Brand"
  PROSPEO_MOBILE=0 PROSPEO_API_KEY=your_key python3 prospeo_test.py "Acme Co"   # skip phone

Each company costs ~1 search credit + email (free); +10 credits for the mobile lookup unless
PROSPEO_MOBILE=0. So the line above is a handful of credits, not a full run.
"""
import os, sys, prospeo_enrich as pe

def main():
    key = os.environ.get("PROSPEO_API_KEY", "").strip()
    if not key:
        sys.exit("Set PROSPEO_API_KEY in the environment first (it is never printed).")
    companies = sys.argv[1:]
    if not companies:
        sys.exit('Pass one or more company names, e.g.  python3 prospeo_test.py "Acme Co."')
    print(f"Mobile lookup: {'ON (+10 credits each)' if pe.WANT_MOBILE else 'OFF'}\n")
    for name in companies:
        print(f"── {name}")
        lead = {"company": name, "score": 1}
        try:
            person = pe._find_person(lead, key)
        except Exception as e:
            print(f"   search failed: {e}\n"); continue
        if not person:
            print("   no decision-maker found (company not matched, or no senior contact)\n"); continue
        print(f"   found: {person.get('full_name','?')}  |  {person.get('job_title','?')}")
        try:
            enriched = pe._enrich(person, key) or {}
        except Exception as e:
            print(f"   enrich failed: {e}\n"); continue
        c = pe._contact_from(person, enriched)
        print(f"   email:    {c['email'] or '(not revealed)'}")
        print(f"   phone:    {c['phone'] or '(not revealed)'}")
        print(f"   linkedin: {c['linkedin'] or '(none)'}\n")

if __name__ == "__main__":
    main()
