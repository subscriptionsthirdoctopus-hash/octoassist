// OctoAssist — User Guide & Standard Operating Procedure
// Pure how-to-use guidance (no competitive positioning, no marketing).
// Uses compressed JPEGs from shots_light/ — every image preserves aspect ratio.
//
// Run: node build_sop.js
// Output: OctoAssist_SOP_and_Product_Guide.docx
const fs   = require('fs');
const path = require('path');
const d    = require('docx');
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  ImageRun, Table, TableRow, TableCell, WidthType, BorderStyle,
  ShadingType, Header, Footer, PageNumber, LevelFormat, PageBreak,
  TableOfContents, TabStopType, TabStopPosition,
} = d;

// ─── Brand ────────────────────────────────────────────────────────────
const NAVY  = "1B2A4A";
const TEAL  = "0097A7";
const SLATE = "546E7A";
const LIGHT = "F5F7FA";
const WHITE = "FFFFFF";
const FONT  = "Aptos";

const SHOTS = path.join(__dirname, "shots_light");

// ─── Helpers ──────────────────────────────────────────────────────────
const para = (text, opts = {}) => new Paragraph({
  spacing: { before: 60, after: 60, line: 300 },
  ...opts,
  children: opts.children || [
    new TextRun({ text, font: FONT, size: 22, color: opts.color || "1F2937" }),
  ],
});

const h1 = (t) => new Paragraph({
  heading: HeadingLevel.HEADING_1,
  spacing: { before: 360, after: 180 },
  pageBreakBefore: true,
  children: [new TextRun({ text: t, font: FONT, size: 36, bold: true, color: NAVY })],
});

const h2 = (t) => new Paragraph({
  heading: HeadingLevel.HEADING_2,
  spacing: { before: 280, after: 120 },
  children: [new TextRun({ text: t, font: FONT, size: 26, bold: true, color: NAVY })],
});

const h3 = (t) => new Paragraph({
  heading: HeadingLevel.HEADING_3,
  spacing: { before: 220, after: 80 },
  children: [new TextRun({ text: t, font: FONT, size: 22, bold: true, color: TEAL })],
});

const bullet = (t, level = 0) => new Paragraph({
  numbering: { reference: "bullets", level },
  spacing: { before: 40, after: 40 },
  children: [new TextRun({ text: t, font: FONT, size: 22, color: "1F2937" })],
});

const _stepCounters = new Map();
const numbered = (t, listKey = "default") => {
  const n = (_stepCounters.get(listKey) || 0) + 1;
  _stepCounters.set(listKey, n);
  return new Paragraph({
    spacing: { before: 40, after: 40 },
    indent: { left: 540, hanging: 280 },
    children: [
      new TextRun({ text: `${n}.  `, font: FONT, size: 22, bold: true, color: TEAL }),
      new TextRun({ text: t, font: FONT, size: 22, color: "1F2937" }),
    ],
  });
};

const note = (label, body, color = TEAL) => new Paragraph({
  spacing: { before: 80, after: 80 },
  shading: { type: ShadingType.CLEAR, fill: LIGHT, color: "auto" },
  border: { left: { style: BorderStyle.SINGLE, size: 24, color: color, space: 4 } },
  indent: { left: 200 },
  children: [
    new TextRun({ text: `${label}  `, font: FONT, size: 22, bold: true, color: color }),
    new TextRun({ text: body, font: FONT, size: 22, color: "1F2937" }),
  ],
});

const code = (t) => new Paragraph({
  spacing: { before: 80, after: 80 },
  shading: { type: ShadingType.CLEAR, fill: "F1F5F9", color: "auto" },
  border: {
    top:    { style: BorderStyle.SINGLE, size: 4, color: "CBD5E1" },
    bottom: { style: BorderStyle.SINGLE, size: 4, color: "CBD5E1" },
    left:   { style: BorderStyle.SINGLE, size: 4, color: "CBD5E1" },
    right:  { style: BorderStyle.SINGLE, size: 4, color: "CBD5E1" },
  },
  indent: { left: 100, right: 100 },
  children: [new TextRun({ text: t, font: "Consolas", size: 20, color: "0F172A" })],
});

