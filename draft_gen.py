#!/usr/bin/env python3
"""Auto-draft specialist applications for Hot leads via the Anthropic Messages API.

Runs inside the daily build ONLY when ANTHROPIC_API_KEY is set. Drafts are baked into the
board (lead['draft'] = {subject, body, fit}) so they appear with a Copy button, no
pasting. Failure is graceful: a bad API call just leaves that lead undrafted.

Cost control: only Hot leads (score >= 7) are drafted, capped at MAX_DRAFTS across the whole
run. The voice/claims system prompt is prompt-cached so repeat calls in a run are ~0.1x cost.

Env:
  ANTHROPIC_API_KEY  (required to draft; absent = feature off)
  DRAFT_MODEL        (optional, default claude-haiku-4-5 — cheap; set to claude-sonnet-5 etc.)
  MAX_DRAFTS         (optional, default 60 — hard ceiling per daily run)
"""
import os, json, re, urllib.request, urllib.error

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("DRAFT_MODEL", "claude-haiku-4-5").strip()
HOT_MIN = 7
_BUDGET = [int(os.environ.get("MAX_DRAFTS", "60"))]

# Stable essentials mirrored from .agents/your notes.md + the your AI assistant skill. Kept here
# (not a copy of the whole brain) because applications only need voice + claims + capability +
# the apply-as-specialist framing, all of which are stable. Update if those rules change.
SYSTEM = "You draft a short job/freelance application for [YOUR NAME], a freelance [YOUR ROLE] (portfolio: [YOUR PORTFOLIO LINK]), applying to hiring posts (Upwork, OnlineJobs.ph, LinkedIn, Facebook, X). [YOUR NAME] does the work personally.\n\nThis is a fast first-pass draft written from the post alone (no website research). Keep it a strong proposal to refine.\n\nPROOF (EDIT THIS): use ONLY your real, documented results. Replace the line below with your own wins and numbers, then match the closest one to each post's channel and industry. Never invent a number.\n- [Add your own results here, for example: \"grew a skincare brand from $0 to $250K/mo at 4x ROAS\", \"took an email list from 12k to $40k/mo\".]\n\nLAYOUT (use \\n line breaks): line 1 is the greeting \"Hi [Employer],\" (company name if it is a real name, otherwise \"Hi there,\"). Then a blank line. Then the body opens \"I'm [YOUR NAME], a [YOUR ROLE].\" then a specific line about their post, then one short story of a relevant result with a few real numbers, then why you are a strong fit, then a low-friction close with your portfolio link. Then a blank line, \"Best regards,\" on its own line, a blank line, then \"[YOUR NAME]\".\n\nSTYLE (hard rules):\n- Short and warm. Simple everyday words. Read like a real person wrote it, not AI: contractions, short sentences, no inflated adjectives, no rule-of-three lists, no \"not just X, it's Y\", no corporate filler, no wrap-up sentence.\n- NO EM DASHES anywhere (use commas, periods, colons or parentheses).\n- Banned words: leverage, synergy, best-in-class, \"I hope this finds you well\", \"I came across your profile\".\n- No rates, pricing or packages. No hype, no guarantees.\n\nReturn ONLY a JSON object with these keys:\n- \"subject\": one punchy, short, warm subject line, plain words, no hype, no em dashes; include a relevant number when one fits.\n- \"body\": the application, 140 to 220 words, laid out with \\n as above.\n- \"fit\": one short line on the industry/channel you matched and any flag (for example \"not a marketing role, skip\").\nOutput the JSON object only, with no text before or after it."


def _draft_one(lead, token):
    ct = lead.get("companyType")
    parts = [
        "Draft my application for this job post.", "",
        "Source: " + str(lead.get("source", "")),
        "Role / Title: " + str(lead.get("jobTitle", "")),
        "Company: " + str(lead.get("company", "")) + (" (" + ct + ")" if ct else ""),
        "Pay: " + str(lead.get("salary", "n/a")),
        "Country: " + str(lead.get("country", "")),
        "Service tags: " + ", ".join(lead.get("service") or []),
        "Job details: " + (str(lead.get("notes") or lead.get("why") or "")[:1000]),
    ]
    payload = {
        "model": MODEL, "max_tokens": 1024,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "\n".join(parts)}],
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-api-key": token,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    obj = _parse_json(text)
    if not obj:
        return None
    body = (obj.get("body") or "").strip()
    if not body:
        return None
    return {"subject": (obj.get("subject") or "").strip(), "body": body,
            "fit": (obj.get("fit") or "").strip()}


