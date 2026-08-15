/* your business "My Leads" sync — Cloudflare Worker.
 *
 * Stores your tracked leads so every browser/device sees the same list.
 * Data lives in a Workers KV namespace under a single key ("leads") as a JSON map of {link: record}.
 *
 * ── ONE-TIME SETUP (about 10 minutes, all in the Cloudflare dashboard) ──────────────────────
 * 1. Sign up (free) at https://dash.cloudflare.com  →  Workers & Pages.
 * 2. Create a KV namespace:  Storage & Databases → KV → Create → name it "MYLEADS".
 * 3. Create a Worker:  Workers & Pages → Create → Worker → name it (e.g. "myleads") → Deploy.
 * 4. Edit the Worker → replace all its code with THIS file → Deploy.
 * 5. Bind the KV namespace to the Worker:  the Worker → Settings → Bindings → Add → KV namespace →
 *    Variable name MUST be exactly  MYLEADS , select the "MYLEADS" namespace → Save & Deploy.
 * 6. (Optional) Set a shared token: Settings → Variables and Secrets → Add → name TOKEN → a random
 *    string. Then use that same value as the SYNC_TOKEN repo variable in step 8.
 * 7. Copy the Worker URL (looks like  https://myleads.<your-subdomain>.workers.dev ).
 * 8. In your clientacquisition GitHub repo:  Settings → Secrets and variables → Actions → Variables
 *    → New repository variable →  SYNC_URL  = that Worker URL.  (And SYNC_TOKEN if you set one.)
 * 9. Run the workflow. My Leads now syncs across your devices.
 *
 * Security note: the Worker URL sits in your public dashboard's page source. It's obscure, and the
 * optional TOKEN stops casual drive-by writes, but it isn't a hard secret. Fine for a personal
 * lead tracker. Don't store anything sensitive beyond the leads themselves.
 */
export default {
  async fetch(request, env) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "content-type, x-token",
    };
    const json = (obj, status = 200) =>
      new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json", ...cors } });

    if (request.method === "OPTIONS") return new Response(null, { headers: cors });

    // Optional shared-token gate (only enforced if you set a TOKEN variable on the Worker).
    if (env.TOKEN) {
      const url = new URL(request.url);
      const supplied = url.searchParams.get("t") || request.headers.get("x-token");
      if (supplied !== env.TOKEN) return json({ error: "unauthorized" }, 401);
    }

    if (!env.MYLEADS) return json({ error: "KV binding MYLEADS missing — add it in Settings → Bindings" }, 500);

    if (request.method === "GET") {
      const blob = await env.MYLEADS.get("leads");
      return json(blob ? JSON.parse(blob) : {});
    }

    if (request.method === "POST") {
      let body;
      try { body = await request.json(); } catch (e) { return json({ error: "bad json" }, 400); }
      const ops = Array.isArray(body.ops) ? body.ops : (body.op ? [body] : []);
      const all = JSON.parse((await env.MYLEADS.get("leads")) || "{}");
      for (const o of ops) {
        if (o.op === "set" && o.lead && o.lead.link) all[o.lead.link] = o.lead;
        else if (o.op === "remove" && o.link) delete all[o.link];
      }
      await env.MYLEADS.put("leads", JSON.stringify(all));
      return json({ ok: true, count: Object.keys(all).length });
    }

    return json({ error: "method not allowed" }, 405);
  },
};
