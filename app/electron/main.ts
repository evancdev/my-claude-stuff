import { app, BrowserWindow, dialog, ipcMain, nativeTheme, shell } from "electron";
import { writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { collect, readPlanFile } from "./plans.ts";
import { planStatus } from "./status.ts";

// Must match server.port in vite.config.ts.
const DEV_SERVER = "http://localhost:5199/";

// The title check keeps the installed app from loading whatever else happens
// to answer on the port. Templates only use a dev server when unpackaged; this
// app is always packaged, and hot reload in it is the point.
async function devServerUp() {
  try {
    const res = await fetch(DEV_SERVER, { signal: AbortSignal.timeout(300) });
    return res.ok && (await res.text()).includes("<title>Grug</title>");
  } catch {
    return false;
  }
}

// Electron's security checklist: never hand the OS a scheme it would run.
function openExternal(url: string) {
  try {
    if (["http:", "https:"].includes(new URL(url).protocol)) shell.openExternal(url);
  } catch {}
}

// --background in index.css. Without it, a resize shows white at the edges
// until the page repaints.
function background() {
  return nativeTheme.shouldUseDarkColors ? "#0a0a0a" : "#ffffff";
}

function openWindow(dev: boolean) {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 720,
    minHeight: 480,
    backgroundColor: background(),
    title: "Grug",
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      preload: join(import.meta.dirname, "preload.cjs"),
    },
  });
  win.webContents.setWindowOpenHandler(({ url: target }) => {
    openExternal(target);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, target) => {
    if (dev && target.startsWith(DEV_SERVER)) return;
    event.preventDefault();
    openExternal(target);
  });
  const load = dev
    ? win.loadURL(DEV_SERVER)
    : win.loadFile(join(import.meta.dirname, "..", "renderer", "index.html"));
  load.catch((err) => dialog.showErrorBox("Grug could not load its page", String(err?.message ?? err)));

  if (process.env.PLANS_SNAPSHOT) {
    const snapshot = process.env.PLANS_SNAPSHOT;
    win.webContents.once("did-finish-load", async () => {
      await new Promise((r) => setTimeout(r, 800));
      if (process.env.PLANS_CLICK) {
        await win.webContents.executeJavaScript(
          `[...document.querySelectorAll("#sidebar button")].find((b) => b.textContent.includes(${JSON.stringify(process.env.PLANS_CLICK)}))?.click()`,
        );
        await new Promise((r) => setTimeout(r, 200));
      }
      // Real mouse input, so pointer capture behaves as it does by hand.
      if (process.env.PLANS_DRAG) {
        const [from, to] = process.env.PLANS_DRAG.split(",").map(Number);
        const mouse = (type: "mouseDown" | "mouseMove" | "mouseUp", x: number) =>
          win.webContents.sendInputEvent({ type, x, y: 300, button: "left", clickCount: 1 });
        mouse("mouseDown", from);
        for (let i = 1; i <= 10; i++) mouse("mouseMove", from + ((to - from) * i) / 10);
        mouse("mouseUp", to);
        await new Promise((r) => setTimeout(r, 200));
      }
      const image = await win.webContents.capturePage();
      writeFileSync(snapshot, image.toPNG());
      app.quit();
    });
  }
}

app.whenReady().then(async () => {
  const projects = join(homedir(), ".claude", "projects");
  ipcMain.handle("plans", async () => ({ repos: await collect(projects) }));
  ipcMain.handle("plan-file", (_, repo, plan, file) => readPlanFile(projects, repo, plan, file));
  ipcMain.handle("plan-status", (_, repo, plan) => planStatus(projects, repo, plan));
  openWindow(await devServerUp());
  nativeTheme.on("updated", () => {
    for (const win of BrowserWindow.getAllWindows()) win.setBackgroundColor(background());
  });
});

// One window, so closing it quits, unlike the macOS default.
app.on("window-all-closed", () => app.quit());
