import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

// Port 1420 is Tauri's dev-server convention and is in the core's CORS list.
// e2e runs pass `--port` on the command line (see test/e2e/ports.ts).
export default defineConfig({
  plugins: [preact()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  build: { target: "es2022" },
});
