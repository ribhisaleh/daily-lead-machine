THE DAILY LEAD MACHINE -- STARTER CODE PACK
===========================================

You do NOT need to read this code or understand it.
Follow "The Complete Build Manual" (Material 03) step by step.

Quick map of what is here:
  keywords.py .......... THE ONE FILE YOU EDIT. Your services, rejects, regions.
  scrape_and_build.py .. the main program that builds your board each morning.
  *_scrape.py .......... the readers for each source (OnlineJobs, Upwork, etc.).
  template_*.html ...... the look of your board pages.
  draft_gen.py ......... (optional) writes application drafts. Add YOUR results
                         inside it where it says PROOF (EDIT THIS).
  prospeo_enrich.py .... (optional) finds a decision-maker's email/phone.
  cloudflare-worker.js . (optional) syncs saved leads across devices.
  daily-workflow.yml ... the 7am daily schedule. Material 03 shows where this
                         one goes (it needs a special folder). Do not edit it.

Keys you add in GitHub (Settings > Secrets and variables > Actions):
  APIFY_TOKEN ......... REQUIRED. Runs the readers.
  ANTHROPIC_API_KEY ... optional. Turns on AI-written drafts.
  PROSPEO_API_KEY ..... optional. Turns on the decision-maker finder.
  SYNC_URL / SYNC_TOKEN optional. For cross-device sync (see cloudflare-setup.txt).

That's it. Open Material 03 and go step by step.
