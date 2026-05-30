// OctoAssist — Hosting Infrastructure Requirements (tabular, branded)
// Third Octopus brand: Navy / Teal / Aptos.  Every section is a table.
// Run: node build_infra_req.js
// Output: OctoAssist_Hosting_Infrastructure_Requirements.docx
const fs   = require('fs');
const path = require('path');
const d    = require('docx');
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, BorderStyle, ShadingType,
  Header, Footer, PageNumber, TabStopType, TabStopPosition, PageBreak,
} = d;

const NAVY = "1B2A4A", TEAL = "0097A7", SLATE = "546E7A", WHITE = "FFFFFF";
const GREEN = "2E7D32", AMBER = "FF8F00", RED = "C62828";
const FONT = "Aptos";
const CW = 9360; // content width (US Letter, 1" margins)

const h1 = (t) => new Paragraph({
  heading: HeadingLevel.HEADING_1,
  spacing: { before: 320, after: 140 },
  children: [new TextRun({ text: t, font: FONT, size: 28, bold: true, color: NAVY })],
});
const lead = (t) => new Paragraph({
  spacing: { before: 40, after: 120, line: 290 },
  children: [new TextRun({ text: t, font: FONT, size: 21, color: "374151" })],
});
const blank = (sz = 120) => new Paragraph({ spacing: { after: sz }, children: [new TextRun({ text: "", font: FONT })] });

// Generic table builder.
//   header: array of column titles
//   rows:   array of arrays of cell strings (or {text,color,bold} objects)
//   widths: array of DXA column widths (must sum to CW)
//   firstColBold: bold + teal the first column of body rows
function tbl(header, rows, widths, firstColBold = true) {
  const headerRow = new TableRow({
    tableHeader: true,
    children: header.map((hc, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: NAVY, color: "auto" },
      margins: { top: 90, bottom: 90, left: 130, right: 130 },
      children: [new Paragraph({ children: [new TextRun({ text: hc, font: FONT, size: 20, bold: true, color: WHITE })] })],
    })),
  });
  const dataRows = rows.map((r, idx) => new TableRow({
    cantSplit: true,
    children: r.map((cell, i) => {
      const obj = (typeof cell === "object" && cell !== null) ? cell : { text: String(cell) };
      const isFirst = i === 0;
      return new TableCell({
        width: { size: widths[i], type: WidthType.DXA },
        shading: { type: ShadingType.CLEAR, fill: idx % 2 ? "F4F7FA" : WHITE, color: "auto" },
        margins: { top: 80, bottom: 80, left: 130, right: 130 },
        borders: {
          top:    { style: BorderStyle.SINGLE, size: 2, color: "E2E8F0" },
          bottom: { style: BorderStyle.SINGLE, size: 2, color: "E2E8F0" },
          left:   { style: BorderStyle.SINGLE, size: 2, color: "E2E8F0" },
          right:  { style: BorderStyle.SINGLE, size: 2, color: "E2E8F0" },
        },
        children: [new Paragraph({ children: [new TextRun({
          text: obj.text,
          font: FONT,
          size: 20,
          bold: obj.bold ?? (isFirst && firstColBold),
          color: obj.color ?? (isFirst && firstColBold ? TEAL : "1F2937"),
        })] })],
      });
    }),
  }));
  return new Table({ width: { size: CW, type: WidthType.DXA }, columnWidths: widths, rows: [headerRow, ...dataRows] });
}

