import type { Config } from "tailwindcss";

// Pocket VFX is dark neutral with an orange CTA and a magenta effect overlay (spec §3). `@oc/ui` uses the semantic
// names (app/panel/ink/muted/accent/border/danger) plus a few stock palette shades (emerald/amber/red 50-900) for
// badges and hover states; both are re-mapped here so the shared components render on dark surfaces.
export default {
  content: ["./src/**/*.{ts,tsx}", "../../packages/ui/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        app: "#111113",
        panel: "#1C1C20",
        stage: "#080809",
        ink: "#F6F5F2",
        muted: "#AAA8AE",
        border: "#34343A",
        accent: { DEFAULT: "#FF9A52", strong: "#FFB27D" },
        effect: "#DD8BFF",
        tracking: "#69D6A5",
        warning: "#F2C86B",
        danger: "#FF8A80",
        emerald: { 50: "#16302A", 200: "#2E6B57" },
        amber: { 50: "#2C2514", 100: "#3A2F14", 200: "#6B5421", 300: "#8A6D2B", 700: "#F2C86B", 800: "#F2C86B", 900: "#F2C86B" },
        red: { 50: "#3A1D1D", 200: "#7A3A3A" },
      },
      borderRadius: { panel: "12px", card: "10px", button: "8px" },
      fontFamily: { sans: ["var(--font-inter)", "var(--font-noto-kr)", "system-ui", "sans-serif"] },
      fontSize: { base: ["14px", "1.5"] },
      gridTemplateColumns: { shell: "220px minmax(0,1fr) 300px" },
      height: { topbar: "56px", timeline: "160px" },
    },
  },
  plugins: [],
} satisfies Config;
