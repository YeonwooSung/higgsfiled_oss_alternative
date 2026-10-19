import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}", "../../packages/ui/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        app: "#F5F4F0",
        panel: "#FFFFFF",
        stage: "#E9E8E3",
        ink: "#171A18",
        muted: "#666D68",
        accent: { DEFAULT: "#18794E", strong: "#12603D" },
        border: "#DADDD7",
        danger: "#B42318",
      },
      borderRadius: { panel: "12px", button: "8px" },
      fontFamily: { sans: ["var(--font-inter)", "var(--font-noto-kr)", "system-ui", "sans-serif"] },
      fontSize: { base: ["14px", "1.5"] },
      transitionDuration: { 150: "150ms" },
    },
  },
  plugins: [],
} satisfies Config;
