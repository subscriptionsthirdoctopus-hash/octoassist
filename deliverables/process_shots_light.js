// Process screenshots for documents:
//   - JPEG quality 82
//   - Final dimensions roughly 1100 × ≤1400 — readable at 5–6 inch print width
//   - Aspect ratio preserved (no stretching)
//   - For very-tall full-page captures (taller than 1.5× wide):
//        crop the TOP portion to a 4:3 / 5:4 viewport before scaling.
//        This shows the page header + first several rows of content
//        — far more useful than scaling the entire 13,000-px-tall image
//        down to a 100-px-wide postage stamp.
const sharp = require('sharp');
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, 'screenshots');
const OUT = path.join(__dirname, 'shots_light');
fs.mkdirSync(OUT, { recursive: true });

const TARGET_W      = 1100;
const TARGET_MAX_H  = 1400;
const Q             = 82;

// If source is taller than 1.5× wide, crop top section first.
// Crop height = width × CROP_RATIO so the result frames cleanly.
const CROP_RATIO = 1.25;  // ≈ 5:4 — a "tall landscape" framing

(async () => {
  const files = fs.readdirSync(SRC).filter(f => f.endsWith('.png')).sort();
  for (const f of files) {
    const src = path.join(SRC, f);
    const out = path.join(OUT, f.replace(/\.png$/, '.jpg'));
    const stat = fs.statSync(src);
    if (stat.size === 0) { console.log('SKIP zero-byte:', f); continue; }
    try {
      const meta = await sharp(src, { limitInputPixels: 600_000_000 }).metadata();
      const aspect = meta.height / meta.width;

      let pipeline = sharp(src, { limitInputPixels: 600_000_000 });

      if (aspect > 1.5) {
        // Crop top portion to a tall-landscape viewport before scaling.
        const cropH = Math.min(meta.height, Math.round(meta.width * CROP_RATIO));
        pipeline = pipeline.extract({ left: 0, top: 0, width: meta.width, height: cropH });
      }

      await pipeline
        .resize({ width: TARGET_W, height: TARGET_MAX_H, fit: 'inside', withoutEnlargement: true })
        .flatten({ background: '#FFFFFF' })
        .jpeg({ quality: Q, progressive: true, mozjpeg: true })
        .toFile(out);

      const finStat = fs.statSync(out);
      const finMeta = await sharp(out).metadata();
      console.log(`${f.padEnd(38)} ${String(meta.width).padStart(5)}x${String(meta.height).padEnd(6)} -> ${String(finMeta.width).padStart(4)}x${String(finMeta.height).padEnd(4)}   ${String(Math.round(finStat.size/1024)).padStart(4)} KB${aspect > 1.5 ? '   (top-cropped)' : ''}`);
    } catch (e) {
      console.log('ERR', f, e.message);
    }
  }
})();
