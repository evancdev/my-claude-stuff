const { app, BrowserWindow, dialog, ipcMain, nativeTheme, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

function config() {
  try {
    return JSON.parse(fs.readFileSync(path.join(__dirname, "config.json")));
  } catch {
    return {};
  }
}

function plansScript() {
  return config().plans || path.join(__dirname, "..", "scripts", "plans.py");
}

function rendererDir() {
  return config().renderer || path.join(__dirname, "dist");
}

// Must match server.port in vite.config.mts.
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
function openExternal(url) {
  try {
    if (["http:", "https:"].includes(new URL(url).protocol)) shell.openExternal(url);
  } catch {}
}

// A Finder launch gets launchd's PATH, which has no pyenv or Homebrew in it.
// /usr/bin/python3 is last because it is the Xcode stub on a machine without
// the command line tools.
function python() {
  const found = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"];
  return found.find((p) => fs.existsSync(p)) || "python3";
}

let server = null;
let ready = null;

// A server that dies is started again on the next request, on a new port.
function serverUrl() {
  return (ready ??= startServer());
}

function startServer() {
  return new Promise((resolve, reject) => {
    // --exit-with-parent covers a crash or force quit, when stopServer never runs.
    server = spawn(python(), [plansScript(), "--port", "0", "--no-open", "--exit-with-parent"], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    server.stderr.on("data", (d) => (stderr = (stderr + d).slice(-4000)));
    server.stdout.on("data", (d) => {
      stdout += d;
      const match = stdout.match(/http:\/\/127\.0\.0\.1:\d+\//);
      if (match) resolve(match[0]);
    });
    server.on("error", reject);
    server.on("exit", (code, signal) => {
      server = null;
      ready = null;
      reject(new Error(stderr.trim() || `plans.py exited ${code ?? signal}`));
    });
  });
}

function stopServer() {
  if (server) server.kill();
}

// --background in index.css. Without it, a resize shows white at the edges
// until the page repaints.
function background() {
  return nativeTheme.shouldUseDarkColors ? "#0a0a0a" : "#ffffff";
}

function openWindow(dev) {
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
      preload: path.join(__dirname, "preload.js"),
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
  const load = dev ? win.loadURL(DEV_SERVER) : win.loadFile(path.join(rendererDir(), "index.html"));
  load.catch((err) => dialog.showErrorBox("Grug could not load its page", String(err.message || err)));

  if (process.env.PLANS_SNAPSHOT) {
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
        const mouse = (type, x) =>
          win.webContents.sendInputEvent({ type, x, y: 300, button: "left", clickCount: 1 });
        mouse("mouseDown", from);
        for (let i = 1; i <= 10; i++) mouse("mouseMove", from + ((to - from) * i) / 10);
        mouse("mouseUp", to);
        await new Promise((r) => setTimeout(r, 200));
      }
      const image = await win.webContents.capturePage();
      fs.writeFileSync(process.env.PLANS_SNAPSHOT, image.toPNG());
      app.quit();
    });
  }
}

app.whenReady().then(async () => {
  let dev;
  try {
    [, dev] = await Promise.all([serverUrl(), devServerUp()]);
  } catch (err) {
    dialog.showErrorBox("Grug could not start the plans server", String(err.message || err));
    app.quit();
    return;
  }
  ipcMain.handle("plans", async () => {
    const res = await fetch(`${await serverUrl()}api/plans`);
    if (!res.ok) throw new Error(`plans.py answered ${res.status}`);
    return res.json();
  });
  openWindow(dev);
  nativeTheme.on("updated", () => {
    for (const win of BrowserWindow.getAllWindows()) win.setBackgroundColor(background());
  });
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) openWindow(dev);
  });
});

// Closing the last window quits, unlike the macOS default. A server kept alive
// behind a closed window is the held port this app exists to avoid.
app.on("window-all-closed", () => app.quit());
app.on("will-quit", stopServer);
process.on("exit", stopServer);