// ── Cover ───────────────────────────────────────────────────────────
const cover = [
  new Paragraph({ spacing: { before: 2600 }, children: [new TextRun({ text: "", font: FONT })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "OctoAssist", font: FONT, size: 84, bold: true, color: NAVY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 100, after: 60 }, children: [new TextRun({ text: "Hosting Infrastructure Requirements", font: FONT, size: 34, color: TEAL })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 }, children: [new TextRun({ text: "Specification for deploying or migrating the OctoAssist ITSM platform", font: FONT, size: 22, italics: true, color: SLATE })] }),
  new Paragraph({ spacing: { before: 2000 }, children: [new TextRun({ text: "", font: FONT })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 }, children: [new TextRun({ text: "Third Octopus", font: FONT, size: 26, bold: true, color: NAVY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 }, children: [new TextRun({ text: "Modern Cloud  ·  Trusted Security  ·  Managed Outcomes", font: FONT, size: 20, italics: true, color: SLATE })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 160 }, children: [new TextRun({ text: "Version 1.0  ·  May 2026  ·  Private & Confidential", font: FONT, size: 18, color: SLATE })] }),
  new Paragraph({ children: [new PageBreak()] }),
];

// ── 1. Document control ─────────────────────────────────────────────
const s1 = [
  h1("1.  Document Control"),
  tbl(["Field", "Detail"], [
    ["Document", "OctoAssist — Hosting Infrastructure Requirements"],
    ["Version", "1.0"],
    ["Date", "May 2026"],
    ["Owner", "Third Octopus — Product Engineering"],
    ["Classification", "Private & Confidential"],
    ["Purpose", "Define the infrastructure required to host or migrate OctoAssist to a new environment"],
    ["Current environment", "Co-hosted on the hrms-erp droplet (DigitalOcean BLR1)"],
  ], [2600, 6760]),
];

// ── 2. Current (as-is) environment ──────────────────────────────────
const s2 = [
  h1("2.  Current Environment (As-Is)"),
  lead("OctoAssist currently co-hosts on the hrms-erp droplet, sharing the host, the nginx reverse proxy and the certbot TLS automation with the HRMS application."),
  tbl(["Layer", "Component", "Detail"], [
    ["Host",          "DigitalOcean droplet", "Region BLR1 · Ubuntu 24.04.4 LTS · kernel 6.8 · x86-64"],
    ["Capacity",      "Shared with HRMS",     "2 vCPU · 3.8 GB RAM · 77 GB disk (shared, not dedicated)"],
    ["Runtime",       "Docker + Compose",     "Docker 29.2 · Docker Compose v5"],
    ["App container", "octoassist-app",       "FastAPI on uvicorn · image 353 MB · port 8088 → 8080"],
    ["DB container",  "octoassist-postgres",  "postgres:16-alpine · image 395 MB"],
    ["Proxy / TLS",   "hrms-nginx",           "Owned by HRMS stack · terminates 80/443 · proxies the subdomain"],
    ["Certificate",   "Let's Encrypt",        "certbot on host · auto-renew every 90 days"],
    ["Public URL",    "DNS A record",         "octoassist.thirdoctopus.com"],
  ], [1500, 2700, 5160]),
];

// ── 3. Measured resource consumption ────────────────────────────────
const s3 = [
  h1("3.  Measured Resource Consumption"),
  lead("OctoAssist's own footprint is small. Figures below are steady-state at idle for the current load (43 endpoints, 399 users)."),
  tbl(["Resource", "App", "Postgres", "Total"], [
    ["RAM",            "119 MB", "66 MB", { text: "≈ 185 MB", bold: true, color: NAVY }],
    ["CPU (of 2 vCPU)","0.7 %",  "0.1 %", { text: "< 1 %", bold: true, color: NAVY }],
    ["Database size",  "—",      "18 MB", { text: "18 MB", bold: true, color: NAVY }],
    ["Disk (images + DB + uploads)", "353 MB", "395 MB", { text: "< 1.5 GB", bold: true, color: NAVY }],
  ], [3360, 2000, 2000, 2000]),
  blank(),
  new Paragraph({ children: [new TextRun({ text: "Note:  the two background threads (SLA escalator every 5 min, patch scheduler every 60 s) are the only steady-state work. The database grows slowly — well under 1 GB even at 10× the current endpoint count.", font: FONT, size: 19, italics: true, color: SLATE })] }),
];

// ── 4. Provisioning specification ───────────────────────────────────
const s4 = [
  h1("4.  Provisioning Specification (CPU · RAM · Disk · Bandwidth)"),
  lead("Headline specification when OctoAssist runs on its own server rather than co-hosted. Three tiers; Recommended is the default choice for the current and near-term load."),
  tbl(["Specification", { text: "Minimum", color: WHITE }, { text: "Recommended", color: WHITE }, { text: "Comfortable", color: WHITE }], [
    ["CPU",              "1 vCPU (x86-64)", { text: "2 vCPU (x86-64)", bold: true, color: NAVY }, "2–4 vCPU (x86-64)"],
    ["RAM",              "2 GB",            { text: "4 GB", bold: true, color: NAVY },             "8 GB"],
    ["HDD type",         "SSD (required)",  { text: "NVMe SSD (preferred)", bold: true, color: NAVY }, "NVMe SSD"],
    ["HDD size",         "20 GB",           { text: "40 GB", bold: true, color: NAVY },            "80 GB"],
    ["Bandwidth (link)", "10 Mbps",         { text: "25 Mbps", bold: true, color: NAVY },          "50–100 Mbps"],
    ["Monthly transfer", "≈ 50 GB",         { text: "≈ 150 GB", bold: true, color: NAVY },         "250 GB+"],
    ["Suitable for",     "Current load (43 endpoints)", { text: "Up to ~500 endpoints", bold: true, color: NAVY }, "1,000+ endpoints"],
  ], [2760, 2200, 2200, 2200]),
  blank(),
  tbl(["Constraint", "Requirement"], [
    ["CPU architecture", "x86-64 (amd64) — the image is not multi-arch; an ARM host needs the image rebuilt for arm64 first"],
    ["Disk type", { text: "SSD is required, not spinning HDD — Postgres + container I/O need low-latency random reads. NVMe preferred.", color: "1F2937" }],
    ["Operating system", "Any Docker-capable Linux; Ubuntu 22.04 / 24.04 LTS recommended"],
  ], [2600, 6760]),
];

// ── 5. Bandwidth detail ─────────────────────────────────────────────
const s4b = [
  h1("5.  Bandwidth — Detail"),
  lead("Public bandwidth demand is low. The figures below are conservative budgets for the current 43-endpoint scale; scale roughly linearly with endpoint count."),
  tbl(["Traffic source", "Direction", "Volume", "Notes"], [
    ["Agent check-ins",     "Inbound",   "Low / steady", "43 endpoints poll every 30 s — small JSON (1–2 KB each) + periodic asset snapshot (10–50 KB)"],
    ["Web UI (admin/agent)","Both",      "Low / bursty", "Pages 20–450 KB; only a handful of concurrent staff users"],
    ["Email (Graph)",       "Outbound",  "Very low",     "5–20 KB per notification, sent to graph.microsoft.com"],
    ["TLS + ACME renewal",  "Both",      "Negligible",   "Cert renewal every 90 days"],
    ["Software / patch payloads", "Outbound", "Bursty (only if self-hosted)", "Installers are normally fetched by endpoints DIRECT from vendor URLs — they do NOT transit OctoAssist unless you use the built-in upload-and-host pipeline"],
  ], [2300, 1500, 1900, 3660]),
  blank(),
  new Paragraph({ shading: { type: ShadingType.CLEAR, fill: "E8F4F7", color: "auto" },
    border: { left: { style: BorderStyle.SINGLE, size: 24, color: TEAL, space: 4 } },
    indent: { left: 200 }, spacing: { before: 60, after: 60 },
    children: [
      new TextRun({ text: "Sizing rule:  ", font: FONT, size: 20, bold: true, color: TEAL }),
      new TextRun({ text: "sustained public throughput stays under 1 Mbps average with 5–10 Mbps peaks. A 10 Mbps link is sufficient; 25 Mbps gives comfortable headroom. Add the installer size × endpoint count to the monthly transfer budget only if you host installers on OctoAssist rather than linking to vendor URLs.", font: FONT, size: 20, color: "1F2937" }),
    ] }),
];

// ── 6. Baseline operating system ────────────────────────────────────
const sOS = [
  h1("6.  Baseline Operating System"),
  lead("The application runs entirely inside a container, so the host OS only needs to run Docker, nginx and certbot. The current production baseline is below; alternatives are listed for portability."),
  tbl(["Item", "Baseline / Requirement"], [
    ["Operating system",  "Ubuntu 24.04 LTS (Noble Numbat) — current production baseline. Ubuntu 22.04 LTS also fully supported."],
    ["Kernel",            "Linux 6.x (production host runs 6.8.0-71-generic)"],
    ["Init system",       "systemd (used to run Docker + the nginx/certbot services at boot)"],
    ["CPU architecture",  "x86-64 (amd64) — mandatory unless the image is rebuilt for arm64"],
    ["Filesystem",        "ext4 or xfs on SSD/NVMe; Docker overlay2 storage driver (default)"],
    ["Supported alternatives", "Any modern Docker-capable Linux — Debian 12, RHEL 9, Rocky / AlmaLinux 9, Amazon Linux 2023. The container itself is OS-agnostic."],
    ["Not recommended",   "ARM/arm64 hosts (need image rebuild); Windows Server via Docker Desktop / WSL2 (not production-grade for this workload)"],
  ], [2600, 6760]),
];

// ── 7. Software prerequisites ───────────────────────────────────────
const s5 = [
  h1("7.  Software Prerequisites (Host)"),
  lead("Everything Python-related lives inside the container; the host needs only the items below."),
  tbl(["Component", "Minimum version", "Role"], [
    ["Docker Engine",   "24+ (host runs 29.2)", "Container runtime"],
    ["Docker Compose",  "v2+ (host runs v5)",   "Orchestrates the app + Postgres stack"],
    ["nginx",           "Any current",          "Reverse proxy + TLS termination (or a managed load balancer)"],
    ["certbot",         "Any current",          "Let's Encrypt certificate issue + auto-renew"],
    ["git",             "Any current",          "Clone the repository to build the image"],
  ], [2400, 2600, 4360]),
];

// ── 8. Access & administrative requirements ─────────────────────────
const sAccess = [
  h1("8.  Access & Administrative Requirements"),
  lead("Deploying and operating OctoAssist needs access at three levels: the host, the DNS / network, and the Microsoft 365 tenant (for SSO and mail). Each is listed below with who needs it and why."),
  tbl(["Access", "Level", "Held by", "Purpose"], [
    ["SSH — root or sudo",        "Host",     "Platform / IT admin",        "Install Docker, run compose, certbot, OS patching, log review"],
    ["SSH on port 22",            "Host",     "Restricted to admin IPs",    "Key-based auth only; must NOT be open to the public internet"],
    ["Docker group membership",   "Host",     "Deploy user (optional)",     "Run docker / compose without full root — hardening option"],
    ["DNS record management",     "Network",  "Owner of thirdoctopus.com DNS","Create or repoint the A record for the hostname"],
    ["Firewall / security group", "Network",  "Network admin",              "Open inbound 80 + 443; allow outbound to Microsoft + Let's Encrypt"],
    ["Entra ID directory role",   "M365",     "Global Admin / App Admin",   "Manage the SSO app registration, grant Mail.Send admin consent, rotate the client secret"],
    ["Licensed mailbox",          "M365",     "helpdesk@temaindia.com",     "Send-as identity for Microsoft Graph mail"],
    ["OctoAssist bootstrap admin","App",      "ADMIN_USERNAME / PASSWORD",  "First login; create and assign all other users / roles"],
    ["Git / registry read",       "Build",    "Deploy user",                "Clone the repository (or pull the image) to build on the host"],
  ], [2500, 1200, 2360, 3300]),
  blank(),
  new Paragraph({ shading: { type: ShadingType.CLEAR, fill: "FFF7E6", color: "auto" },
    border: { left: { style: BorderStyle.SINGLE, size: 24, color: AMBER, space: 4 } },
    indent: { left: 200 }, spacing: { before: 60, after: 60 },
    children: [
      new TextRun({ text: "Note:  ", font: FONT, size: 20, bold: true, color: AMBER }),
      new TextRun({ text: "the host-level (SSH) access and the Microsoft 365 (Entra) access are usually held by different teams. Plan for both before the migration window — the SSO + mail integration cannot be completed without an Entra administrator on hand.", font: FONT, size: 20, color: "1F2937" }),
    ] }),
];

// ── 9. Network, DNS, TLS, ports ─────────────────────────────────────
const s6 = [
  h1("9.  Network, DNS, TLS & Ports"),
  tbl(["Item", "Requirement"], [
    ["Public DNS",       "A record for the hostname → new host public IP"],
    ["Inbound 443/tcp",  "HTTPS — primary application traffic (required)"],
    ["Inbound 80/tcp",   "HTTP → HTTPS redirect + ACME (Let's Encrypt) challenge"],
    ["Other inbound",    "None — nothing else needs to be public"],
    ["TLS certificate",  "Let's Encrypt (free, auto-renew) or own cert — HTTPS is mandatory; agents reject plain HTTP"],
    ["Outbound egress",  "login.microsoftonline.com, graph.microsoft.com (SSO + mail); letsencrypt.org (certs); GitHub / PyPI (image build)"],
  ], [2600, 6760]),
  blank(),
  new Paragraph({ shading: { type: ShadingType.CLEAR, fill: "FFF7E6", color: "auto" },
    border: { left: { style: BorderStyle.SINGLE, size: 24, color: AMBER, space: 4 } },
    indent: { left: 200 }, spacing: { before: 60, after: 60 },
    children: [
      new TextRun({ text: "Critical:  ", font: FONT, size: 20, bold: true, color: AMBER }),
      new TextRun({ text: "Keep the hostname octoassist.thirdoctopus.com. All 43 endpoint agents and the Entra SSO redirect URI are bound to it — repoint the DNS A record and the migration is transparent. Change the hostname and every agent must be re-pointed and SSO reconfigured.", font: FONT, size: 20, color: "1F2937" }),
    ] }),
];

// ── 7. Persistent data to migrate ───────────────────────────────────
const s7 = [
  h1("10.  Persistent Data to Migrate"),
  lead("Exactly three artifacts carry state. Lose any one and data or access is lost."),
  tbl(["#", "Artifact", "Location", "Why it is critical"], [
    ["1", "Postgres data", "Docker volume octoassist_postgres_data", "All tickets, changes, assets, users, SLAs, KB, audit trail"],
    ["2", "Uploads",       "Host dir /opt/octoassist/uploads",       "Ticket attachments"],
    ["3", "Encryption key","OCTOASSIST_ENCRYPTION_KEY in .env",      "Fernet key that decrypts the Graph mail secret + SSO secrets in the DB"],
  ], [560, 2200, 3200, 3400]),
  blank(),
  lead("Migration in one line:  pg_dump the database, copy the uploads directory, and carry the encryption key to the new host."),
];

// ── 8. Environment variables / secrets ──────────────────────────────
const s8 = [
  h1("11.  Environment Variables & Secrets"),
  lead("Read from deploy/docker/.env. Graph mail and SSO secrets are NOT here — they live encrypted in the database."),
  tbl(["Variable", "Purpose", "Carry over?"], [
    ["POSTGRES_PASSWORD",         "Postgres password (internal only)", "May be regenerated"],
    ["ADMIN_USERNAME",            "Bootstrap admin login",             "Optional"],
    ["ADMIN_PASSWORD",            "Bootstrap admin password",          "Optional"],
    ["TENANT_NAME",               "Display name (Tema IT Team)",       "Yes"],
    ["OCTOASSIST_ENCRYPTION_KEY", "Fernet key for secrets at rest",    { text: "MUST carry verbatim", bold: true, color: RED }],
  ], [3600, 3760, 2000]),
];

// ── 9. External dependencies ────────────────────────────────────────
const s9 = [
  h1("12.  External Dependencies"),
  tbl(["Service", "Used for", "Migration impact"], [
    ["Microsoft Entra ID",        "Single sign-on",        "No change if hostname unchanged; else update OIDC redirect URI"],
    ["Microsoft Graph (Mail.Send)","Email notifications",  "No change — credentials travel inside the DB dump"],
    ["Let's Encrypt",             "TLS certificate",       "Re-issue on new host (automatic via certbot)"],
    ["Enrolled Windows endpoints","Inventory + actions",   "No change if hostname unchanged; else re-point all agents"],
  ], [2800, 2600, 3960]),
];

// ── 10. Backup & DR ─────────────────────────────────────────────────
const s10 = [
  h1("13.  Backup & Disaster Recovery"),
  tbl(["Item", "Recommendation"], [
    ["Database backup",  "Nightly pg_dump to off-box storage (S3 / Spaces), 30-day retention — DB is ~18 MB"],
    ["Uploads backup",   "Sync /opt/octoassist/uploads to the same off-box storage"],
    ["Encryption key",   "Store separately in a password manager / secrets vault — never alongside the DB backup"],
    ["Recovery time",    "Full rebuild on a fresh host ≈ 15 minutes (clone repo → compose up → restore dump)"],
  ], [2600, 6760]),
];

// ── 11. Summary ─────────────────────────────────────────────────────
const s11 = [
  h1("14.  Summary"),
  tbl(["Question", "Answer"], [
    ["Smallest viable host",   "1 vCPU · 2 GB RAM · 20 GB SSD · 10 Mbps · x86-64 Linux"],
    ["Recommended host",       "2 vCPU · 4 GB RAM · 40 GB NVMe SSD · 25 Mbps"],
    ["Disk type",              "SSD required (NVMe preferred) — never spinning HDD"],
    ["Bandwidth",              "10 Mbps min / 25 Mbps recommended; ≈ 50–150 GB transfer per month"],
    ["Host software",          "Docker + Docker Compose + nginx + certbot + git"],
    ["Public ports",           "80 and 443 only"],
    ["What to migrate",        "Postgres dump + uploads folder + encryption key"],
    ["Transparent cutover",    "Keep hostname octoassist.thirdoctopus.com and repoint DNS"],
    ["Endpoint impact",        "Zero if hostname unchanged (43 agents keep working)"],
  ], [2800, 6560]),
];

// ── Header / Footer ─────────────────────────────────────────────────
const header = new Header({ children: [new Paragraph({
  tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
  children: [
    new TextRun({ text: "OctoAssist — Hosting Infrastructure Requirements", font: FONT, size: 17, bold: true, color: NAVY }),
    new TextRun({ text: "\tPrivate & Confidential", font: FONT, size: 17, italics: true, color: SLATE }),
  ],
})] });
const footer = new Footer({ children: [new Paragraph({
  tabStops: [{ type: TabStopType.CENTER, position: 4680 }, { type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
  children: [
    new TextRun({ text: "Third Octopus  ·  May 2026", font: FONT, size: 17, color: SLATE }),
    new TextRun({ text: "\tPage ", font: FONT, size: 17, color: SLATE }),
    new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 17, color: SLATE }),
    new TextRun({ text: " of ", font: FONT, size: 17, color: SLATE }),
    new TextRun({ children: [PageNumber.TOTAL_PAGES], font: FONT, size: 17, color: SLATE }),
    new TextRun({ text: "\toctoassist.thirdoctopus.com", font: FONT, size: 17, color: TEAL }),
  ],
})] });

const doc = new Document({
  creator: "Third Octopus",
  title: "OctoAssist — Hosting Infrastructure Requirements",
  styles: {
    default: { document: { run: { font: FONT, size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: FONT, color: NAVY },
        paragraph: { spacing: { before: 320, after: 140 }, outlineLevel: 0 } },
    ],
  },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    headers: { default: header },
    footers: { default: footer },
    children: [
      ...cover, ...s1, ...s2, ...s3, ...s4, ...s4b, ...sOS, ...s5, ...sAccess, ...s6, ...s7, ...s8, ...s9, ...s10, ...s11,
    ],
  }],
});

Packer.toBuffer(doc).then(buf => {
  const out = path.join(__dirname, "OctoAssist_Hosting_Infrastructure_Requirements.docx");
  fs.writeFileSync(out, buf);
  console.log("Wrote:", out, "(", Math.round(buf.length / 1024), "KB )");
});
