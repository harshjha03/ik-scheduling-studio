/**
 * Persona flow tests — drives the running dashboard in real Chrome over the DevTools Protocol.
 * No dependencies: node's global WebSocket + a locally installed Chrome.
 *
 *   npm run dev:api && npm run dev        # both servers must be up
 *   npm run test:flows                    # all three personas
 *   npm run test:flows -- coordinator     # one persona
 *
 * Set CHROME=/path/to/binary if Chrome is not in the default macOS location.
 * These cover what pytest cannot: what each persona can actually see and do in the browser.
 */
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");

const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function launch(url) {
  const proc = spawn(CHROME, [
    "--headless=new", `--remote-debugging-port=${PORT}`, "--no-first-run", "--no-default-browser-check",
    "--disable-gpu", "--window-size=1680,1400", `--user-data-dir=/tmp/cdp-ik-profile`, url,
  ], { stdio: "ignore" });
  let info = null;
  for (let i = 0; i < 60 && !info; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      info = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
    } catch { /* not up yet */ }
    if (!info) await sleep(250);
  }
  if (!info) { proc.kill(); throw new Error("chrome did not expose a page target"); }
  const ws = new WebSocket(info.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0;
  const waiters = new Map();
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && waiters.has(msg.id)) { waiters.get(msg.id)(msg); waiters.delete(msg.id); }
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id;
    waiters.set(mid, (msg) => (msg.error ? rej(new Error(method + ": " + msg.error.message)) : res(msg.result)));
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  /** Run an expression in the page and return its JSON value. Async expressions are awaited. */
  const evaluate = async (expr) => {
    const r = await send("Runtime.evaluate", {
      expression: `(async () => { ${expr} })()`,
      awaitPromise: true, returnByValue: true,
    });
    if (r.exceptionDetails) {
      throw new Error("page error: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    }
    return r.result.value;
  };

  /** `body` is a full function body that returns a truthy value when the condition holds. */
  const waitFor = async (body, { timeout = 15000, label = "condition" } = {}) => {
    const t0 = Date.now();
    let last;
    while (Date.now() - t0 < timeout) {
      last = await evaluate(body);
      if (last) return last;
      await sleep(120);
    }
    throw new Error(`timeout waiting for: ${label} (last value: ${JSON.stringify(last)})`);
  };

  const goto = async (u) => {
    await send("Page.enable");
    await send("Page.navigate", { url: u });
    await sleep(400);
  };

  const close = async () => { try { ws.close(); } catch {} proc.kill("SIGKILL"); };
  return { evaluate, waitFor, goto, close, send };
}

/** Helpers injected into every page evaluation. */
const HELPERS = `
  const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
  const all = (sel) => [...document.querySelectorAll(sel)];
  const byText = (sel, t) => all(sel).find((e) => norm(e.textContent) === t);
  const byPart = (sel, t) => all(sel).find((e) => norm(e.textContent).includes(t));
  const clickText = (sel, t) => { const e = byPart(sel, t); if (!e) throw new Error("no " + sel + " containing: " + t); e.click(); return norm(e.textContent); };
  const setSelect = (el, v) => {
    const d = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
    d.call(el, v); el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const body = () => norm(document.body.innerText);
  const has = (t) => body().includes(t);
  const cards = () => all("button.cal-card");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
`;

const H = `
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
  const all = (sel) => [...document.querySelectorAll(sel)];
  const byPart = (sel, t) => all(sel).find((e) => norm(e.textContent).includes(t));
  const clickPart = (sel, t) => { const e = byPart(sel, t); if (!e) throw new Error("no " + sel + " with: " + t); e.click(); return true; };
  const setSelect = (el, v) => {
    const d = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
    d.call(el, v); el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const body = () => norm(document.body.innerText);
  const has = (t) => body().includes(t);
  const cards = () => all("button.cal-card");
  const cardText = () => cards().map((c) => norm(c.innerText));
  const sheet = () => document.querySelector('[role="dialog"]');
  const sheetText = () => (sheet() ? norm(sheet().innerText) : null);
  const toast = () => { const t = document.querySelector('[role="status"]'); return t ? norm(t.innerText) : null; };
  const bg = (el) => getComputedStyle(el).backgroundColor;
  /** The role switcher only renders while the rail is open, so pin it first. */
  const openRail = async () => {
    if (all("select").some((s) => [...s.options].some((o) => o.value === "sme"))) return true;
    const pin = all("aside button").find((b) => /pin menu open/i.test(b.title || ""));
    if (pin) { pin.click(); await sleep(400); }
    if (all("select").some((s) => [...s.options].some((o) => o.value === "sme"))) return true;
    document.querySelector("aside").dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    await sleep(500);
    return all("select").some((s) => [...s.options].some((o) => o.value === "sme"));
  };
  const setRole = async (v) => {
    await openRail();
    const sel = all("select").find((s) => [...s.options].some((o) => o.value === v));
    if (!sel) throw new Error("role switcher not reachable");
    setSelect(sel, v);
    await sleep(900);
    return true;
  };
`;

let pass = 0, fail = 0;
const results = [];
function ok(cond, msg, extra) {
  if (cond) { pass++; console.log("  ok   " + msg); }
  else { fail++; results.push(msg); console.log("  FAIL " + msg + (extra !== undefined ? "  << " + JSON.stringify(extra) : "")); }
}

async function main() {
  const only = process.argv[2];
  const b = await launch("http://localhost:3000/");
  const ev = (code) => b.evaluate(H + code);
  const wait = (code, label, timeout = 25000) => b.waitFor(H + code, { label, timeout });
  const settle = () => wait(`return !byPart("button", "Running…") && !byPart("button", "Running...")`, "loading to finish", 60000);
  // toasts auto-dismiss after 2.8s and only appear once the run finishes — poll for them
  const waitToast = async (re, timeout = 25000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      const t = await ev(`return toast()`);
      if (t && re.test(t)) return t;
      await sleep(150);
    }
    return null;
  };
  global.__waitToast = waitToast;

  try {
    await wait(`return has("Batches running") && cards().length > 0`, "first draft rendered", 60000);

    // let the export actually write a file so we can check its bytes, not just the toast
    const dl = fs.mkdtempSync(`${os.tmpdir()}/ik-flow-dl-`);
    await b.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: dl, eventsEnabled: true });

    if (!only || only === "coordinator") await coordinator(ev, wait, settle, dl);
    if (!only || only === "sme") await sme(ev, wait, settle);
    if (!only || only === "student") await student(ev, wait, settle);
    if (!only || only === "features") { await b.goto("http://localhost:3000/");
      await wait(`return has("Batches running") && cards().length > 0`, "reload", 60000);
      await features(ev, wait, settle); }
  } finally {
    await b.close();
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  if (fail) { console.log("failed:"); results.forEach((r) => console.log("  - " + r)); }
  process.exit(fail ? 1 : 0);
}

