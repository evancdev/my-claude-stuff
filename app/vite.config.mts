import path from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Build only: the dev server needs inline scripts and a websocket for hot
// reload, which this policy would block.
const csp: Plugin = {
  name: "csp",
  apply: "build",
  transformIndexHtml: () => [
    {
      tag: "meta",
      attrs: {
        "http-equiv": "Content-Security-Policy",
        content: "default-src 'self'; style-src 'self' 'unsafe-inline'",
      },
      injectTo: "head-prepend",
    },
  ],
};

export default defineConfig({
  root: "renderer",
  // Relative asset paths, so dist/index.html works over file://.
  base: "./",
  plugins: [react(), tailwindcss(), csp],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "renderer/src") } },
  // Must match DEV_SERVER in main.js.
  server: { port: 5199, strictPort: true },
  build: { outDir: "../dist", emptyOutDir: true },
});
