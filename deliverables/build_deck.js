// OctoAssist User Guidance Deck — Third Octopus brand
// Pure how-to-use guidance (no competitive comparison, no roadmap pitch).
// Uses compressed JPEG screenshots from shots_light/  (≈1100px wide each).
//
// Run: node build_deck.js
// Output: OctoAssist_Product_Deck.pptx
const PptxGenJS = require('pptxgenjs');
const fs   = require('fs');
const path = require('path');

const SHOTS = path.join(__dirname, "shots_light");

// ─── Brand ────────────────────────────────────────────────────────────
const NAVY  = "1B2A4A";
const TEAL  = "0097A7";
const SLATE = "546E7A";
const ICE   = "E8F4F7";
const WHITE = "FFFFFF";
const GREEN = "2E7D32";
const AMBER = "FF8F00";
const FONT  = "Aptos Display";

const pres = new PptxGenJS();
pres.layout = "LAYOUT_WIDE";  // 13.333 × 7.5
pres.title   = "OctoAssist — User Guide";
pres.author  = "Third Octopus";
pres.company = "Third Octopus";

// ─── Masters ──────────────────────────────────────────────────────────
pres.defineSlideMaster({
  title: "OCTO_CONTENT",
  background: { color: WHITE },
  objects: [
    { rect: { x: 0, y: 0, w: 13.333, h: 0.08, fill: { color: TEAL } } },
    { rect: { x: 0, y: 7.30, w: 13.333, h: 0.20, fill: { color: NAVY } } },
    { text: { text: "OctoAssist User Guide  ·  Private & Confidential",
      options: { x: 0.4, y: 7.30, w: 7, h: 0.20, fontFace: FONT, fontSize: 9, color: WHITE, valign: "middle" } } },
    { text: { text: "Third Octopus  ·  octoassist.thirdoctopus.com",
      options: { x: 6.5, y: 7.30, w: 6.0, h: 0.20, fontFace: FONT, fontSize: 9, color: WHITE, align: "right", valign: "middle", italic: true } } },
  ],
  slideNumber: { x: 12.85, y: 7.31, w: 0.4, h: 0.20, fontFace: FONT, fontSize: 9, color: WHITE, align: "right" },
});

pres.defineSlideMaster({
  title: "OCTO_TITLE",
  background: { color: NAVY },
  objects: [
    { rect: { x: 0, y: 7.30, w: 13.333, h: 0.20, fill: { color: TEAL } } },
    { text: { text: "Private & Confidential",
      options: { x: 0.5, y: 7.30, w: 12, h: 0.20, fontFace: FONT, fontSize: 9, color: WHITE, align: "right", valign: "middle", italic: true } } },
  ],
});

pres.defineSlideMaster({
  title: "OCTO_DIVIDER",
  background: { color: NAVY },
  objects: [
    { rect: { x: 0, y: 7.30, w: 13.333, h: 0.20, fill: { color: TEAL } } },
    { text: { text: "OctoAssist User Guide  ·  Private & Confidential",
      options: { x: 0.5, y: 7.30, w: 12, h: 0.20, fontFace: FONT, fontSize: 9, color: WHITE, align: "right", valign: "middle" } } },
  ],
});

// ─── Helpers ──────────────────────────────────────────────────────────
const addEyebrow = (s, t) => s.addText(t, {
  x: 0.5, y: 0.18, w: 12.3, h: 0.25,
  fontFace: FONT, fontSize: 11, color: TEAL, bold: true, charSpacing: 1.5,
});
const addTitle = (s, t) => s.addText(t, {
  x: 0.5, y: 0.30, w: 12.3, h: 0.7,
  fontFace: FONT, fontSize: 28, bold: true, color: NAVY,
});
const addNotes = (s, t) => { s.addNotes(t); };

// Add an image scaled to fit a given box (preserves aspect ratio, never stretches).
// `box` = { x, y, w, h }. We compute the actual width/height from the file
// so the image is centred inside its box at the correct aspect ratio.
const sharpSync = (() => {
  // Cache file dimensions so we don't re-decode per call
  const cache = {};
  return (file) => {
    if (cache[file]) return cache[file];
    const sharp = require('sharp');
    // Use sync-ish — we'll preload dimensions in a single async loop before build
    const buf = fs.readFileSync(file);
    cache[file] = buf;
    return buf;
  };
})();

const dimensions = (() => {
  // Pre-decoded image dimensions, populated by preloadDimensions()
  return {};
})();

async function preloadDimensions() {
  const sharp = require('sharp');
  const files = fs.readdirSync(SHOTS).filter(f => f.endsWith('.jpg'));
  for (const f of files) {
    const m = await sharp(path.join(SHOTS, f)).metadata();
    dimensions[f] = { w: m.width, h: m.height };
  }
}