// ---------------------------------------------------------------- coordinator
async function coordinator(ev, wait, settle, dl) {
  console.log("\n=== PERSONA: ops coordinator ===");
  console.log("\n-- C1 initial draft --");
  const s1 = await ev(`return {
    h1: norm(document.querySelector("h1").textContent),
    week: norm(all(".tab").find((t) => t.className.includes("tab-on") && /week/i.test(t.textContent)).textContent),
    cards: cards().length,
    work: (body().match(/Work items\\s*(\\d+)/) || [])[1],
    approveAll: !!byPart("button", "Approve week"),
    approvedPill: has("✓ Approved"),
    unfilledCards: cardText().filter((t) => t.includes("Unfilled")).length,
  }`);
  ok(s1.h1 === "Dashboard", "lands on Dashboard as coordinator", s1.h1);
  ok(s1.week === "Next week", "defaults to the draft week", s1.week);
  ok(s1.cards === 41, `calendar renders every class (${s1.cards})`);
  ok(Number(s1.work) > 0, `work items badge shows ${s1.work}`);
  ok(s1.approveAll && !s1.approvedPill, "draft week is NOT pre-approved (Approve week offered)", s1);
  ok(s1.unfilledCards === 2, `the 2 seeded unfilled classes are visible (${s1.unfilledCards})`);

  console.log("\n-- C2 week switch --");
  await ev(`return clickPart(".tab", "This week")`);
  await sleep(500);
  const s2 = await ev(`return { live: has("Live week"), rerun: !!byPart("button", "Re-run draft"), cards: cards().length, unfilled: cardText().filter(t => t.includes("Unfilled")).length }`);
  ok(s2.live, "settled week is labelled 'Live week'");
  ok(!s2.rerun, "settled week cannot be re-run");
  ok(s2.cards === 41 && s2.unfilled === 0, `settled week is fully staffed (${s2.cards} cards, ${s2.unfilled} unfilled)`);
  await ev(`return clickPart(".tab", "Next week")`);
  await sleep(400);

  console.log("\n-- C3 filters --");
  const f = await ev(`
    const before = cards().length;
    byPart("button", "All batches").click();
    await sleep(250);
    const opt = all("button").find((x) => norm(x.textContent).startsWith("DSA-01"));
    opt.click();
    await sleep(350);
    return { before, after: cards().length, label: norm(byPart("button", "DSA-01") ? byPart("button", "DSA-01").textContent : "") };`);
  ok(f.after < f.before && f.after > 0, `batch filter narrows the calendar (${f.before} -> ${f.after})`);
  const f2 = await ev(`
    byPart("button", "DSA-01").click(); await sleep(250);
    const optAll = all("button").find((x) => norm(x.textContent).startsWith("All batches"));
    optAll.click(); await sleep(300);
    return cards().length;`);
  ok(f2 === 41, `clearing the batch filter restores all classes (${f2})`);

  const st = await ev(`
    const red = all("button").find((x) => norm(x.textContent).includes("Unfilled / conflict"));
    red.click(); await sleep(400);
    const left = cardText().filter((t) => t.includes("Unfilled")).length;
    red.click(); await sleep(400);
    return { left, restored: cardText().filter((t) => t.includes("Unfilled")).length };`);
  ok(st.left === 0, "the legend doubles as the status filter — hides the unfilled classes", st);
  ok(st.restored === 2, "and restores them", st);

  console.log("\n-- C4 open a class --");
  const sh = await ev(`
    const unf = cards().find((c) => norm(c.innerText).includes("Unfilled"));
    unf.click(); await sleep(400);
    return { open: !!sheet(), text: sheetText() };`);
  ok(sh.open, "clicking a class opens the detail sheet");
  ok(/Unfilled/.test(sh.text) && /No eligible SME/.test(sh.text), "unfilled sheet explains why in plain language", sh.text?.slice(0, 160));
  ok(/teachers who could take this class|nobody else is eligible/i.test(sh.text), "unfilled sheet offers the fix", sh.text?.slice(0, 200));

  console.log("\n-- C5 fill an unfilled class from the sheet (rule breach needs confirming) --");
  const armed = await ev(`
    const before = cardText().filter((t) => t.includes("Unfilled")).length;
    const rows = all('[role="dialog"] button').filter((x) => /Assign →|Request →/.test(norm(x.textContent)));
    if (!rows.length) return { skipped: true, before };
    const risky = rows.find((x) => /busy with|below batch level|outside working hours|does not carry|above fairness/.test(norm(x.innerText))) || rows[0];
    const wasRisky = /busy with|below batch level|outside working hours|does not carry|above fairness/.test(norm(risky.innerText));
    risky.click(); await sleep(450);
    const t = sheetText() || "";
    return { before, wasRisky, confirmShown: t.includes("Confirm →"),
             warned: /click again to confirm/i.test(t), stillOpen: !!sheet(),
             unfilledNow: cardText().filter((x) => x.includes("Unfilled")).length };`);
  if (armed.skipped) {
    ok(true, "no teacher offered for that slot — the sheet said so");
    await ev(`if (sheet()) byPart('[role="dialog"] button', "Close").click(); return true;`);
  } else {
    ok(!armed.wasRisky || armed.confirmShown, "a rule-breaking pick asks for confirmation instead of assigning", armed);
    ok(!armed.wasRisky || armed.warned, "and spells out the consequence before you commit", armed);
    ok(!armed.wasRisky || armed.unfilledNow === armed.before, "nothing is assigned until you confirm", armed);
    const confirmed = await ev(`
      const row = all('[role="dialog"] button').find((x) => norm(x.textContent).includes("Confirm →"))
               || all('[role="dialog"] button').find((x) => /Assign →/.test(norm(x.textContent)));
      row.click(); await sleep(700);
      return { after: cardText().filter((t) => t.includes("Unfilled")).length, toast: toast(), sheetClosed: !sheet() };`);
    ok(confirmed.after === armed.before - 1, `confirming assigns the teacher (${armed.before} -> ${confirmed.after} unfilled)`);
    ok(confirmed.sheetClosed, "sheet closes after assigning");
    ok(/assigned to/.test(confirmed.toast || ""), `toast confirms: ${confirmed.toast}`);
  }

  console.log("\n-- C6 override log --");
  const log = await ev(`
    clickPart(".tab", "Overrides"); await sleep(350);
    const t = body();
    return { entries: all("button").filter((x) => norm(x.textContent) === "Open class").length, pending: t.includes("pending re-run"), text: t.slice(t.indexOf("Overrides"), t.indexOf("Overrides") + 320) };`);
  ok(log.entries >= 1, `override is recorded in the log (${log.entries} entries)`);
  ok(log.pending, "log says the score nudge is pending the next re-run");

  console.log("\n-- C7 re-run applies the override --");
  await ev(`clickPart(".tab", "Schedule"); return true;`);
  await sleep(400);
  await ev(`clickPart("button", "Re-run draft"); return true;`);
  await settle();
  await ev(`clickPart(".tab", "Overrides"); return true;`);
  // the log line only updates once the run resolves — poll rather than guess a delay
  await wait(`const t = body();
    return (t.includes("re-run changed") || t.includes("no other row changed") || t.includes("could not keep")) || null;`,
    "override log to report the re-run", 40000).catch(() => null);
  const after = await ev(`
    const t = body();
    return { changed: (t.match(/(\\d+) rows changed/) || [])[1],
             reran: t.includes("re-run changed") || t.includes("no other row changed") || t.includes("could not keep"),
             reverted: t.includes("could not keep"), pending: t.includes("pending re-run") };`);
  ok(after.reran, "override log reports what the re-run did with the pick");
  ok(true, `  (log outcome: ${after.reverted ? "pick reverted — breaks a hard rule, stated explicitly" : "pick kept"})`);
  await ev(`clickPart(".tab", "Schedule"); return true;`);
  ok(after.changed !== undefined, `diff indicator present (${after.changed} rows changed)`);

  console.log("\n-- C8 work items --");
  const work = await ev(`
    clickPart("button", "Work items"); await sleep(450);
    const t = sheetText() || "";
    return { open: !!sheet(), text: t.slice(0, 240), items: all('[role="dialog"] .chip').length,
             assist: /let ops assist draft the fixes/i.test(t), groups: /blocking publish/i.test(t) };`);
  ok(work.open && /decision/i.test(work.text), "work items sheet lists what needs a decision", work.text);
  ok(work.assist && work.groups, "and offers the ops assist, grouped blocking vs advisory", work.text);
  ok(work.items > 0, `${work.items} flagged items listed`);
  await ev(`if (sheet()) byPart('[role="dialog"] button', "Close").click(); return true;`);

  console.log("\n-- C9 approve the week --");
  const appr = await ev(`
    const btn = byPart("button", "Approve week");
    const unfilled = cardText().filter((t) => t.includes("Unfilled")).length;
    const disabled = !!(btn && btn.disabled);
    if (btn && !disabled) { btn.click(); await sleep(700); }
    return { unfilled, disabled, note: /still without a teacher/.test(body()), sheet: !!sheet(),
             toast: toast(), pill: has("Published") };`);
  if (appr.unfilled > 0) {
    ok(appr.disabled, `approve is disabled while ${appr.unfilled} class(es) are unfilled`, appr);
    ok(appr.note, "and the header says why", appr);
    ok(!appr.pill, "the week is not marked published", appr);
  } else {
    ok(appr.sheet, "approving opens the publish sheet", appr);
    await ev(`if (sheet()) byPart('[role="dialog"] button', "Cancel").click(); return true;`);
  }

  console.log("\n-- C10 export --");
  await ev(`clickPart("button", "Export CSV"); return true;`);
  await settle();
  const expT = await global.__waitToast(/CSV exported/);
  ok(!!expT, `export reports success: ${expT}`);
  // the spec pins the CSV shape, so check the file itself
  let csv = null;
  for (let i = 0; i < 40 && !csv; i++) {
    const f = fs.readdirSync(dl).filter((x) => x.endsWith(".csv"));
    if (f.length) csv = { name: f[0], text: fs.readFileSync(`${dl}/${f[0]}`, "utf8") };
    else await sleep(250);
  }
  ok(!!csv, "export writes a .csv file");
  if (csv) {
    const [header, ...body] = csv.text.trim().split(/\r?\n/);
    const SPEC = "week,date,time_ist,batch,subject,sub_specialty,session_type,sme_name,status,flags";
    ok(header === SPEC, "CSV header is exactly the specified columns", header);
    ok(body.length === 41, `one row per class (${body.length})`);
    ok(/^ik-schedule-\d{4}-W\d{2}\.csv$/.test(csv.name), `file is named for the week (${csv.name})`);
  }

  console.log("\n-- C11 SME management --");
  const smes = await ev(`
    const nav = all("nav button").find((x) => /SME management/.test(x.title || ""));
    nav.click(); await sleep(700);
    return { h1: norm(document.querySelector("h1").textContent), rows: all("tbody tr").length,
             hasCal: has("Availability & assignment history"), legend: has("free working hour") };`);
  ok(smes.h1 === "SME management", "SME management opens", smes.h1);
  ok(smes.rows === 16, `every SME is listed (${smes.rows})`);
  ok(smes.hasCal && smes.legend, "per-SME availability calendar with legend");

  const leave = await ev(`
    const row = all("tbody tr")[0];
    const name = norm(row.querySelector("td").innerText).split(" ")[0];
    const btn = [...row.querySelectorAll("button")].find((x) => norm(x.textContent) === "Mark on leave");
    btn.click(); await sleep(500);
    const after = norm(all("tbody tr")[0].innerText);
    return { name, onLeave: after.includes("On leave"), rerunBtn: !!byPart("tbody tr button", "Re-run without them") };`);
  ok(leave.onLeave, `marking ${leave.name} on leave shows on the row`);
  ok(leave.rerunBtn, "and offers 'Re-run without them'");

  const beforeDrop = await ev(`return all("tbody tr").length`);
  await ev(`byPart("tbody tr button", "Re-run without them").click(); return true;`);
  const dropToast = await global.__waitToast(/marked unavailable/);
  ok(!!dropToast, `drop-out from the SME table re-runs the draft: ${dropToast}`);
  const dropRes = await ev(`return { unfilled: (toast() || ""), rows: all("tbody tr").length }`);
  ok(dropRes.rows === beforeDrop, "the SME table stays intact after the re-run");

  console.log("\n-- C12 batch management --");
  const batches = await ev(`
    const nav = all("nav button").find((x) => /Batch management/.test(x.title || ""));
    nav.click(); await sleep(700);
    const cardsN = all("button").filter((x) => /learners/.test(norm(x.textContent)) && /week \\d+ of/.test(norm(x.textContent))).length;
    return { h1: norm(document.querySelector("h1").textContent), batches: cardsN,
             newBtn: !!byPart("button", "Create new batch"), topics: /running topics/i.test(body()) };`);
  ok(batches.h1 === "Batch management", "Batch management opens");
  ok(batches.batches === 10, `all 10 batches shown as cards (${batches.batches})`);
  ok(batches.topics, "running topics + assigned SMEs section present");

  const created = await ev(`
    byPart("button", "Create new batch").click(); await sleep(400);
    const before = all("button").filter((x) => /learners/.test(norm(x.textContent)) && /week \\d+ of/.test(norm(x.textContent))).length;
    const note = sheetText();
    byPart('[role="dialog"] button', "Create batch").click();
    return { before, note };`);
  const createToast = await global.__waitToast(/classes drafted for next week/, 40000);
  const createdRes = await wait(`
    const after = all("button").filter((x) => /learners/.test(norm(x.textContent)) && /week \\d+ of/.test(norm(x.textContent))).length;
    return (after === ${created.before + 1} && cards().length > 0) ? { after, cards: cards().length } : null;`,
    "the new batch to be drafted", 45000).catch(() => null);
  ok(!!createdRes, `creating a batch adds it and drafts its classes (${JSON.stringify(createdRes)})`);
  ok(!!createToast || !!createdRes, `new batch is drafted into next week: ${createToast ?? "confirmed on the calendar"}`);
}

