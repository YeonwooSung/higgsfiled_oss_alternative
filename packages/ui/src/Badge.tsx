import type { ReactNode } from "react";
import { cx } from "./cx";

const TONES = {
  neutral: "bg-app text-muted border-border",
  success: "bg-emerald-50 text-accent border-emerald-200",
  warning: "bg-amber-50 text-amber-800 border-amber-200",
  danger: "bg-red-50 text-danger border-red-200",
  mock: "bg-amber-100 text-amber-900 border-amber-300",
};

export function Badge({ tone = "neutral", icon, children, title }: { tone?: keyof typeof TONES; icon?: ReactNode; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium", TONES[tone])}>
      {icon}
      {children}
    </span>
  );
}
