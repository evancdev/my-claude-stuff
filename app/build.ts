import { packager } from "@electron/packager";
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join } from "node:path";

const here = import.meta.dirname;

// The app carries only these, plus the folders that lead to them.
const KEEP = ["/package.json", "/dist/renderer", "/dist/electron"];

function kept(file: string) {
  if (file.endsWith(".test.js")) return false;
  return file === "" || KEEP.some((k) => file === k || file.startsWith(k + "/") || k.startsWith(file + "/"));
}

function icon(work: string) {
  const src = ["icon.png", "icon.jpg"].map((f) => join(here, f)).find(existsSync);
  if (!src) return undefined;
  const square = join(work, "square.png");
  const out = (flag: string) => {
    const match = execFileSync("sips", ["-g", flag, src]).toString().match(/: (\d+)/);
    if (!match) throw new Error(`sips could not read the ${flag} of ${src}`);
    return Number(match[1]);
  };
  const side = String(Math.min(out("pixelWidth"), out("pixelHeight")));
  execFileSync("sips", ["-s", "format", "png", "-c", side, side, src, "--out", square], { stdio: "ignore" });
  const set = join(work, "icon.iconset");
  mkdirSync(set);
  for (const size of [16, 32, 128, 256, 512]) {
    for (const [scale, suffix] of [[1, ""], [2, "@2x"]] as const) {
      const px = String(size * scale);
      const file = join(set, `icon_${size}x${size}${suffix}.png`);
      execFileSync("sips", ["-z", px, px, square, "--out", file], { stdio: "ignore" });
    }
  }
  const icns = join(work, "icon.icns");
  execFileSync("iconutil", ["-c", "icns", set, "-o", icns]);
  return icns;
}

const work = mkdtempSync(join(tmpdir(), "grug-icon-"));
let built: string;
try {
  [built] = await packager({
    dir: here,
    out: join(here, "out"),
    overwrite: true,
    ignore: (file: string) => !kept(file),
    icon: icon(work),
    appBundleId: "dev.evanc.grug",
  });
} finally {
  rmSync(work, { recursive: true, force: true });
}

const dest = join(homedir(), "Applications", "Grug.app");
mkdirSync(dirname(dest), { recursive: true });
rmSync(dest, { recursive: true, force: true });
cpSync(join(built, "Grug.app"), dest, { recursive: true, verbatimSymlinks: true });
console.log(dest);