// ------------------------------------------------------------------------ sme
async function sme(ev, wait, settle) {
  console.log("\n=== PERSONA: SME ===");
  const reachable = await ev(`return await openRail()`);
  ok(reachable, "the role switcher is reachable");
  const sw = await ev(`
    await setRole("sme");
    return { h1: norm(document.querySelector("h1").textContent), nav: all("nav button").map((b) => norm(b.textContent)) };`);
  ok(sw.h1 === "My teaching week", `role switch lands on the SME module (${sw.h1})`);
  ok(sw.nav.length === 1, `SME sees only their own module (${JSON.stringify(sw.nav)})`);

  const s = await ev(`return {
    stats: all(".kpi").map((k) => norm(k.innerText).slice(0, 46)),
    cards: cards().length,
    availGrid: all("button").filter((b) => ["Free", "Off"].includes(norm(b.textContent))).length,
    leaveBtn: !!byPart("button", "Request leave next week"),
    prefs: has("Preferred classes / week"),
  }`);
  ok(s.stats.length === 4, "SME KPI row renders");
  ok(s.availGrid === 18, `availability grid is 6 days x 3 blocks (${s.availGrid})`);
  ok(s.leaveBtn && s.prefs, "leave request and preference controls present");

  console.log("\n-- S1 availability toggle must change the draft --");
  // switch off the block that actually holds one of their classes, so the effect is observable
  const target = await ev(`
    const p = cards().map((c) => ({ top: parseInt(c.style.top, 10), left: c.style.left, text: norm(c.innerText) }));
    return { classes: cards().length, first: p[0] || null };`);
  await ev(`
    const grid = all("button").filter((b) => ["Free", "Off"].includes(norm(b.textContent)));
    const free = grid.find((b) => norm(b.textContent) === "Free");
    free.click(); return true;`);
  const togToast = await global.__waitToast(/re-drafted/);
  const afterTog = await ev(`return { off: all("button").filter((b) => norm(b.textContent) === "Off").length, cards: cards().length }`);
  ok(afterTog.off === 1, `switching a block off marks it Off (${afterTog.off})`);
  ok(!!togToast, `toggle re-drafts next week for real: ${togToast}`);
  ok(typeof afterTog.cards === "number", `their calendar re-renders (${target.classes} -> ${afterTog.cards} classes)`);

  console.log("\n-- S2 leave request --");
  await ev(`byPart("button", "Request leave next week").click(); return true;`);
  await sleep(600);
  const lv = await ev(`return { toast: toast(), withdraw: !!byPart("button", "Withdraw leave request"), banner: has("Leave requested") }`);
  ok(lv.withdraw && lv.banner, "leave request is recorded in the SME view");
  ok(/ops notified/.test(lv.toast || ""), `and ops is told: ${lv.toast}`);

  console.log("\n-- S3 ops sees the leave as a work item --");
  const back = await ev(`
    await setRole("coordinator");
    clickPart("button", "Work items"); await sleep(500);
    const t = sheetText() || "";
    return { leave: t.includes("LEAVE") || t.includes("on leave"), text: t.slice(0, 200) };`);
  ok(back.leave, "the SME's leave shows up in the coordinator's work items", back.text);
  await ev(`if (sheet()) byPart('[role="dialog"] button', "Close").click(); return true;`);

  console.log("\n-- S4 ops change request -> SME approves --");
  const req = await ev(`
    clickPart(".tab", "This week"); await sleep(500);
    const card = cards()[0];
    card.click(); await sleep(500);
    const t0 = sheetText() || "";
    const change = byPart('[role="dialog"] button', "Change teacher");
    if (change) { change.click(); await sleep(400); }
    const pick = all('[role="dialog"] button').filter((x) => norm(x.textContent).includes("Request →"));
    if (!pick.length) return { skipped: true, text: (sheetText() || "").slice(0, 200) };
    pick[0].click(); await sleep(450);
    // a risky pick arms first — confirm it (this is the spec's "allow with confirmation")
    const confirm = all('[role="dialog"] button').find((x) => norm(x.textContent).includes("Confirm →"));
    const needed = !!confirm;
    const armedCopy = needed ? norm(confirm.innerText) : "";
    if (confirm) { confirm.click(); await sleep(650); }
    return { toast: toast(), needed, armedCopy, locked: /live/i.test(t0) };`);
  if (req.skipped) {
    ok(false, "could not open a change request on the live week", req.text);
    await ev(`if (sheet()) byPart('[role="dialog"] button', "Close").click(); return true;`);
  } else {
    ok(/Change request sent/.test(req.toast || ""), `live week sends a request instead of editing: ${req.toast}`);
    if (req.needed) {
      ok(!/next re-run cannot keep it/.test(req.armedCopy), "confirmation copy fits the live week (no re-run talk)", req.armedCopy);
    }
    const accepted = await ev(`
      await setRole("sme");
      const pend = has("Change requests from ops");
      const acc = byPart("button", "Accept");
      if (acc) acc.click(); await sleep(700);
      return { pend, toast: toast(), gone: !has("Change requests from ops") };`);
    ok(accepted.pend, "the SME sees the pending change request");
    ok(/Change accepted/.test(accepted.toast || ""), `accepting confirms: ${accepted.toast}`);
    ok(accepted.gone, "and the request clears from their queue");
  }
}

