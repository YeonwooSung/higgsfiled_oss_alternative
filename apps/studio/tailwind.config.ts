import type { Config } from "tailwindcss";

// The studio hosts three tools with one dark shell. Semantic colours are CSS variables (RGB triplets)
// that each theme scope in globals.css (`theme-camera`, `theme-product`, `theme-vfx`) redefines, so
// `@oc/ui` and the tools render in the active tool's palette and alpha modifiers (`bg-panel/90`) work.
// Tool-only colours (path/target/generated from Camera Sketch, effect/tracking/warning from Pocket VFX)
// keep fixed values.
const v = (name: string) => `rgb(var(--oc-${name}) / <alpha-value>)`;

export default {
  content: ["./src/**/*.{ts,tsx}", "../../packages/ui/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        app: v("app"),
        panel: v("panel"),
        stage: v("stage"),
        ink: v("ink"),
        muted: v("muted"),
        border: v("border"),
        accent: { DEFAULT: v("accent"), strong: v("accent-strong") },
        danger: v("danger"),
        "on-accent": v("on-accent"),
        path: "#66DCF2",
        target: "#F0BA65",
        generated: "#B89BFF",
        effect: "#DD8BFF",
        tracking: "#69D6A5",
        warning: "#F2C86B",
        emerald: { 50: v("emerald-50"), 200: v("emerald-200") },
        amber: {
          50: v("amber-50"),
          100: v("amber-100"),
          200: v("amber-200"),
          300: v("amber-300"),
          700: v("amber-700"),
          800: v("amber-800"),
          900: v("amber-900"),
        },
        red: { 50: v("red-50"), 200: v("red-200") },
      },
      borderRadius: { panel: "12px", card: "10px", button: "8px" },
      fontFamily: { sans: ["var(--font-inter)", "var(--font-noto-kr)", "system-ui", "sans-serif"] },
      fontSize: { base: ["14px", "1.5"] },
      gridTemplateColumns: {
        "camera-shell": "240px minmax(0,1fr) 320px",
        "vfx-shell": "220px minmax(0,1fr) 300px",
      },
      height: { topbar: "56px", timeline: "160px" },
      transitionDuration: { 150: "150ms" },
    },
  },
  plugins: [],
} satisfies Config;
