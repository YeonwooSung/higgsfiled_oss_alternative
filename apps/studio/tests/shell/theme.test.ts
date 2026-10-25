import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const css = readFileSync(fileURLToPath(new URL("../../src/app/globals.css", import.meta.url)), "utf8");

const THEMES = ["theme-camera", "theme-product", "theme-vfx"] as const;
const VARS = ["app", "panel", "stage", "ink", "muted", "border", "accent", "accent-strong", "danger", "on-accent"] as const;

/** Body of the first rule whose selector list is exactly `.<cls>`. */
function ruleBody(cls: string): string | null {
  const m = new RegExp(`(^|[}\\s])\\.${cls}\\s*\\{([^}]*)\\}`, "m").exec(css);
  return m ? m[2] : null;
}

describe("theme scopes in globals.css", () => {
  for (const theme of THEMES) {
    it(`.${theme} defines every --oc-* colour as an RGB triplet`, () => {
      const body = ruleBody(theme);
      expect(body, `.${theme} rule`).not.toBeNull();
      for (const v of VARS) {
        const m = new RegExp(`--oc-${v}\\s*:\\s*([^;]+);`).exec(body!);
        expect(m, `--oc-${v} in .${theme}`).not.toBeNull();
        expect(m![1].trim(), `--oc-${v} in .${theme}`).toMatch(/^\d{1,3} \d{1,3} \d{1,3}$/);
      }
    });
  }

  it("keeps the current Camera Sketch and Pocket VFX accents", () => {
    expect(ruleBody("theme-camera")).toMatch(/--oc-accent:\s*102 220 242;/); // #66DCF2
    expect(ruleBody("theme-vfx")).toMatch(/--oc-accent:\s*255 154 82;/); // #FF9A52
  });

  it("keeps the Product Lock stage light on its dark theme", () => {
    expect(ruleBody("theme-product")).toMatch(/--oc-stage:\s*233 232 227;/); // #E9E8E3
    expect(ruleBody("theme-product")).toMatch(/--oc-app:\s*18 21 19;/); // #121513
  });
});
