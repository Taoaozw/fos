import { resolve } from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    port: 5178,
    proxy: {
      "/v1": {
        target: process.env.FOS_BRAIN_URL ?? "http://127.0.0.1:7622",
        changeOrigin: true,
      },
    },
  },
})