const addImage = (slide, file, box) => {
  const p = path.join(SHOTS, file);
  if (!fs.existsSync(p)) return;
  const dim = dimensions[file];
  if (!dim) {
    slide.addImage({ path: p, x: box.x, y: box.y, w: box.w, h: box.h, sizing: { type: "contain", w: box.w, h: box.h } });
    return;
  }
  // Compute the largest rectangle inside the box that matches the image aspect
  const imgAspect = dim.w / dim.h;
  const boxAspect = box.w / box.h;
  let drawW, drawH;
  if (imgAspect >= boxAspect) {
    drawW = box.w;
    drawH = box.w / imgAspect;
  } else {
    drawH = box.h;
    drawW = box.h * imgAspect;
  }
  const drawX = box.x + (box.w - drawW) / 2;
  const drawY = box.y + (box.h - drawH) / 2;
  // Optional: subtle border for clarity
  slide.addShape("rect", { x: drawX, y: drawY, w: drawW, h: drawH, fill: { color: WHITE }, line: { color: "E5E7EB", width: 0.5 } });
  slide.addImage({ path: p, x: drawX, y: drawY, w: drawW, h: drawH });
};

// ─── Build slides ─────────────────────────────────────────────────────
async function buildDeck() {
  await preloadDimensions();

  // ─── 1. Title ──────────────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_TITLE" });
    s.addText("OctoAssist", {
      x: 0.5, y: 2.0, w: 12.3, h: 1.3,
      fontFace: FONT, fontSize: 80, bold: true, color: WHITE, align: "center",
    });
    s.addText("User Guide", {
      x: 0.5, y: 3.4, w: 12.3, h: 0.8,
      fontFace: FONT, fontSize: 32, color: ICE, align: "center", italic: true,
    });
    s.addShape("rect", { x: 5.5, y: 4.55, w: 2.3, h: 0.05, fill: { color: TEAL }, line: { color: TEAL } });
    s.addText("How to use the OctoAssist ITSM platform", {
      x: 0.5, y: 4.8, w: 12.3, h: 0.5,
      fontFace: FONT, fontSize: 18, color: WHITE, align: "center",
    });
    s.addText("Production environment", { x: 0.5, y: 5.7, w: 12.3, h: 0.4, fontFace: FONT, fontSize: 14, color: ICE, align: "center" });
    s.addText("https://octoassist.thirdoctopus.com", { x: 0.5, y: 6.05, w: 12.3, h: 0.5, fontFace: FONT, fontSize: 20, bold: true, color: TEAL, align: "center" });
    s.addText("Third Octopus  ·  May 2026  ·  Version 1.0", { x: 0.5, y: 6.7, w: 12.3, h: 0.35, fontFace: FONT, fontSize: 12, color: ICE, align: "center" });
  }

  // ─── 2. How this guide is organised ────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "INSIDE THIS GUIDE");
    addTitle(s, "Five things you will learn how to do");
    const items = [
      ["1", "Sign in",                         "How to access OctoAssist with Microsoft Entra ID single sign-on or a local password."],
      ["2", "Raise a ticket",                  "Two paths — the agent ticket form for technicians, the requester portal for everyone else."],
      ["3", "Work a ticket end-to-end",        "Triage, assign, comment, resolve, close — with the right keyboard shortcuts."],
      ["4", "Use the everyday IT pages",       "Changes · Problems · Patches · Assets · Software · Knowledge Base · Reports."],
      ["5", "Configure your settings",         "Where admins set SLAs, holidays, the CAB, the software catalog and email."],
    ];
    items.forEach((row, i) => {
      const y = 1.30 + i * 1.10;
      s.addShape("ellipse", { x: 0.6, y: y + 0.10, w: 0.5, h: 0.5, fill: { color: TEAL }, line: { color: TEAL } });
      s.addText(row[0], { x: 0.6, y: y + 0.10, w: 0.5, h: 0.5, fontFace: FONT, fontSize: 18, bold: true, color: WHITE, align: "center", valign: "middle" });
      s.addText(row[1], { x: 1.4, y: y + 0.10, w: 3.4, h: 0.5, fontFace: FONT, fontSize: 18, bold: true, color: NAVY, valign: "middle" });
      s.addText(row[2], { x: 4.9, y: y + 0.10, w: 7.9, h: 0.65, fontFace: FONT, fontSize: 13, color: "374151", valign: "middle" });
    });
    addNotes(s, "Set expectations: this is a how-to deck. We walk through five user journeys end-to-end, each with screenshots.");
  }

  // ─── 3. Sign in ────────────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 1 · SIGN IN");
    addTitle(s, "Open the sign-in page and choose how to authenticate");
    addImage(s, "01_login.jpg", { x: 0.5, y: 1.15, w: 8.0, h: 5.7 });
    s.addText("Two ways to sign in", { x: 8.85, y: 1.25, w: 4.0, h: 0.4, fontFace: FONT, fontSize: 16, bold: true, color: NAVY });
    s.addShape("rect", { x: 8.85, y: 1.65, w: 4.0, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    const ways = [
      ["🔐", "Microsoft Entra ID (SSO)", "Click 'Sign in with Microsoft Entra ID'. Use your work email. Recommended for everyone."],
      ["🔑", "Local password",          "Enter the email + password supplied by your administrator. Falls back when SSO is unavailable."],
    ];
    ways.forEach((w, i) => {
      const y = 1.95 + i * 1.85;
      s.addShape("roundRect", { x: 8.85, y, w: 4.0, h: 1.65, fill: { color: ICE }, line: { color: TEAL, width: 0.5 }, rectRadius: 0.05 });
      s.addText(w[0], { x: 9.0, y: y + 0.20, w: 0.7, h: 0.9, fontSize: 32 });
      s.addText(w[1], { x: 9.7, y: y + 0.20, w: 3.1, h: 0.45, fontFace: FONT, fontSize: 14, bold: true, color: NAVY });
      s.addText(w[2], { x: 9.7, y: y + 0.65, w: 3.1, h: 0.95, fontFace: FONT, fontSize: 11, color: "374151" });
    });
    addNotes(s, "Two sign-in paths. Most users go via SSO. The local password is the fallback for the bootstrap administrator and for accounts not yet provisioned in Entra.");
  }

  // ─── 4. Tickets list ───────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 2 · YOUR HOME SCREEN");
    addTitle(s, "The Tickets page — everything you need on one screen");
    addImage(s, "02_tickets_list.jpg", { x: 0.5, y: 1.15, w: 8.5, h: 5.7 });
    s.addText("What each column shows", { x: 9.35, y: 1.25, w: 3.6, h: 0.4, fontFace: FONT, fontSize: 15, bold: true, color: NAVY });
    s.addShape("rect", { x: 9.35, y: 1.65, w: 3.6, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    const cols = [
      ["Ticket",   "Auto-generated number — INC-xxx or SR-xxx"],
      ["Title",    "What the requester typed plus a category pill"],
      ["Status",   "Open · In Progress · On Hold · Resolved · Closed"],
      ["Priority", "Low · Medium · High · Critical (colour-coded)"],
      ["Reporter", "Who raised it"],
      ["Assignee", "Who is working on it (or Unassigned)"],
      ["Resolve by", "Auto-calculated from the SLA matrix"],
    ];
    cols.forEach((c, i) => {
      const y = 1.95 + i * 0.65;
      s.addText(c[0], { x: 9.35, y, w: 1.05, h: 0.5, fontFace: FONT, fontSize: 12, bold: true, color: TEAL, valign: "middle" });
      s.addText(c[1], { x: 10.45, y, w: 2.50, h: 0.5, fontFace: FONT, fontSize: 11, color: "1F2937", valign: "middle" });
    });
    addNotes(s, "Walk the columns left-to-right. Emphasise the saved filters at top (Open / Mine / Unassigned) and the search box that hits title + ticket number + reporter name.");
  }

  // ─── 5. Raise a ticket — agent side ───────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 3 · RAISE A TICKET");
    addTitle(s, "Submit a new ticket — agent form");
    addImage(s, "04_ticket_new.jpg", { x: 0.5, y: 1.15, w: 7.5, h: 5.7 });
    s.addText("What to fill in", { x: 8.3, y: 1.25, w: 4.5, h: 0.4, fontFace: FONT, fontSize: 16, bold: true, color: NAVY });
    s.addShape("rect", { x: 8.3, y: 1.65, w: 4.5, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    const steps = [
      ["1", "Title",       "One-line summary of the issue (under 80 characters)."],
      ["2", "Category",    "Pick the closest match — this drives the SLA."],
      ["3", "Priority",    "Low / Medium / High / Critical based on impact."],
      ["4", "Reporter",    "Pick from the directory if you are filing on behalf of a user."],
      ["5", "Location",    "Mumbai HQ / Chembur / Vadodara / etc. — drives location routing."],
      ["6", "Description", "What's happening, what was expected, what error message appeared."],
      ["7", "Submit",      "Reporter, assignee and CAB members are emailed instantly."],
    ];
    steps.forEach((it, i) => {
      const y = 1.95 + i * 0.65;
      s.addShape("ellipse", { x: 8.3, y, w: 0.40, h: 0.40, fill: { color: TEAL }, line: { color: TEAL } });
      s.addText(it[0], { x: 8.3, y, w: 0.40, h: 0.40, fontFace: FONT, fontSize: 13, bold: true, color: WHITE, align: "center", valign: "middle" });
      s.addText(it[1], { x: 8.80, y, w: 1.30, h: 0.40, fontFace: FONT, fontSize: 13, bold: true, color: NAVY, valign: "middle" });
      s.addText(it[2], { x: 10.15, y, w: 2.75, h: 0.40, fontFace: FONT, fontSize: 11, color: "374151", valign: "middle" });
    });
    addNotes(s, "Seven fields. Title and category are the most important — they determine routing and SLA. Encourage agents to fill the location field too if they know it.");
  }

  // ─── 6. Work a ticket ──────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 3 · WORK A TICKET");
    addTitle(s, "Triage → comment → resolve, all on one page");
    addImage(s, "03_ticket_detail.jpg", { x: 0.5, y: 1.15, w: 7.5, h: 5.7 });
    s.addText("Five things you can do here", { x: 8.3, y: 1.25, w: 4.5, h: 0.4, fontFace: FONT, fontSize: 16, bold: true, color: NAVY });
    s.addShape("rect", { x: 8.3, y: 1.65, w: 4.5, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    const acts = [
      ["1", "Change status",  "Open → In Progress → On Hold → Resolved → Closed."],
      ["2", "Change priority", "Raise or lower if impact changes."],
      ["3", "Reassign",        "Pick another technician; both parties get email."],
      ["4", "Add a comment",   "Public (emailed to requester) or Internal note."],
      ["5", "Insert a reply",  "Pick from predefined templates dropdown."],
    ];
    acts.forEach((it, i) => {
      const y = 1.95 + i * 0.95;
      s.addShape("ellipse", { x: 8.3, y, w: 0.40, h: 0.40, fill: { color: TEAL }, line: { color: TEAL } });
      s.addText(it[0], { x: 8.3, y, w: 0.40, h: 0.40, fontFace: FONT, fontSize: 13, bold: true, color: WHITE, align: "center", valign: "middle" });
      s.addText(it[1], { x: 8.80, y: y - 0.05, w: 4.10, h: 0.45, fontFace: FONT, fontSize: 14, bold: true, color: NAVY });
      s.addText(it[2], { x: 8.80, y: y + 0.40, w: 4.10, h: 0.45, fontFace: FONT, fontSize: 11, color: "374151" });
    });
    addNotes(s, "Walk one realistic ticket end-to-end. The activity log on the right is the audit trail — every state change is recorded.");
  }

  // ─── 7. Email notifications ────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 3 · NOTIFICATIONS");
    addTitle(s, "Who gets emailed and when");
    s.addText("Every state change fires an email via Microsoft Graph. You don't need to do anything — it's automatic.", { x: 0.5, y: 1.10, w: 12.3, h: 0.5, fontFace: FONT, fontSize: 14, color: "374151" });
    const rows = [
      ["Ticket created",    "Reporter + assignee (if set) + every CAB member",  "[OctoAssist] T-xxxx — created"],
      ["Ticket assigned",   "Reporter + new assignee",                          "[OctoAssist] T-xxxx — Assigned to Technician"],
      ["Status changed",    "Reporter",                                         "[OctoAssist] T-xxxx — Status Updated to …"],
      ["Priority changed",  "Reporter",                                         "[OctoAssist] T-xxxx — Priority Updated to …"],
      ["Public comment",    "Reporter and assignee (not the author)",           "[OctoAssist] T-xxxx — New comment posted"],
    ];
    const startY = 1.85, rowH = 0.85;
    const colX = [0.5, 3.0, 6.8];
    const colW = [2.5, 3.8, 6.0];
    // header
    s.addShape("rect", { x: 0.5, y: startY, w: 12.3, h: rowH, fill: { color: NAVY }, line: { color: NAVY } });
    s.addText("Event",       { x: colX[0] + 0.15, y: startY, w: colW[0], h: rowH, fontFace: FONT, fontSize: 14, bold: true, color: WHITE, valign: "middle" });
    s.addText("Recipients",  { x: colX[1] + 0.15, y: startY, w: colW[1], h: rowH, fontFace: FONT, fontSize: 14, bold: true, color: WHITE, valign: "middle" });
    s.addText("Email subject",{ x: colX[2] + 0.15, y: startY, w: colW[2], h: rowH, fontFace: FONT, fontSize: 14, bold: true, color: WHITE, valign: "middle" });
    rows.forEach((r, i) => {
      const y = startY + (i + 1) * rowH;
      const bg = i % 2 ? "F8FAFC" : WHITE;
      s.addShape("rect", { x: 0.5, y, w: 12.3, h: rowH, fill: { color: bg }, line: { color: "E5E7EB" } });
      s.addText(r[0], { x: colX[0] + 0.15, y, w: colW[0], h: rowH, fontFace: FONT, fontSize: 12, bold: true, color: TEAL, valign: "middle" });
      s.addText(r[1], { x: colX[1] + 0.15, y, w: colW[1], h: rowH, fontFace: FONT, fontSize: 11, color: "1F2937", valign: "middle" });
      s.addText(r[2], { x: colX[2] + 0.15, y, w: colW[2], h: rowH, fontFace: FONT, fontSize: 11, color: "1F2937", valign: "middle" });
    });
    addNotes(s, "Five trigger events, five email types. Internal notes don't fire mail to the requester — only public comments do.");
  }

  // ─── 8. Changes ────────────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 4 · CHANGES");
    addTitle(s, "Submit a change for CAB review");
    addImage(s, "05_changes_list.jpg", { x: 0.5, y: 1.15, w: 12.3, h: 2.8 });
    // 7-state flow below
    const states = ["Draft", "Pre-CAB", "Under Review", "Approved", "Scheduled", "In Progress", "Completed"];
    const flowY = 4.30;
    const cellW = 1.62;
    const startX = 0.5 + (12.3 - states.length * cellW) / 2;
    states.forEach((st, i) => {
      const x = startX + i * cellW;
      s.addShape("roundRect", { x, y: flowY, w: cellW - 0.12, h: 0.55, fill: { color: i === 2 ? TEAL : NAVY }, line: { color: NAVY }, rectRadius: 0.04 });
      s.addText(st, { x, y: flowY, w: cellW - 0.12, h: 0.55, fontFace: FONT, fontSize: 11, bold: true, color: WHITE, align: "center", valign: "middle" });
      if (i < states.length - 1) {
        s.addText("→", { x: x + cellW - 0.15, y: flowY, w: 0.18, h: 0.55, fontSize: 18, color: TEAL, align: "center", valign: "middle", bold: true });
      }
    });
    s.addText("The seven-state change lifecycle", { x: 0.5, y: 5.0, w: 12.3, h: 0.35, fontFace: FONT, fontSize: 12, color: SLATE, align: "center", italic: true });
    // 3 cards
    const feats = [
      ["✅ Submit",           "Fill title, type (Standard / Normal / Emergency), risk, target date, plan + rollback. Click Submit."],
      ["📝 CAB review",        "Every CAB member receives an email with one-click Approve / Reject. Quorum enforced before Approved."],
      ["📅 Schedule + execute", "Pick a window, mark In Progress at the start, mark Completed at the end. Activity log preserves every step."],
    ];
    feats.forEach((f, i) => {
      const x = 0.5 + i * 4.15;
      const y = 5.50;
      s.addShape("roundRect", { x, y, w: 4.00, h: 1.55, fill: { color: WHITE }, line: { color: "E5E7EB" }, rectRadius: 0.05 });
      s.addShape("rect", { x, y, w: 0.10, h: 1.55, fill: { color: TEAL }, line: { color: TEAL } });
      s.addText(f[0], { x: x + 0.25, y: y + 0.10, w: 3.7, h: 0.4, fontFace: FONT, fontSize: 14, bold: true, color: NAVY });
      s.addText(f[1], { x: x + 0.25, y: y + 0.55, w: 3.7, h: 0.95, fontFace: FONT, fontSize: 11, color: "374151" });
    });
    addNotes(s, "Three steps: Submit → CAB review → Schedule + execute. The rollback-plan field is required — without it the system rejects the submission.");
  }

  // ─── 9. Patches ────────────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 4 · PATCHES");
    addTitle(s, "See patch posture, schedule patch windows");
    addImage(s, "07_patches.jpg", { x: 0.5, y: 1.15, w: 8.5, h: 5.7 });
    s.addText("What this page shows", { x: 9.35, y: 1.25, w: 3.6, h: 0.4, fontFace: FONT, fontSize: 14, bold: true, color: NAVY });
    s.addShape("rect", { x: 9.35, y: 1.65, w: 3.6, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    const items = [
      "Hostname + assigned-to user + office",
      "Online status (last 5 min check-in)",
      "Critical-pending patch count",
      "Important-pending patch count",
      "Last successful install date",
    ];
    items.forEach((t, i) => {
      const y = 1.95 + i * 0.50;
      s.addText("●", { x: 9.35, y, w: 0.25, h: 0.4, fontSize: 16, color: TEAL });
      s.addText(t,   { x: 9.65, y, w: 3.30, h: 0.4, fontFace: FONT, fontSize: 12, color: "1F2937", valign: "middle" });
    });
    s.addText("To schedule a patch window", { x: 9.35, y: 4.55, w: 3.6, h: 0.4, fontFace: FONT, fontSize: 14, bold: true, color: NAVY });
    s.addShape("rect", { x: 9.35, y: 4.95, w: 3.6, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    s.addText("Patches → Patch windows → New", { x: 9.35, y: 5.10, w: 3.6, h: 0.35, fontFace: FONT, fontSize: 12, bold: true, color: TEAL });
    s.addText("Pick start time + targets (endpoints or Asset Group) + auto-execute mode. Save.", { x: 9.35, y: 5.45, w: 3.6, h: 1.30, fontFace: FONT, fontSize: 11, color: "374151" });
    addNotes(s, "Filter chips at the top let you focus on Online only, Critical-pending only, or by office. The CSV export of this page is the auditor evidence for ISO 27001 A.8.8.");
  }

  // ─── 10. Software deploy ───────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 4 · DEPLOY SOFTWARE");
    addTitle(s, "Catalog-driven, multi-select, one-click queue");
    addImage(s, "10_software_deploy.jpg", { x: 0.5, y: 1.15, w: 8.5, h: 5.7 });
    s.addText("How to deploy software", { x: 9.35, y: 1.25, w: 3.6, h: 0.4, fontFace: FONT, fontSize: 14, bold: true, color: NAVY });
    s.addShape("rect", { x: 9.35, y: 1.65, w: 3.6, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    const steps = [
      ["1", "Pick a package", "From the catalog dropdown — Chrome, Acrobat, your LOB app, etc."],
      ["2", "Pick targets",   "Multi-select endpoints, OR pick an Asset Group."],
      ["3", "Queue deploy",   "Job runs at each endpoint's next 30-second check-in."],
      ["4", "Track status",   "Live status flips: pending → in_progress → succeeded / failed."],
    ];
    steps.forEach((stp, i) => {
      const y = 1.95 + i * 1.10;
      s.addShape("ellipse", { x: 9.35, y, w: 0.40, h: 0.40, fill: { color: TEAL }, line: { color: TEAL } });
      s.addText(stp[0], { x: 9.35, y, w: 0.40, h: 0.40, fontFace: FONT, fontSize: 13, bold: true, color: WHITE, align: "center", valign: "middle" });
      s.addText(stp[1], { x: 9.85, y: y - 0.02, w: 3.10, h: 0.40, fontFace: FONT, fontSize: 13, bold: true, color: NAVY });
      s.addText(stp[2], { x: 9.85, y: y + 0.38, w: 3.10, h: 0.65, fontFace: FONT, fontSize: 10.5, color: "374151" });
    });
    addNotes(s, "Reusable catalog means you never paste silent-install switches again. Add an installer once in Settings → Software catalog; pick it from the dropdown forever after.");
  }

  // ─── 11. Assets ────────────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 4 · ASSETS");
    addTitle(s, "Every Windows endpoint, reported by the agent");
    addImage(s, "08_assets.jpg", { x: 0.5, y: 1.15, w: 8.5, h: 5.7 });
    s.addText("Click any hostname to drill in", { x: 9.35, y: 1.25, w: 3.6, h: 0.4, fontFace: FONT, fontSize: 14, bold: true, color: NAVY });
    s.addShape("rect", { x: 9.35, y: 1.65, w: 3.6, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    const items = [
      "Hardware spec (CPU, RAM, disk)",
      "OS + build + last reboot",
      "Installed software inventory",
      "Windows Services panel",
      "Patch posture + history",
      "Remote actions queue + log",
      "All tickets this user raised",
    ];
    items.forEach((t, i) => {
      const y = 1.95 + i * 0.55;
      s.addText("●", { x: 9.35, y, w: 0.25, h: 0.4, fontSize: 14, color: TEAL });
      s.addText(t,   { x: 9.65, y, w: 3.30, h: 0.4, fontFace: FONT, fontSize: 12, color: "1F2937", valign: "middle" });
    });
    addNotes(s, "The Assets list is sortable by every column. To install the agent on a new endpoint, copy the one-liner from Settings → Tenant.");
  }

  // ─── 12. Knowledge Base ────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 4 · KNOWLEDGE BASE");
    addTitle(s, "Self-service answers for common questions");
    addImage(s, "11_kb.jpg", { x: 0.5, y: 1.15, w: 12.3, h: 5.7 });
    addNotes(s, "Search-first interface. Articles are category-tagged. Most KB items are step-by-step with screenshots — written to deflect tickets.");
  }

  // ─── 13. Reports ───────────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 4 · REPORTS");
    addTitle(s, "Live KPIs with three filters: timeframe, priority, category");
    addImage(s, "12_reports.jpg", { x: 0.5, y: 1.15, w: 8.5, h: 5.7 });
    s.addText("Filter bar", { x: 9.35, y: 1.25, w: 3.6, h: 0.4, fontFace: FONT, fontSize: 14, bold: true, color: NAVY });
    s.addShape("rect", { x: 9.35, y: 1.65, w: 3.6, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    const filters = [
      ["Timeframe", "7 / 15 / 30 / 90 / 180 days"],
      ["Priority",  "Low / Medium / High / Critical"],
      ["Category",  "Network / Endpoints / Email / Access …"],
    ];
    filters.forEach((f, i) => {
      const y = 1.95 + i * 0.85;
      s.addText(f[0], { x: 9.35, y, w: 3.6, h: 0.40, fontFace: FONT, fontSize: 13, bold: true, color: TEAL });
      s.addText(f[1], { x: 9.35, y: y + 0.40, w: 3.6, h: 0.40, fontFace: FONT, fontSize: 11, color: "374151" });
    });
    s.addText("On the page you also get", { x: 9.35, y: 4.65, w: 3.6, h: 0.4, fontFace: FONT, fontSize: 13, bold: true, color: NAVY });
    s.addText("KPI cards · status & priority breakdowns · SLA compliance · workload by assignee", { x: 9.35, y: 5.05, w: 3.6, h: 1.75, fontFace: FONT, fontSize: 11, color: "374151" });
    addNotes(s, "Filters auto-submit when you change them. The page redraws in under a second.");
  }

  // ─── 14. Reports library ───────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 4 · REPORTS LIBRARY");
    addTitle(s, "25+ downloadable reports, all CSV");
    addImage(s, "13_reports_library.jpg", { x: 0.5, y: 1.15, w: 12.3, h: 5.7 });
    addNotes(s, "Pick a report, click Download. Each CSV opens directly in Excel or Power BI. Use the date filters at the top of each report to narrow the export.");
  }

  // ─── 15. End-user portal ───────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "FOR REQUESTERS · PORTAL");
    addTitle(s, "Self-service portal — three things, no training");
    addImage(s, "21_portal.jpg",     { x: 0.5, y: 1.15, w: 6.2, h: 3.5 });
    addImage(s, "22_portal_new.jpg", { x: 6.8, y: 1.15, w: 6.0, h: 3.5 });
    s.addText("Dashboard — your tickets", { x: 0.5, y: 4.70, w: 6.2, h: 0.35, fontFace: FONT, fontSize: 12, color: SLATE, align: "center", italic: true });
    s.addText("New ticket — submit a request",  { x: 6.8, y: 4.70, w: 6.0, h: 0.35, fontFace: FONT, fontSize: 12, color: SLATE, align: "center", italic: true });
    const things = [
      ["🎟️", "Raise a ticket",     "Pick a category, type a description, attach files. Email confirmation in seconds."],
      ["📬", "Track your tickets", "Inbox-style view. Email update on every status change."],
      ["📚", "Search the KB",      "Self-service answers — password reset, VPN, printers, Outlook."],
    ];
    things.forEach((t, i) => {
      const x = 0.5 + i * 4.20;
      const y = 5.25;
      s.addShape("roundRect", { x, y, w: 4.05, h: 1.75, fill: { color: WHITE }, line: { color: TEAL, width: 1 }, rectRadius: 0.05 });
      s.addText(t[0], { x: x + 0.2, y: y + 0.15, w: 0.8, h: 0.7, fontSize: 28 });
      s.addText(t[1], { x: x + 1.0, y: y + 0.18, w: 2.9, h: 0.45, fontFace: FONT, fontSize: 15, bold: true, color: NAVY });
      s.addText(t[2], { x: x + 0.2, y: y + 0.95, w: 3.7, h: 0.75, fontFace: FONT, fontSize: 11, color: "374151" });
    });
    addNotes(s, "The portal is intentionally narrow. Three actions, no surprises. Most of your 399 users will only ever see this view.");
  }

  // ─── 16. Section divider — admin settings ──────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_DIVIDER" });
    s.addText("Step 5", {
      x: 0.5, y: 2.5, w: 12.3, h: 0.6, fontFace: FONT, fontSize: 22, color: TEAL, align: "center", italic: true,
    });
    s.addText("Configure Your Settings", {
      x: 0.5, y: 3.1, w: 12.3, h: 1.3, fontFace: FONT, fontSize: 50, bold: true, color: WHITE, align: "center",
    });
    s.addShape("rect", { x: 6.0, y: 4.55, w: 1.3, h: 0.08, fill: { color: TEAL }, line: { color: TEAL } });
    s.addText("Admin-only — where you set SLAs, holidays, CAB, mail and software catalog", {
      x: 0.5, y: 4.85, w: 12.3, h: 0.5, fontFace: FONT, fontSize: 17, color: ICE, align: "center", italic: true,
    });
  }

  // ─── 17. Settings tile grid ────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 5 · SETTINGS");
    addTitle(s, "One landing page, every configurable area");
    addImage(s, "14_settings.jpg", { x: 0.5, y: 1.15, w: 8.5, h: 5.7 });
    s.addText("Tiles you'll use most often", { x: 9.35, y: 1.25, w: 3.6, h: 0.4, fontFace: FONT, fontSize: 14, bold: true, color: NAVY });
    s.addShape("rect", { x: 9.35, y: 1.65, w: 3.6, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    const tiles = [
      ["⏱ SLA Matrix",        "Response + resolution per category × priority"],
      ["📅 Holidays",          "Skip dates in SLA business-day math"],
      ["📧 Notifications",     "Mail provider + send test email"],
      ["👥 CAB Members",       "Pick reviewers from Entra; sub-tab for committees"],
      ["📦 Software catalog",  "Curate installer entries"],
    ];
    tiles.forEach((t, i) => {
      const y = 1.95 + i * 1.00;
      s.addText(t[0], { x: 9.35, y, w: 3.6, h: 0.35, fontFace: FONT, fontSize: 12, bold: true, color: TEAL });
      s.addText(t[1], { x: 9.35, y: y + 0.35, w: 3.6, h: 0.60, fontFace: FONT, fontSize: 10.5, color: "374151" });
    });
    addNotes(s, "Each tile shows a live count so you can spot misconfiguration at a glance — e.g. 0 categories means no tickets will escalate.");
  }

  // ─── 18. SLA matrix ────────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 5 · SLA MATRIX");
    addTitle(s, "Per-category response and resolution targets");
    addImage(s, "15_settings_sla.jpg", { x: 0.5, y: 1.15, w: 12.3, h: 5.7 });
    addNotes(s, "The SLA matrix is the single source of truth for breach detection. Every ticket inherits its due-response and due-resolution timestamps from this matrix at creation time.");
  }

  // ─── 19. Notifications ─────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 5 · MAIL PROVIDER");
    addTitle(s, "Configure Microsoft Graph and send a test email");
    addImage(s, "16_settings_notifications.jpg", { x: 0.5, y: 1.15, w: 12.3, h: 5.7 });
    addNotes(s, "Microsoft Graph with Mail.Send application permission is the recommended mail path. Use Send test email after saving to confirm the wiring.");
  }

  // ─── 20. CAB / committees ──────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 5 · CAB MEMBERS");
    addTitle(s, "Pick reviewers from your directory; group into committees");
    addImage(s, "17_settings_cab.jpg", { x: 0.5, y: 1.15, w: 12.3, h: 5.7 });
    addNotes(s, "CAB members get emailed on every Change. Use the CAB Committees sub-nav to group them — Change Management, Risk Management, Security Review.");
  }

  // ─── 21. Software catalog ──────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 5 · SOFTWARE CATALOG");
    addTitle(s, "Add installers once — pick from a dropdown forever after");
    addImage(s, "18_settings_software_catalog.jpg", { x: 0.5, y: 1.15, w: 12.3, h: 5.7 });
    addNotes(s, "Add the installer URL, the silent-install switches, and (optionally) an uninstall command. Every package in this catalog appears in the Software → Deploy dropdown.");
  }

  // ─── 22. Holidays ──────────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 5 · HOLIDAYS");
    addTitle(s, "Skip business holidays in SLA math");
    addImage(s, "19_settings_holidays.jpg", { x: 0.5, y: 1.15, w: 12.3, h: 5.7 });
    addNotes(s, "Fifteen standard Indian 2026 public holidays are seeded automatically. Add or remove dates that apply to your specific business.");
  }

  // ─── 23. Predefined replies ────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "STEP 5 · PREDEFINED REPLIES");
    addTitle(s, "Curate canned responses your agents insert in one click");
    addImage(s, "20_settings_reply_templates.jpg", { x: 0.5, y: 1.15, w: 12.3, h: 5.7 });
    addNotes(s, "Ten professional templates are seeded by default — Acknowledge & Investigate, Password Reset Completed, Escalated to Level 2, etc. Edit wording to match your team's voice.");
  }

  // ─── 24. Quick reference card ──────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_CONTENT" });
    addEyebrow(s, "QUICK REFERENCE");
    addTitle(s, "Common tasks — where to go");
    const rows = [
      ["Raise a ticket (agent)",                "Tickets → New incident / New service request"],
      ["Raise a ticket (requester)",            "/portal → New ticket"],
      ["Search the Knowledge Base",             "Knowledge Base → search box"],
      ["Approve a change",                      "Email link → Changes → CHG-xxxx"],
      ["Schedule patches",                      "Patches → Patch windows → New window"],
      ["Push software to a group of endpoints", "Software → Deploy → pick package + group"],
      ["See an endpoint's installed software",  "Assets → click hostname → Software tab"],
      ["Add a CAB member",                      "Settings → CAB Members → tick checkbox"],
      ["Test email wiring",                     "Settings → Notifications → Send test"],
      ["Add a public holiday",                  "Settings → Holidays → Add"],
      ["Download an audit report",              "Reports → Reports library → pick → CSV"],
      ["Find when something happened",          "Settings → Remote Actions  /  Audit Trail report"],
    ];
    const startY = 1.20;
    const rowH = 0.46;
    s.addShape("rect", { x: 0.5, y: startY, w: 12.3, h: rowH, fill: { color: NAVY }, line: { color: NAVY } });
    s.addText("If you want to …",        { x: 0.7, y: startY, w: 5.0, h: rowH, fontFace: FONT, fontSize: 13, bold: true, color: WHITE, valign: "middle" });
    s.addText("Go here",                  { x: 5.9, y: startY, w: 7.0, h: rowH, fontFace: FONT, fontSize: 13, bold: true, color: WHITE, valign: "middle" });
    rows.forEach((r, i) => {
      const y = startY + (i + 1) * rowH;
      const bg = i % 2 ? "F8FAFC" : WHITE;
      s.addShape("rect", { x: 0.5, y, w: 12.3, h: rowH, fill: { color: bg }, line: { color: "E5E7EB" } });
      s.addText(r[0], { x: 0.7, y, w: 5.0, h: rowH, fontFace: FONT, fontSize: 11.5, color: "1F2937", valign: "middle" });
      s.addText(r[1], { x: 5.9, y, w: 7.0, h: rowH, fontFace: FONT, fontSize: 11.5, bold: true, color: TEAL, valign: "middle" });
    });
    addNotes(s, "Print this slide and tape it next to the helpdesk monitor.");
  }

  // ─── 25. Closing ───────────────────────────────────────────────────
  {
    const s = pres.addSlide({ masterName: "OCTO_TITLE" });
    s.addText("That's the tour", {
      x: 0.5, y: 2.3, w: 12.3, h: 1.3, fontFace: FONT, fontSize: 60, bold: true, color: WHITE, align: "center",
    });
    s.addShape("rect", { x: 5.7, y: 3.75, w: 1.9, h: 0.06, fill: { color: TEAL }, line: { color: TEAL } });
    s.addText("Open the application and try each step", {
      x: 0.5, y: 3.95, w: 12.3, h: 0.6, fontFace: FONT, fontSize: 22, color: ICE, align: "center", italic: true,
    });
    s.addText("Production environment", { x: 0.5, y: 5.2, w: 12.3, h: 0.4, fontFace: FONT, fontSize: 14, color: ICE, align: "center" });
    s.addText("https://octoassist.thirdoctopus.com", { x: 0.5, y: 5.55, w: 12.3, h: 0.5, fontFace: FONT, fontSize: 22, bold: true, color: TEAL, align: "center" });
    s.addText("Third Octopus  ·  Modern Cloud · Trusted Security · Managed Outcomes", { x: 0.5, y: 6.5, w: 12.3, h: 0.4, fontFace: FONT, fontSize: 12, color: ICE, align: "center", italic: true });
  }

  // ─── Save ──────────────────────────────────────────────────────────
  const outPath = path.join(__dirname, "OctoAssist_Product_Deck.pptx");
  await pres.writeFile({ fileName: outPath });
  const stat = fs.statSync(outPath);
  console.log("Wrote:", outPath, "(", Math.round(stat.size / 1024), "KB )");
}

buildDeck().catch(e => { console.error(e); process.exit(1); });
