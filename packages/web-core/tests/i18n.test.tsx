import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { createI18n, createLocaleStore, LocaleProvider, type I18n } from "../src/i18n";

// web-core tests run in a plain node environment (no DOM library), so interaction is driven through the
// pure locale store: we capture `setLocale` from a component inside provider A, call it, and re-render
// the tree with renderToString to see what provider B shows.

const A = createI18n({ en: { hello: "Hello" }, ko: { hello: "안녕하세요" } });
const B = createI18n({ en: { bye: "Goodbye" }, ko: { bye: "안녕히 가세요" } });

describe("createLocaleStore", () => {
  it("starts on the given locale and notifies subscribers on change", () => {
    const store = createLocaleStore("en");
    const seen: string[] = [];
    const off = store.subscribe(() => seen.push(store.get()));
    store.set("ko");
    store.set("ko"); // no-op: same value
    off();
    store.set("en");
    expect(seen).toEqual(["ko"]);
    expect(store.get()).toBe("en");
  });
});

describe("LocaleProvider", () => {
  it("shares one locale between two createI18n providers", () => {
    const store = createLocaleStore("en");
    let fromA: I18n<"hello"> | null = null;
    function ProbeA() {
      fromA = A.useI18n();
      return <span>{fromA.t("hello")}</span>;
    }
    function ProbeB() {
      return <span>{B.useI18n().t("bye")}</span>;
    }
    const tree = () => (
      <LocaleProvider store={store}>
        <A.I18nProvider>
          <ProbeA />
        </A.I18nProvider>
        <B.I18nProvider>
          <ProbeB />
        </B.I18nProvider>
      </LocaleProvider>
    );

    expect(renderToString(tree())).toContain("Goodbye");
    expect(fromA).not.toBeNull();
    fromA!.setLocale("ko");
    expect(store.get()).toBe("ko");
    const html = renderToString(tree());
    expect(html).toContain("안녕히 가세요");
    expect(html).toContain("안녕하세요");
  });

  it("exposes the shared locale through useLocale", async () => {
    const { useLocale } = await import("../src/i18n");
    const store = createLocaleStore("ko");
    function Probe() {
      return <span>{useLocale().locale}</span>;
    }
    expect(renderToString(<LocaleProvider store={store}><Probe /></LocaleProvider>)).toContain("ko");
  });
});

describe("createI18n without LocaleProvider", () => {
  it("falls back to its own locale state", () => {
    function Probe() {
      return <span>{A.useI18n().t("hello")}</span>;
    }
    expect(renderToString(<A.I18nProvider><Probe /></A.I18nProvider>)).toContain("Hello");
  });
});