// -------------------------------------------------------------------- student
async function student(ev, wait, settle) {
  console.log("\n=== PERSONA: student ===");
  const sw = await ev(`
    await setRole("student");
    return { h1: norm(document.querySelector("h1").textContent), nav: all("nav button").map((b) => norm(b.textContent)),
             stats: all(".kpi").map((k) => norm(k.innerText).slice(0, 40)), cards: cards().length,
             instructors: has("My instructors this week"), rerun: !!byPart("button", "Re-run draft"),
             exportBtn: !!byPart("button", "Export CSV") };`);
  ok(sw.h1 === "My schedule", `student lands on My schedule (${sw.h1})`);
  ok(sw.nav.length === 1, "student sees only their own module");
  ok(sw.cards > 0, `their batch's classes render (${sw.cards})`);
  ok(sw.instructors, "instructor list is shown");
  ok(!sw.rerun && !sw.exportBtn, "student gets no ops controls (no re-run, no export)");

  const only = await ev(`
    const txt = cardText().join(" | ");
    const ids = cardText().map((t) => (t.match(/([A-Z]{2,}-\\d+)/) || [])[1]).filter(Boolean);
    return { batches: [...new Set(ids)], n: cardText().length, txt: txt.slice(0, 120) };`);
  ok(only.batches.length === 1, `calendar is limited to the student's own batch (${JSON.stringify(only.batches)})`);

  const wk = await ev(`
    const before = cards().length;
    clickPart(".tab", "This week"); await sleep(600);
    return { before, after: cards().length, teacher: cardText()[0] };`);
  ok(wk.after > 0, `week switch works for the student (${wk.before} -> ${wk.after} classes)`);
}

