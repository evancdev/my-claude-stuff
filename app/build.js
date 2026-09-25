// The app runs plans.py and loads dist/ from this checkout by absolute path, so
// moving the checkout means rebuilding. Only main.js and preload.js are baked in.

const { packager } = require("@electron/packager");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function icon(work) {
  const src = ["icon.png", "icon.jpg"].map((f) => path.join(__dirname, f)).find(fs.existsSync);
  if (!src) return undefined;
  const square = path.join(work, "square.png");
  const out = (flag) => execFileSync("sips", ["-g", flag, src]).toString().match(/: (\d+)/)[1];
  const side = Math.min(Number(out("pixelWidth")), Number(out("pixelHeight")));
  execFileSync("sips", ["-s", "format", "png", "-c", side, side, src, "--out", square], { stdio: "ignore" });
  const set = path.join(work, "icon.iconset");
  fs.mkdirSync(set);
  for (const size of [16, 32, 128, 256, 512]) {
    for (const [scale, suffix] of [[1, ""], [2, "@2x"]]) {
      const px = String(size * scale);
      const file = path.join(set, `icon_${size}x${size}${suffix}.png`);
      execFileSync("sips", ["-z", px, px, square, "--out", file], { stdio: "ignore" });
    }
  }
  const icns = path.join(work, "icon.icns");
  execFileSync("iconutil", ["-c", "icns", set, "-o", icns]);
  return icns;
}

async function main() {
  const plans = path.resolve(__dirname, "..", "scripts", "plans.py");
  const renderer = path.join(__dirname, "dist");
  fs.writeFileSync(path.join(__dirname, "config.json"), JSON.stringify({ plans, renderer }) + "\n");

  const work = fs.mkdtempSync(path.join(os.tmpdir(), "grug-icon-"));
  // node_modules is ignored by hand: the packager's prune still copies
  // devDependencies, and main.js needs none of them.
  const [built] = await packager({
    dir: __dirname,
    out: path.join(__dirname, "out"),
    overwrite: true,
    ignore: [
      /^\/(out|dist|renderer|node_modules)($|\/)/,
      /^\/(build\.js|vite\.config\.mts|tsconfig\.json|components\.json)$/,
      /^\/icon\.(jpg|png)$/,
    ],
    icon: icon(work),
    appBundleId: "dev.evanc.grug",
  }).finally(() => fs.rmSync(work, { recursive: true, force: true }));

  const dest = path.join(os.homedir(), "Applications", "Grug.app");
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.rmSync(dest, { recursive: true, force: true });
  fs.cpSync(path.join(built, "Grug.app"), dest, { recursive: true, verbatimSymlinks: true });
  console.log(dest);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
