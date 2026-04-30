import path from "path";
import react from "@vitejs/plugin-react-swc";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

const uiRoot = path.resolve(__dirname, "src/scgp_agent_hub/ui");

export default defineConfig({
  define: {
    __APP_NAME__: JSON.stringify("SCGP Agent Hub"),
  },
  plugins: [
    TanStackRouterVite({
      routesDirectory: path.resolve(uiRoot, "routes"),
      generatedRouteTree: path.resolve(uiRoot, "types/routeTree.gen.ts"),
      routeFileIgnorePrefix: "-",
      routeFileIgnorePattern: ".*\\.test\\.tsx?$",
      quoteStyle: "double",
    }),
    tailwindcss(),
    react(),
  ],
  resolve: {
    alias: {
      "@": uiRoot,
    },
  },
  root: uiRoot,
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: path.resolve(__dirname, "src/scgp_agent_hub/__dist__"),
    emptyOutDir: true,
  },
});