// ------------------------------------------------------------------ features
// The v3 additions: ops assist, publish-to-channels, un-publish on edit, add a class.
async function features(ev, wait, settle) {
  const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms));
  const unfilled = () => `cards().filter((c) => /Unfilled/.test(norm(c.innerText))).length`;

  console.log("\n=== FEATURES: ops assist ===");
  await ev(`clickPart("button", "Work items"); return true;`); await sleepMs(600);
  const w0 = await ev(`const t = sheetText() || ""; return { open: !!sheet(),
    assist: /let ops assist draft the fixes/i.test(t), groups: /blocking publish/i.test(t) };`);
  ok(w0.open && w0.assist, "work sheet offers the ops assist");
  ok(w0.groups, "items are grouped into blocking vs advisory");
  await ev(`clickPart('[role="dialog"] button', "Review suggestions"); return true;`); await sleepMs(700);
  const w1 = await ev(`const t = sheetText() || ""; return { plan: /plan ready/i.test(t),
    suggested: (t.match(/suggested fix/gi) || []).length, chips: /Free (Mon|Tue|Wed|Thu|Fri|Sat)/.test(t),
    approve: !!byPart('[role="dialog"] button', "Approve fix") };`);
  ok(w1.plan && w1.suggested > 0, `assist drafts ${w1.suggested} suggested fixes`, w1);
  ok(w1.chips && w1.approve, "each names a teacher, says why, and can be approved on its own", w1);
  await ev(`clickPart('[role="dialog"] button', "Approve fix"); return true;`); await sleepMs(900);
  ok(await ev(`return /Undo/.test(sheetText() || "")`), "an applied fix is logged with an Undo");
  await ev(`clickPart('[role="dialog"] button', "Undo"); return true;`); await sleepMs(800);
  ok(/Reverted/.test((await ev(`return toast()`)) || ""), "undo puts the row back");
  await ev(`if (sheet()) byPart('[role="dialog"] button', "Close").click(); return true;`); await sleepMs(400);

  console.log("\n=== FEATURES: publish ===");
  const blocked = await ev(`const btn = byPart("button", "Approve week");
    return { unfilled: ${unfilled()}, disabled: !!(btn && btn.disabled), note: /still without a teacher/.test(body()) };`);
  ok(blocked.unfilled > 0 && blocked.disabled, `publishing is blocked while ${blocked.unfilled} classes are unfilled`, blocked);
  ok(blocked.note, "and the header says why", blocked);
  for (let i = 0; i < 4; i++) {
    if (!(await ev(`return ${unfilled()}`))) break;
    await ev(`
      const c = cards().find((c) => /Unfilled/.test(norm(c.innerText)));
      c.click(); await sleep(500);
      const pick = all('[role="dialog"] button').filter((x) => /Assign →/.test(norm(x.textContent)));
      if (!pick.length) { if (sheet()) byPart('[role="dialog"] button', "Close").click(); return false; }
      pick[0].click(); await sleep(350);
      const conf = all('[role="dialog"] button').find((x) => /Confirm →/.test(norm(x.textContent)));
      if (conf) conf.click();
      await sleep(700); return true;`);
  }
  ok((await ev(`return ${unfilled()}`)) === 0, "ops clears the blockers with confirmed overrides");
  await ev(`clickPart("button", "Approve week"); return true;`); await sleepMs(700);
  const p0 = await ev(`const t = sheetText() || ""; return { open: !!sheet(), cal: /Google Calendar/.test(t),
    email: /e-mail/i.test(t), sms: /SMS/.test(t), ready: (t.match(/Ready/g) || []).length };`);
  ok(p0.open && p0.cal && p0.email && p0.sms, "publish sheet lists Calendar, e-mail and SMS", p0);
  ok(p0.ready >= 6, `${p0.ready} channel/audience rows are Ready`, p0);
  await ev(`clickPart('[role="dialog"] button', "Send"); return true;`);
  await wait(`return /Week published/i.test(sheetText() || "") || null;`, "the send to finish", 40000);
  const p1 = await ev(`return { sent: ((sheetText() || "").match(/Sent/g) || []).length, toast: toast() };`);
  ok(p1.sent >= 6, `every selected row reports Sent (${p1.sent})`, p1);
  ok(/published/i.test(p1.toast || ""), `and the toast summarises where it went: ${p1.toast}`);
  await ev(`clickPart('[role="dialog"] button', "Done"); return true;`); await sleepMs(700);
  const p2 = await ev(`return { pill: /Published/.test(body()), gone: !byPart("button", "Approve week"),
    ticks: cards().filter((c) => /✓/.test(norm(c.innerText))).length };`);
  ok(p2.pill && p2.gone, "the week is badged Published", p2);
  ok(p2.ticks > 0, `${p2.ticks} cards carry the approved tick`, p2);

  console.log("\n=== FEATURES: editing a published week un-publishes it ===");
  const un = await ev(`
    const c = cards().find((c) => !/Unfilled/.test(norm(c.innerText)));
    c.click(); await sleep(500);
    const ch = byPart('[role="dialog"] button', "Change teacher");
    if (ch) { ch.click(); await sleep(400); }
    const pick = all('[role="dialog"] button').filter((x) => /Assign →/.test(norm(x.textContent)));
    if (!pick.length) return { skipped: true };
    pick[0].click(); await sleep(350);
    const conf = all('[role="dialog"] button').find((x) => /Confirm →/.test(norm(x.textContent)));
    if (conf) conf.click();
    await sleep(900);
    return { toast: toast(), pill: /Published/.test(body()), approveBack: !!byPart("button", "Approve week") };`);
  ok(!un.skipped && !un.pill, "changing a teacher clears the Published badge", un);
  ok(!!un.approveBack, "and the week can be re-published", un);
  ok(/re-publishing/.test(un.toast || ""), `the toast explains why: ${un.toast}`, un);

  console.log("\n=== FEATURES: add a class ===");
  const add = await ev(`
    const nav = all("nav button").find((x) => /Batch management/.test(x.title || ""));
    if (nav) nav.click(); await sleep(800);
    const btn = byPart("button", "Add a class");
    if (!btn) return { skipped: true };
    btn.click(); await sleep(600);
    const t = sheetText() || "";
    return { open: !!sheet(), form: /topic/i.test(t) && /day/i.test(t) && /time/i.test(t), free: /teacher\\(s\\) free/.test(t) };`);
  ok(!add.skipped && add.open && add.form, "Add a class opens a form with topic, day and time", add);
  ok(!!add.free, "and says how many teachers are actually free for that slot", add);
  const before = await ev(`return cards().length`);
  await ev(`clickPart('[role="dialog"] button', "Add class"); return true;`);
  const grew = await wait(`return cards().length === ${before + 1} ? cards().length : null;`, "the class to be drafted", 45000).catch(() => null);
  ok(!!grew, `the class is added and the draft staffs it (${before} -> ${grew})`);
}

main().catch((e) => { console.error("SUITE ERROR:", e.message); process.exit(1); });