// Image with aspect-ratio-preserving sizing — never stretches, never crops.
const img = (file, maxWidthInches = 5.5) => {
  const p = path.join(SHOTS, file);
  if (!fs.existsSync(p)) {
    return para(`[Screenshot ${file} not available]`, { color: "9CA3AF" });
  }
  const data = fs.readFileSync(p);
  // Read JPEG SOF marker for width/height
  let w = 0, h = 0;
  for (let i = 0; i < data.length - 9; i++) {
    if (data[i] === 0xFF) {
      const m = data[i + 1];
      if (m >= 0xC0 && m <= 0xCF && m !== 0xC4 && m !== 0xC8 && m !== 0xCC) {
        h = (data[i + 5] << 8) | data[i + 6];
        w = (data[i + 7] << 8) | data[i + 8];
        break;
      }
    }
  }
  if (!w || !h) {
    w = data.readUInt32BE(16);
    h = data.readUInt32BE(20);
  }
  const aspect = h / w;
  const MAX_H = 6.0;
  let widthInches = maxWidthInches;
  let heightInches = widthInches * aspect;
  if (heightInches > MAX_H) {
    heightInches = MAX_H;
    widthInches  = MAX_H / aspect;
  }
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 120, after: 60 },
    children: [
      new ImageRun({
        type: "jpg",
        data,
        transformation: { width: widthInches * 72, height: heightInches * 72 },
        altText: { title: file, description: `Screenshot: ${file}`, name: file },
      }),
    ],
  });
};

const caption = (t) => new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 0, after: 200 },
  children: [new TextRun({ text: t, font: FONT, size: 18, italics: true, color: SLATE })],
});

