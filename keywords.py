# ============================================================
#   EDIT THIS FILE. It tells your machine who to look for.
#   Retargeted for: AI automation & voice agents (general) +
#   Gulf Arabic voice AI (niche track).
#
#   Two keyword sets, both searched by every source. Anything
#   that matches CORE_GULF gets tagged "Gulf Arabic Voice AI" in
#   the "service" field on the board (searchable/filterable/CSV
#   exportable there) -- CORE_GENERAL matches are tagged normally.
#   Keep the quotes and commas exactly as shown.
# ============================================================

# 1) CORE (general track) -- keep a post ONLY if it mentions one of these.
CORE_GENERAL = [
    "ai automation", "automate my workflow", "voice agent", "voice ai",
    "ai receptionist", "ai cold caller", "ai appointment setter", "ai sdr",
    "chatbot developer", "build me a chatbot", "conversational ai",
    "make.com expert", "zapier expert", "n8n automation", "n8n expert",
    "workflow automation", "ivr", "call automation", "crm automation",
    "lead follow-up automation", "customer support automation",
    "retell ai", "vapi", "voiceflow", "gpt integration",
    # ...add your own words here...
]

# 2) CORE (Gulf Arabic voice AI track) -- niche track, tagged separately.
CORE_GULF = [
    "arabic voice ai", "arabic ivr", "arabic chatbot", "gulf dialect",
    "arabic call center", "arabic virtual assistant", "arabic ai agent",
    "خدمة عملاء بالذكاء الاصطناعي", "روبوت محادثة", "مساعد صوتي",
    # ...add your own words here...
]

# Combined list -- used wherever the pipeline just needs "is this in-scope at all".
CORE = CORE_GENERAL + CORE_GULF

# 3) REJECT -- drop a post if its TITLE mentions one of these words.
#    Work you do NOT do.
REJECT = [
    "video editor", "graphic designer", "data entry", "bookkeeper",
    "web developer", "wordpress developer", "seo specialist",
    "content writer", "copywriter only", "photographer",
    # ...add your own words here...
]

# 4) PLACES -- the regions whose clients you want.
#    Choose from: "US", "UK", "Canada", "Australia", "New Zealand",
#    "UAE", "Saudi Arabia", "Qatar", "Kuwait", "Bahrain", "Oman".
PLACES = ["US", "UK", "Canada", "Australia",
          "UAE", "Saudi Arabia", "Qatar", "Kuwait", "Bahrain", "Oman"]
