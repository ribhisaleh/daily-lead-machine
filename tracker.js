/* your business "My Leads" tracker.
   Local-first: every read is instant from localStorage. If a sync backend is configured
   (SYNC_URL, a Cloudflare Worker), it also mirrors to the cloud so the list shows on every
   browser/device. Offline changes queue in an outbox and flush on the next sync.
   Exposes window.Track. */
(function () {
  var KEY = "your-business_myleads_v1";
  var QKEY = "your-business_myleads_outbox_v1";
  var SYNC_URL = "__SYNC_URL__";      // filled at build from repo var SYNC_URL ("" = local only)
  var SYNC_TOKEN = "__SYNC_TOKEN__";  // optional shared token
  var STATUSES = ["To reach", "Applied", "Replied", "Call booked", "Won", "Skip"];
  var COLORS = { "To reach": "#8A939B", "Applied": "#4E7CB0", "Replied": "#C87A0A",
                 "Call booked": "#0C8B86", "Won": "#2E9E5B", "Skip": "#B0454A" };
  var RETAIN_DAYS = 30;

  function load() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } }
  function save(o) { try { localStorage.setItem(KEY, JSON.stringify(o)); } catch (e) {} }
  function loadQ() { try { return JSON.parse(localStorage.getItem(QKEY)) || []; } catch (e) { return []; } }
  function saveQ(q) { try { localStorage.setItem(QKEY, JSON.stringify(q)); } catch (e) {} }
  function today() { try { return new Date().toISOString().slice(0, 10); } catch (e) { return ""; } }

  function syncOn() { return !!SYNC_URL && SYNC_URL.indexOf("__") !== 0; }
  function urlWithToken() { return SYNC_URL + (SYNC_TOKEN && SYNC_TOKEN.indexOf("__") !== 0 ? ("?t=" + encodeURIComponent(SYNC_TOKEN)) : ""); }

  function enqueue(op) { if (!syncOn()) return; var q = loadQ(); q.push(op); saveQ(q); flush(); }
  function flush() {
    if (!syncOn()) return Promise.resolve();
    var q = loadQ();
    if (!q.length) return Promise.resolve();
    return fetch(urlWithToken(), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ops: q }) })
      .then(function (r) { if (r.ok) saveQ([]); })       // clear outbox only on success
      .catch(function () {});                             // keep queued for next flush
  }

  function daysBetween(iso) {
    try { if (!iso) return 0; var d = Date.parse(iso.length <= 10 ? iso + "T00:00:00Z" : iso); if (isNaN(d)) return 0; return Math.floor((Date.now() - d) / 86400000); } catch (e) { return 0; }
  }
  // Auto-clear leads 30 days after last activity, EXCEPT Won (kept). Runs on read; also queues
  // a backend remove so the clear propagates to your other devices.
  function prune() {
    var o = load(), changed = false;
    for (var k in o) {
      var r = o[k];
      if (r && r.status === "Won") continue;
      var last = (r && (r.updatedAt || r.appliedAt || r.addedAt)) || "";
      if (last && daysBetween(last) > RETAIN_DAYS) { delete o[k]; enqueue({ op: "remove", link: k }); changed = true; }
    }
    if (changed) save(o);
    return o;
  }

  window.Track = {
    STATUSES: STATUSES,
    RETAIN_DAYS: RETAIN_DAYS,
    syncEnabled: syncOn,
    all: function () { return prune(); },
    get: function (link) { return load()[link] || null; },
    daysSince: function (iso) { return daysBetween(iso); },
    expiresIn: function (rec) { if (!rec || rec.status === "Won") return null; var last = rec.updatedAt || rec.appliedAt || rec.addedAt; if (!last) return null; return RETAIN_DAYS - daysBetween(last); },
    set: function (lead, status) {
      var o = load(), link = lead && lead.link;
      if (!link) return null;
      var ex = o[link] || {}, rec = Object.assign({}, ex, lead);
      rec.status = status;
      rec.addedAt = ex.addedAt || today();
      rec.updatedAt = today();
      if (status === "Applied" && !rec.appliedAt) rec.appliedAt = today();
      rec.myNotes = ex.myNotes || rec.myNotes || "";
      o[link] = rec; save(o); enqueue({ op: "set", lead: rec }); return rec;
    },
    setNotes: function (link, notes) {
      var o = load();
      if (o[link]) { o[link].myNotes = notes; o[link].updatedAt = today(); save(o); enqueue({ op: "set", lead: o[link] }); }
    },
    remove: function (link) { var o = load(); delete o[link]; save(o); enqueue({ op: "remove", link: link }); },
    color: function (s) { return COLORS[s] || "#8A939B"; },
    // Two-way sync: push queued local changes, pull the cloud copy, then MERGE (never blind
    // overwrite, so a first sync against an empty cloud can't wipe existing local leads). Per
    // link, the record with the newer updatedAt wins; local-only records are pushed up. Calls
    // cb() when done (even if sync is off) so the page can re-render.
    sync: function (cb) {
      if (!syncOn()) { if (cb) cb(); return; }
      flush().then(function () {
        return fetch(urlWithToken(), { method: "GET" }).then(function (r) { return r.json(); });
      }).then(function (remote) {
        if (!remote || typeof remote !== "object" || remote.error) return;
        var local = load(), merged = {}, pushUps = [], links = {}, k;
        for (k in local) links[k] = 1;
        for (k in remote) links[k] = 1;
        for (var link in links) {
          var lo = local[link], re = remote[link];
          if (lo && re) {
            var loWins = String(lo.updatedAt || "") >= String(re.updatedAt || "");
            merged[link] = loWins ? lo : re;
            if (loWins && String(lo.updatedAt || "") > String(re.updatedAt || "")) pushUps.push(lo);
          } else if (lo) { merged[link] = lo; pushUps.push(lo); }   // local-only -> keep + push up
          else { merged[link] = re; }                              // remote-only -> adopt
        }
        save(merged);
        if (pushUps.length) {
          var q = loadQ();
          pushUps.forEach(function (r) { q.push({ op: "set", lead: r }); });
          saveQ(q); flush();
        }
      }).catch(function () {}).then(function () { if (cb) cb(); });
    }
  };
})();
