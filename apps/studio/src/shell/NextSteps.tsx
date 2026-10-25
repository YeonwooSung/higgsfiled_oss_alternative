"use client";

import type { AppSlug } from "@oc/contracts";
import { Button } from "@oc/ui";
import { ApiClient } from "@oc/web-core";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Loader2, Workflow } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { handoffButtons, handoffErrorText, type HandoffAsset, type HandoffButton } from "./handoff";
import { useShellI18n } from "./i18n";
import { PROJECTS_QUERY_KEY, projectHref } from "./tools";

// `POST /handoffs` is not scoped by tool; the client's slug only matters for app-scoped calls this does not make.
const handoffApi = new ApiClient("camera-sketch");

/**
 * "Next step" (spec §4.2): one button per result that the server allowlist lets another tool take. A click copies the
 * result into a new project of that tool and opens it; the target editor links and inspects it like an upload.
 */
export function NextSteps({ app, assets }: { app: AppSlug; assets: readonly (HandoffAsset | null | undefined)[] }) {
  const { t } = useShellI18n();
  const buttons = handoffButtons(app, assets);
  const [running, setRunning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!buttons.length) return null;
  return (
    <section aria-label={t("handoffNext")} className="space-y-2" data-region="next-steps">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("handoffNext")}</h2>
      <div className="flex flex-wrap gap-2">
        {buttons.map((b) => (
          <NextStepButton key={b.key} button={b} running={running} onStart={() => { setRunning(b.key); setError(null); }}
            onError={(e) => { setRunning(null); setError(handoffErrorText(e, t)); }} />
        ))}
      </div>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </section>
  );
}

function NextStepButton({ button, running, onStart, onError }: {
  button: HandoffButton;
  running: string | null;
  onStart: () => void;
  onError: (e: unknown) => void;
}) {
  const { t } = useShellI18n();
  const router = useRouter();
  const qc = useQueryClient();
  // One key per (asset, target) for the life of the button: a retry after a lost response replays the same hand-off
  // instead of creating a second project.
  const key = useRef<string | null>(null);
  const busy = running === button.key;

  const go = async () => {
    onStart();
    key.current ??= crypto.randomUUID();
    try {
      const out = await handoffApi.handoff(button.assetId, button.target, key.current);
      await qc.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY });
      // Stays disabled while the next tool loads.
      router.push(projectHref({ id: out.project_id, app_slug: out.app_slug }));
    } catch (e) {
      onError(e);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      {button.caption && <span className="text-[11px] text-muted">{button.caption}</span>}
      <Button size="sm" variant="secondary" disabled={running !== null} aria-busy={busy || undefined} onClick={() => void go()}
        data-handoff={button.target}
        icon={busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <ArrowRight className="h-4 w-4" aria-hidden />}>
        {busy ? t("handoffWorking") : t(button.labelKey)}
      </Button>
    </div>
  );
}

/** Start-screen hint for the three-tool demo chain (spec §4.3), linking to Product Lock where it begins. */
export function DemoChainHint() {
  const { t } = useShellI18n();
  return (
    <p className="flex flex-wrap items-start gap-x-2 gap-y-1 rounded-panel border border-border bg-panel px-3 py-2 text-xs text-muted" data-region="demo-chain">
      <Workflow className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" aria-hidden />
      <span className="min-w-0 flex-1">{t("demoChainHint")}</span>
      <Link href="/product" className="font-medium text-accent hover:underline">{t("demoChainLink")}</Link>
    </p>
  );
}