def _parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


SYSTEM_PITCH = "You draft a short warm cold-outreach note for [YOUR NAME], who offers done-for-you marketing (portfolio: [YOUR PORTFOLIO LINK]). The recipient is a small business [YOUR NAME] wants to work with.\n\nCRITICAL: the role details you are given are PRIVATE background to infer their likely problem. NEVER reveal or imply you saw a job post or that they are hiring. Write as if you simply understand their space and want to help.\n\nPROOF (EDIT THIS): use ONLY your real results. Replace the line below with your own wins and numbers, then match the closest one to their space. Never invent a number.\n- [Add your own results here.]\n\nLAYOUT (use \\n line breaks): greet the contact by first name if one is given (\"Hi Sarah,\"), otherwise use the company name (\"Hi [Employer],\"). Then a blank line. Then the body opens \"I'm [YOUR NAME].\" then names their likely problem and that you can help, then one relevant result with a few real numbers, then a soft next step (a quick call or a short free plan, no pricing), then your portfolio link. Then a blank line, \"Best regards,\" on its own line, a blank line, then \"[YOUR NAME]\".\n\nSTYLE: short, warm, plain, human not AI, NO EM DASHES, no banned words, no pricing, no hype, no guarantees.\n\nReturn ONLY a JSON object with keys \"subject\", \"body\", \"fit\". Output the JSON object only."

_PITCH_BUDGET = [int(os.environ.get("MAX_PITCHES", "40"))]


def _pitch_one(lead, token):
    ct = lead.get("companyType")
    c = lead.get("contact") or {}
    reach = ""
    if c.get("name"):
        reach = c["name"] + (" (" + c["title"] + ")" if c.get("title") else "")
    parts = [
        "Draft my outreach note to this small business.", "",
        "Source: " + str(lead.get("source", "")),
        "Their likely need (background only, do not mention): " + str(lead.get("jobTitle", "")),
        "Company: " + str(lead.get("company", "")) + (" (" + ct + ")" if ct else ""),
        "Reach out to (greet this person by first name): " + (reach or "unknown, greet the company"),
        "Country: " + str(lead.get("country", "")),
        "Service tags: " + ", ".join(lead.get("service") or []),
        "Context (background only, do not mention): " + (str(lead.get("notes") or lead.get("why") or "")[:1000]),
    ]
    payload = {
        "model": MODEL, "max_tokens": 1024,
        "system": [{"type": "text", "text": SYSTEM_PITCH, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "\n".join(parts)}],
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-api-key": token,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    obj = _parse_json(text)
    if not obj:
        return None
    body = (obj.get("body") or "").strip()
    if not body:
        return None
    return {"subject": (obj.get("subject") or "").strip(), "body": body,
            "fit": (obj.get("fit") or "").strip()}


def attach_pitches(leads, token):
    """Mutate `leads` in place, adding l['pitch'] (an agency outreach pitch) to each agency-fit
    lead, highest score first, until the MAX_PITCHES budget for this run is spent. Agency leads
    are already a curated subset, so there is no score floor. Returns the count drafted."""
    if not token or _PITCH_BUDGET[0] <= 0:
        return 0
    n = 0
    for l in sorted(leads, key=lambda x: -(x.get("score") or 0)):
        if _PITCH_BUDGET[0] <= 0:
            break
        if l.get("pitch"):
            continue
        try:
            d = _pitch_one(l, token)
        except Exception:
            continue
        if d:
            l["pitch"] = d
            n += 1
            _PITCH_BUDGET[0] -= 1
    return n


def attach_drafts(leads, token):
    """Mutate `leads` in place, adding l['draft'] for Hot leads (score >= 7), newest/highest
    first, until the shared MAX_DRAFTS budget for this run is spent. Returns the count drafted."""
    if not token or _BUDGET[0] <= 0:
        return 0
    n = 0
    for l in sorted(leads, key=lambda x: -(x.get("score") or 0)):
        if _BUDGET[0] <= 0:
            break
        if (l.get("score") or 0) < HOT_MIN or l.get("draft"):
            continue
        try:
            d = _draft_one(l, token)
        except Exception:
            continue
        if d:
            l["draft"] = d
            n += 1
            _BUDGET[0] -= 1
    return n