const kvTable = (rows, col1 = 2200, col2 = 7160) => new Table({
  width: { size: col1 + col2, type: WidthType.DXA },
  columnWidths: [col1, col2],
  rows: rows.map((r, i) => new TableRow({
    children: [
      new TableCell({
        width: { size: col1, type: WidthType.DXA },
        shading: { type: ShadingType.CLEAR, fill: i === 0 ? NAVY : (i % 2 ? "F8FAFC" : WHITE), color: "auto" },
        borders: {
          top:    { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
          bottom: { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
          left:   { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
          right:  { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
        },
        margins: { top: 100, bottom: 100, left: 140, right: 140 },
        children: [new Paragraph({ children: [new TextRun({ text: r[0], font: FONT, size: 22, bold: i === 0, color: i === 0 ? WHITE : NAVY })] })],
      }),
      new TableCell({
        width: { size: col2, type: WidthType.DXA },
        shading: { type: ShadingType.CLEAR, fill: i === 0 ? NAVY : (i % 2 ? "F8FAFC" : WHITE), color: "auto" },
        borders: {
          top:    { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
          bottom: { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
          left:   { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
          right:  { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
        },
        margins: { top: 100, bottom: 100, left: 140, right: 140 },
        children: [new Paragraph({ children: [new TextRun({ text: r[1], font: FONT, size: 22, bold: i === 0, color: i === 0 ? WHITE : "1F2937" })] })],
      }),
    ],
  })),
});

const procTable = (header, rows) => {
  const firstColIsShortDigit = rows.every(r => /^\d{1,2}$/.test((r[0] || "").trim()));
  const widths = firstColIsShortDigit ? [780, 2400, 6180] : [1900, 2500, 4960];
  const headerRow = new TableRow({
    tableHeader: true,
    children: header.map((hc, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: NAVY, color: "auto" },
      borders: {
        top:    { style: BorderStyle.SINGLE, size: 4, color: NAVY },
        bottom: { style: BorderStyle.SINGLE, size: 4, color: NAVY },
        left:   { style: BorderStyle.SINGLE, size: 4, color: NAVY },
        right:  { style: BorderStyle.SINGLE, size: 4, color: NAVY },
      },
      margins: { top: 100, bottom: 100, left: 140, right: 140 },
      children: [new Paragraph({ children: [new TextRun({ text: hc, font: FONT, size: 22, bold: true, color: WHITE })] })],
    })),
  });
  const dataRows = rows.map((r, idx) => new TableRow({
    children: r.map((cell, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: idx % 2 ? "F8FAFC" : WHITE, color: "auto" },
      borders: {
        top:    { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
        bottom: { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
        left:   { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
        right:  { style: BorderStyle.SINGLE, size: 2, color: "E5E7EB" },
      },
      margins: { top: 90, bottom: 90, left: 140, right: 140 },
      children: [new Paragraph({ children: [new TextRun({ text: cell, font: FONT, size: 22, bold: i === 0, color: i === 0 ? TEAL : "1F2937" })] })],
    })),
  }));
  return new Table({ width: { size: 9360, type: WidthType.DXA }, columnWidths: widths, rows: [headerRow, ...dataRows] });
};

const pgbreak = () => new Paragraph({ children: [new PageBreak()] });

// ─── Cover ────────────────────────────────────────────────────────────
const cover = [
  new Paragraph({ children: [new TextRun({ text: "", font: FONT })], spacing: { before: 2400 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "OctoAssist", font: FONT, size: 96, bold: true, color: NAVY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 80, after: 80 }, children: [new TextRun({ text: "User Guide", font: FONT, size: 44, color: TEAL })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 }, children: [new TextRun({ text: "How to use the OctoAssist ITSM platform", font: FONT, size: 24, italics: true, color: SLATE })] }),
  new Paragraph({ children: [new TextRun({ text: "", font: FONT })], spacing: { before: 2000 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 80, after: 40 }, children: [new TextRun({ text: "Third Octopus", font: FONT, size: 28, bold: true, color: NAVY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 }, children: [new TextRun({ text: "https://octoassist.thirdoctopus.com", font: FONT, size: 22, color: TEAL })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200 }, children: [new TextRun({ text: "Version 1.0  ·  May 2026", font: FONT, size: 20, color: SLATE })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 60 }, children: [new TextRun({ text: "Private & Confidential", font: FONT, size: 20, italics: true, color: SLATE })] }),
  pgbreak(),
];

const toc = [
  h1("Contents"),
  new Paragraph({ children: [new TextRun({ text: "Right-click and Update Field after opening in Word to refresh page numbers.", font: FONT, size: 18, italics: true, color: SLATE })], spacing: { after: 200 } }),
  new Paragraph({ children: [new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-3" })] }),
];

// ─── 1. About this guide ──────────────────────────────────────────────
const section1 = [
  h1("1.  About This Guide"),
  para("This is a how-to guide for the people who use OctoAssist every day."),
  para("If you raise tickets, sign in to the requester portal and read Section 8. If you work the helpdesk queue, start at Section 4 and work through Section 7. If you administer the platform, read Sections 2, 5 and 9 to configure SLAs, the CAB and the mail provider."),
  h2("1.1  What you can do in OctoAssist"),
  bullet("Raise and resolve incidents and service requests"),
  bullet("Submit and approve changes through the Change Advisory Board"),
  bullet("Track Windows endpoints, push software, schedule patches"),
  bullet("Read and write Knowledge Base articles"),
  bullet("Pull live KPIs and download 25+ CSV reports for audit evidence"),
  h2("1.2  Where the platform is hosted"),
  kvTable([
    ["Item", "Value"],
    ["URL", "https://octoassist.thirdoctopus.com"],
    ["Sign-in", "Microsoft Entra ID single sign-on, with local-password fallback"],
    ["Tenant", "Tema IT Team"],
    ["Endpoints enrolled", "43 Windows endpoints (live count)"],
    ["Users provisioned", "399 (admin / agent / requester roles)"],
    ["Mail", "Microsoft Graph — sender helpdesk@temaindia.com"],
  ]),
  h2("1.3  Who can do what"),
  procTable(["Role", "Can do", "Cannot do"], [
    ["Administrator", "Everything — Settings, users, SSO, mail provider, SLAs, holidays, CAB, asset groups, software catalog.", "Nothing restricted."],
    ["Agent", "Triage and resolve any ticket. Approve / reject changes if a CAB member. Trigger remote actions. Deploy software.", "Cannot edit Settings or provision users."],
    ["Requester", "Raise tickets, comment on own tickets, search the Knowledge Base.", "Cannot see other people's tickets or Assets / Patches / Software / Settings."],
  ]),
];

const section2 = [
  h1("2.  Sign In"),
  para("Open https://octoassist.thirdoctopus.com/login in any modern browser (Chrome, Edge, Firefox, Safari)."),
  img("01_login.jpg"),
  caption("Figure 1 — Sign-in page."),
  h2("2.1  Two ways to sign in"),
  numbered("Click Sign in with Microsoft Entra ID (Temaindia.com) — recommended for everyone. Use your work email.", "signin"),
  numbered("Or enter the local email + password supplied by your administrator (used when SSO is unavailable).", "signin"),
  note("Note:", "When you sign in by SSO for the first time, OctoAssist auto-creates your user record with the role assigned by the administrator. Subsequent sign-ins are silent."),
];

const section3 = [
  h1("3.  Your Home Screen — Tickets"),
  para("After signing in as an agent or administrator, you land on the Tickets page."),
  img("02_tickets_list.jpg"),
  caption("Figure 2 — Tickets list with saved filters and priority colour coding."),
  h2("3.1  Reading the list"),
  procTable(["Column", "Meaning", "Notes"], [
    ["Ticket", "Auto-generated number", "INC-xxx for incidents, SR-xxx for service requests."],
    ["Title", "What the requester typed", "Plus a small category pill."],
    ["Status", "Open / In Progress / On Hold / Resolved / Closed", "Coloured pill."],
    ["Priority", "Low / Medium / High / Critical", "Red, amber, blue, grey — easy to scan."],
    ["Reporter", "Who raised the ticket", "Linked to the user record."],
    ["Assignee", "Who is working on it", "Or Unassigned (filter chip)."],
    ["Resolve by", "When it must be resolved", "Auto-calculated from the SLA matrix."],
  ]),
  h2("3.2  Filters and search"),
  bullet("Saved filter chips at the top — Open · In Progress · On Hold · Resolved · Closed · All. Click any to narrow the list."),
  bullet("Type in the search box to filter by title, ticket number or reporter name."),
  bullet("Tick the Mine box to show only tickets where you are the assignee."),
];

const section4 = [
  h1("4.  Raise a Ticket — Agent Form"),
  para("Use this form when you are filing a ticket on behalf of a user or recording one you took over the phone."),
  img("04_ticket_new.jpg"),
  caption("Figure 3 — New-ticket form (agent side)."),
  h2("4.1  Step by step"),
  numbered("Click + Incident or + Service request in the top-right of the Tickets page.", "raise_agent"),
  numbered("Type a clear one-line Title (the user's exact words help when they search later).", "raise_agent"),
  numbered("Pick the Category — this drives the SLA the ticket will be measured against.", "raise_agent"),
  numbered("Pick the Priority based on impact (Low / Medium / High / Critical).", "raise_agent"),
  numbered("Pick the Reporter from the directory dropdown.", "raise_agent"),
  numbered("Set the Location (Mumbai HQ / Chembur / Vadodara / …) — drives location-based assignment.", "raise_agent"),
  numbered("Write a Description with what's happening, what was expected, any error messages.", "raise_agent"),
  numbered("Attach screenshots or log files if helpful (drag-drop, up to 25 MB each).", "raise_agent"),
  numbered("Click Submit. The reporter, assignee and CAB members are emailed instantly.", "raise_agent"),
];

const section5 = [
  h1("5.  Work a Ticket"),
  para("Click any ticket on the Tickets page to open the detail view. Here you triage, comment, change status and resolve."),
  img("03_ticket_detail.jpg"),
  caption("Figure 4 — Ticket detail with status controls and activity timeline."),
  h2("5.1  Five things you can do on this page"),
  procTable(["#", "Action", "How"], [
    ["1", "Change status", "Use the status pills on the right: Open → In Progress → On Hold → Resolved → Closed."],
    ["2", "Change priority", "Click the Priority pill and pick a new value."],
    ["3", "Reassign", "Pick another agent from the Assignee dropdown. Both old and new assignees get email."],
    ["4", "Add a comment", "Type in the box at the bottom. Tick Internal note if it should NOT be visible to the requester."],
    ["5", "Insert a reply", "Pick from the Predefined replies dropdown — saves typing for common responses."],
  ]),
  h2("5.2  When to use Internal note vs Public comment"),
  bullet("Public comment — emailed to the requester. Use this for status updates, requests for more information, resolution explanations."),
  bullet("Internal note — visible only to other staff. Use for technician hand-offs, troubleshooting notes, vendor-ticket references."),
  h2("5.3  SLA breach risk"),
  para("OctoAssist polls every five minutes for tickets within four hours of breach. They surface as SLA breach risk (<4h) in the notification bell and on your Tickets list with an amber chip. Breached tickets show a red chip and are included in the daily SLA report."),
  h2("5.4  Email notifications fired by ticket events"),
  procTable(["Event", "Recipients", "Subject"], [
    ["Ticket created", "Reporter + assignee (if set) + every CAB member", "[OctoAssist] T-xxxx — created"],
    ["Ticket assigned", "Reporter + new assignee", "[OctoAssist] T-xxxx — Assigned to Technician"],
    ["Status changed", "Reporter", "[OctoAssist] T-xxxx — Status Updated to …"],
    ["Priority changed", "Reporter", "[OctoAssist] T-xxxx — Priority Updated to …"],
    ["Public comment", "Reporter and assignee (not the author)", "[OctoAssist] T-xxxx — New comment posted"],
  ]),
];

const section6 = [
  h1("6.  Submit and Approve Changes"),
  para("Every infrastructure change with the potential to impact production goes through the Change module."),
  img("05_changes_list.jpg"),
  caption("Figure 5 — Changes list."),
  h2("6.1  Submit a change"),
  numbered("Open Changes → + New change.", "submit_change"),
  numbered("Fill Title, Type (Standard / Normal / Emergency), Risk and target completion date.", "submit_change"),
  numbered("Paste your runbook steps into Implementation plan.", "submit_change"),
  numbered("Paste your roll-back steps into Rollback plan — this field is required.", "submit_change"),
  numbered("Tick Linked tickets to attach any incidents this change addresses.", "submit_change"),
  numbered("Click Submit. Each CAB member receives email with one-click Approve / Reject.", "submit_change"),
  h2("6.2  Approve or reject as a CAB member"),
  numbered("Open the email and click Open in OctoAssist.", "cab_approve"),
  numbered("Read the implementation and rollback plans.", "cab_approve"),
  numbered("Click Approve or Reject. Add a comment explaining the decision.", "cab_approve"),
  numbered("Once quorum is reached, the change advances to Approved automatically.", "cab_approve"),
  h2("6.3  The seven-state change lifecycle"),
  para("Draft → Pre-CAB → Under Review → Approved → Scheduled → In Progress → Completed. Every transition is timestamped with the actor."),
];

const section7 = [
  h1("7.  Patches, Assets and Software"),
  h2("7.1  See patch posture"),
  para("Open Patches to see Windows-Update posture for every endpoint."),
  img("07_patches.jpg"),
  caption("Figure 6 — Patches list — per-endpoint posture."),
  bullet("Columns include hostname, assigned-to user, office, online status, missing critical count, missing important count, last installed."),
  bullet("Quick-filter chips at the top: Online · Critical pending · By office · By posture."),
  h2("7.2  Schedule a patch window"),
  numbered("Open Patches → Patch windows → + New window.", "patch_win"),
  numbered("Pick a start date and time.", "patch_win"),
  numbered("Select targets — individual endpoints, or an Asset Group (e.g. Mumbai laptops).", "patch_win"),
  numbered("Choose Auto-execute (agent installs as soon as window opens) or Manual.", "patch_win"),
  numbered("Save. The window appears in the dashboard and emails the notification address when it starts and completes.", "patch_win"),
  h2("7.3  Browse the asset inventory"),
  img("08_assets.jpg"),
  caption("Figure 7 — Assets list. Click any hostname to drill in."),
  para("Each asset detail page shows hardware spec, OS, software inventory, Windows Services panel, patch posture and history, remote actions queue, and all tickets the user at this endpoint ever raised."),
  h2("7.4  Deploy software"),
  img("10_software_deploy.jpg"),
  caption("Figure 8 — Software deploy: catalog dropdown + multi-select endpoints."),
  numbered("Open Software → Deploy software.", "deploy_sw"),
  numbered("Pick a package from the catalog dropdown.", "deploy_sw"),
  numbered("Pick target endpoints (multi-select) or pick an Asset Group.", "deploy_sw"),
  numbered("Click Queue deploy. Each target picks up the job at its next 30-second check-in, runs the installer with the catalog's silent-install switches, and reports back.", "deploy_sw"),
  h2("7.5  Install the OctoAssist agent on a new endpoint"),
  para("On any new Windows endpoint, open an elevated PowerShell and run this one-liner. It downloads the agent installer, registers the endpoint using your tenant enrolment key, and installs the OctoAssist Agent Windows service that auto-starts on boot."),
  code("powershell -ExecutionPolicy Bypass -Command \"Invoke-RestMethod https://octoassist.thirdoctopus.com/agent/install.ps1 | Invoke-Expression\""),
  note("Tip:", "Copy the up-to-date one-liner from Settings → Tenant — it includes your tenant's enrolment key already filled in."),
];

const section8 = [
  h1("8.  End-User Portal"),
  para("Every employee in the organisation has access to the OctoAssist requester portal. There are three things you can do here."),
  bullet("Raise a new ticket."),
  bullet("View status and replies on your existing tickets."),
  bullet("Search the Knowledge Base for self-service answers."),
  img("21_portal.jpg"),
  caption("Figure 9 — Portal dashboard showing your open and recent tickets."),
  h2("8.1  Raise a ticket"),
  numbered("Open https://octoassist.thirdoctopus.com/portal and sign in with your work email.", "portal_raise"),
  numbered("Click + Report an issue (or + Service Catalog for a request).", "portal_raise"),
  numbered("Pick a Category — Software / Hardware / Network / Email / Access. The category determines the SLA.", "portal_raise"),
  numbered("Type a one-line Title that summarises the issue.", "portal_raise"),
  numbered("Add a Description — what you were doing, what you expected, what actually happened, any error messages.", "portal_raise"),
  numbered("Attach screenshots or files if helpful (drag-drop, up to 25 MB each).", "portal_raise"),
  numbered("Click Submit. You'll receive an instant email confirmation with your ticket number.", "portal_raise"),
  img("22_portal_new.jpg"),
  caption("Figure 10 — Portal: submit a new ticket."),
  h2("8.2  Reply to a ticket"),
  para("When the IT team replies you receive an email. Click the link, sign in, type your reply, click Post. Your reply is visible to the assigned technician immediately."),
  h2("8.3  Search the Knowledge Base"),
  para("Before raising a ticket for common issues — password reset, VPN setup, printer install, Outlook configuration — try the Knowledge Base. Most articles are step-by-step with screenshots."),
  img("11_kb.jpg"),
  caption("Figure 11 — Knowledge Base: search-first interface."),
  h2("8.4  Track via email — no login required"),
  para("Every status change on your ticket fires an email to your inbox. The email contains the ticket number, the status change (e.g. Open → In Progress → Resolved), and the technician's comment. You can reply to that email directly and your reply is added to the ticket as a comment."),
];

const section9 = [
  h1("9.  Configure Settings (Admin)"),
  para("Settings is admin-only. Open it from the gear icon in the top nav, or by navigating to /settings."),
  img("14_settings.jpg"),
  caption("Figure 12 — Settings landing: every configurable area in one tile grid with live counts."),
  h2("9.1  SLA matrix"),
  para("Open Settings → SLA Matrix. Each category (Network, Endpoints, Email, Access, etc.) has two SLA values — first response time and resolution time — defined for each of the four priorities."),
  img("15_settings_sla.jpg"),
  caption("Figure 13 — SLA Matrix: per-category × per-priority targets."),
  h2("9.2  Holidays and SLA calendar"),
  para("Open Settings → Holidays & SLA Calendar. Fifteen standard Indian 2026 public holidays are seeded automatically. Add or remove dates that apply to your specific business; SLA business-day math will skip these dates."),
  img("19_settings_holidays.jpg"),
  caption("Figure 14 — Holidays calendar."),
  h2("9.3  Mail provider"),
  para("Open Settings → Notifications. Microsoft Graph is the recommended path: requires an Entra app registration with Mail.Send application permission. Paste the Client ID, Client Secret (Value), Tenant ID, and the licensed mailbox to send-as."),
  img("16_settings_notifications.jpg"),
  caption("Figure 15 — Mail provider configuration with encrypted client-secret storage."),
  note("Tip:", "After saving the Graph credentials, click Send test email at the top of the same page. A 200/202 response means the wiring is healthy. A 403 from Microsoft means Mail.Send admin consent has not been granted."),
  h2("9.4  Change Advisory Board"),
  para("Build the CAB in two steps."),
  numbered("Open Settings → CAB Members. Use the search box to find people from your Entra directory; tick the CAB box for each.", "cab_build"),
  numbered("Open the CAB Committees sub-tab. Two committees are seeded by default — Change Management Committee and Risk Management Committee. Add committees as your governance requires (Security Review, Architecture Review, etc.).", "cab_build"),
  img("17_settings_cab.jpg"),
  caption("Figure 16 — CAB Members with CAB Committees sub-nav."),
  h2("9.5  Software catalog"),
  para("Add the installers your team pushes regularly — Chrome, Acrobat, Teams, the VPN client, LOB apps — once. Each entry stores the installer URL, the silent-install switches, and an optional uninstall command. Every package appears in the Software → Deploy dropdown thereafter."),
  img("18_settings_software_catalog.jpg"),
  caption("Figure 17 — Software catalog: one entry per installer, reusable."),
  h2("9.6  Predefined replies"),
  para("Open Settings → Predefined Replies. Ten professional canned responses are seeded by default — Acknowledge & Investigate, Password Reset Completed, Escalated to Level 2, etc. Edit wording to match your team's voice; agents see these in a dropdown when adding a comment."),
  img("20_settings_reply_templates.jpg"),
  caption("Figure 18 — Predefined Replies."),
];

const section10 = [
  h1("10.  Reports"),
  para("Reports is the at-a-glance dashboard for IT leadership."),
  img("12_reports.jpg"),
  caption("Figure 19 — Reports dashboard with dynamic filter bar."),
  h2("10.1  Filters"),
  procTable(["Filter", "Values", "Purpose"], [
    ["Timeframe", "Last 7 / 15 / 30 / 90 / 180 days", "Narrow KPIs and charts to a specific look-back window."],
    ["Priority", "Low / Medium / High / Critical", "Focus on Critical for war-room reviews."],
    ["Category", "Network / Endpoints / Email / Access …", "Show only tickets in this functional area."],
  ]),
  h2("10.2  Reports library"),
  para("Open Reports → Reports library for the full catalogue of 25+ downloadable reports. Each is one click to download as CSV. Reports cite the relevant ISO 27001:2022 control where applicable."),
  img("13_reports_library.jpg"),
  caption("Figure 20 — Reports library."),
];

const section11 = [
  h1("11.  Quick Reference Card"),
  para("Common tasks and where to find them in OctoAssist."),
  procTable(["If you want to …", "Where", "Notes"], [
    ["Raise a ticket (agent)", "Tickets → + Incident / + Service request", "Pick category first — it drives the SLA."],
    ["Raise a ticket (requester)", "/portal → + Report an issue", "Or use the catalog button for a request."],
    ["Search the Knowledge Base", "Knowledge Base → search box", "Articles are category-tagged."],
    ["Approve a change", "Email link → Changes → CHG-xxxx", "Both Approve and Reject must include a comment."],
    ["Schedule patches", "Patches → Patch windows → + New", "Pick group + datetime + auto-execute mode."],
    ["Push software to many endpoints", "Software → Deploy → pick + group", "Group must exist in Settings → Asset Groups."],
    ["See endpoint's installed software", "Assets → click hostname → Software", "Software inventory is reported every check-in."],
    ["Add a CAB member", "Settings → CAB Members → tick checkbox", "They get CAB emails on every Change."],
    ["Test email wiring", "Settings → Notifications → Send test", "200/202 = healthy. 403 = no Mail.Send consent."],
    ["Add a public holiday", "Settings → Holidays → Add", "SLA math will skip this date thereafter."],
    ["Download an audit report", "Reports → Reports library → pick → CSV", "Use the date filters at the top of each report."],
    ["Find when something happened", "Settings → Remote Actions  /  Audit report", "Full event log with actor + timestamp."],
  ]),
];

const section12 = [
  h1("12.  Troubleshooting"),
  h2("12.1  Notifications are not arriving"),
  bullet("Open Settings → Notifications. Click Send test email."),
  bullet("A 200/202 response and an email in your inbox confirms the wiring."),
  bullet("If the test fails with HTTP 401, the client secret is wrong or expired — rotate it in Azure and paste the new Value."),
  bullet("If the test fails with HTTP 403, Mail.Send application permission has not been granted with admin consent in Entra — ask your tenant admin to grant it."),
  bullet("If the test silently no-ops with no email, the Send-as mailbox does not match a licensed Exchange Online mailbox."),
  h2("12.2  An endpoint shows offline"),
  bullet("On the endpoint, open services.msc and confirm the OctoAssist Agent service is running."),
  bullet("If stopped, start it. The watchdog scheduled task will keep it running across reboots."),
  bullet("If absent, re-run the install one-liner from Section 7.5."),
  bullet("If the service is running but the endpoint still shows offline, check outbound HTTPS to octoassist.thirdoctopus.com from the endpoint — corporate proxies can intercept."),
  h2("12.3  SLA breaches happening on weekends"),
  para("Open Settings → Holidays. If your business is closed on Saturdays / Sundays, add them to the holiday calendar or adjust the SLA matrix to use business hours."),
  h2("12.4  CAB members complain about email volume"),
  para("CAB members receive an email on every new incident AND on every change. If volume is overwhelming, separate operational visibility from CAB approval — keep senior reviewers in the Change Management committee only, and let one delegate per area receive the incident FYIs."),
  h1("13.  Glossary"),
  kvTable([
    ["Term", "Definition"],
    ["Agent (endpoint)", "The Windows service installed on each managed endpoint."],
    ["Agent (helpdesk)", "A user with the Agent role — a helpdesk technician."],
    ["Asset Group", "A reusable, named collection of endpoints for bulk targeting."],
    ["CAB", "Change Advisory Board — the committee that approves infrastructure changes."],
    ["Committee", "A named sub-group of CAB members (e.g. Change Management, Risk Management)."],
    ["Enrolment key", "Tenant-scoped secret used by the agent installer to register an endpoint."],
    ["Incident", "An unplanned interruption to service or quality reduction."],
    ["Patch window", "A scheduled bulk patching operation on a target set of endpoints."],
    ["Predefined reply", "A canned response agents insert from the comment dropdown."],
    ["Problem", "A root cause identified to explain one or many recurring incidents."],
    ["Requester", "An end user who raises tickets through the portal."],
    ["Service request", "A user-initiated request for provisioning (e.g. install software, reset access)."],
    ["SLA", "Service Level Agreement — time within which a ticket must be responded to or resolved."],
    ["Tenant", "A logical customer organisation; every record is tenant-scoped."],
  ]),
];

const header = new Header({
  children: [new Paragraph({
    tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
    children: [
      new TextRun({ text: "OctoAssist — User Guide", font: FONT, size: 18, color: NAVY, bold: true }),
      new TextRun({ text: "\tPrivate & Confidential", font: FONT, size: 18, color: SLATE, italics: true }),
    ],
  })],
});

const footer = new Footer({
  children: [new Paragraph({
    tabStops: [
      { type: TabStopType.CENTER, position: 4680 },
      { type: TabStopType.RIGHT,  position: TabStopPosition.MAX },
    ],
    children: [
      new TextRun({ text: "Third Octopus  ·  ", font: FONT, size: 18, color: SLATE }),
      new TextRun({ text: "May 2026", font: FONT, size: 18, color: SLATE, italics: true }),
      new TextRun({ text: "\tPage ", font: FONT, size: 18, color: SLATE }),
      new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: SLATE }),
      new TextRun({ text: " of ", font: FONT, size: 18, color: SLATE }),
      new TextRun({ children: [PageNumber.TOTAL_PAGES], font: FONT, size: 18, color: SLATE }),
      new TextRun({ text: "\toctoassist.thirdoctopus.com", font: FONT, size: 18, color: TEAL }),
    ],
  })],
});

const doc = new Document({
  creator: "Third Octopus",
  title:   "OctoAssist — User Guide",
  description: "User guidance for OctoAssist ITSM",
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, font: FONT, color: NAVY },
        paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: FONT, color: NAVY },
        paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, font: FONT, color: TEAL },
        paragraph: { spacing: { before: 220, after: 80 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "■", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 280 } },
                   run: { font: FONT, color: TEAL } } }] },
    ],
  },
  sections: [{
    properties: {
      page: {
        size:   { width: 12240, height: 15840 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: { default: header },
    footers: { default: footer },
    children: [
      ...cover,
      ...toc,
      ...section1,
      ...section2,
      ...section3,
      ...section4,
      ...section5,
      ...section6,
      ...section7,
      ...section8,
      ...section9,
      ...section10,
      ...section11,
      ...section12,
    ],
  }],
});

Packer.toBuffer(doc).then(buf => {
  const out = path.join(__dirname, "OctoAssist_SOP_and_Product_Guide.docx");
  fs.writeFileSync(out, buf);
  console.log("Wrote:", out, "(", Math.round(buf.length / 1024), "KB )");
});
