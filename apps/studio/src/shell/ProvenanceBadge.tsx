"use client";

import type { HandoffInfo } from "@oc/contracts";
import { CornerDownRight } from "lucide-react";
import Link from "next/link";
import { provenanceHref } from "./handoff";
import { useShellI18n } from "./i18n";
import { findToolForSlug } from "./tools";

/** "From {tool} result" on a hand-off input; links to the source project while it still exists. */
export function ProvenanceBadge({ handoff }: { handoff: HandoffInfo | null | undefined }) {
  const { t } = useShellI18n();
  if (!handoff) return null;
  const tool = findToolForSlug(handoff.source_app);
  const text = t("handoffFrom", { tool: tool ? t(tool.labelKey) : handoff.source_app });
  const href = provenanceHref(handoff);
  const body = <><CornerDownRight className="h-3 w-3 shrink-0" aria-hidden /><span className="truncate">{text}</span></>;
  const cls = "inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-app px-2 py-0.5 text-xs font-medium text-muted";
  return href
    ? <Link href={href} title={handoff.source_title} data-badge="provenance" className={`${cls} hover:text-ink`}>{body}</Link>
    : <span title={handoff.source_title} data-badge="provenance" className={cls}>{body}</span>;
}
