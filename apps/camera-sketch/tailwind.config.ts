import type { Config } from "tailwindcss";

// Camera Sketch is dark. `@oc/ui` uses the semantic names (app/panel/ink/muted/accent/border/danger)
// plus a few stock palette shades (emerald/amber/red 50-900) for badges and hover states; both are
// re-mapped here so the shared components render on dark surfaces without forking them.
export default {
  content: ["./src/**/*.{ts,tsx}", "../../packages/ui/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        app: "#101216",
        panel: "#191D24",
        stage: "#0A0C10",
        ink: "#F0F3F8",
        muted: "#A3ACB9",
        border: "#303845",
        accent: { DEFAULT: "#66DCF2", strong: "#8AE6F7" },
        path: "#66DCF2",
        target: "#F0BA65",
        generated: "#B89BFF",
        danger: "#FF8A80",
        emerald: { 50: "#16303a", 200: "#2c6a7a" },
        amber: { 50: "#2c2514", 100: "#3a2f14", 200: "#6b5421", 300: "#8a6d2b", 700: "#F0BA65", 800: "#F0BA65", 900: "#F0BA65" },
        red: { 50: "#3a1d1d", 200: "#7a3a3a" },
      },
      borderRadius: { panel: "10px", button: "6px" },
      fontFamily: { sans: ["var(--font-inter)", "var(--font-noto-kr)", "system-ui", "sans-serif"] },
      fontSize: { base: ["14px", "1.5"] },
      gridTemplateColumns: { shell: "240px minmax(0,1fr) 320px" },
    },
  },
  plugins: [],
} satisfies Config;
