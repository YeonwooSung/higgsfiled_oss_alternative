import type { ReactNode } from "react";
import { StudioShell } from "../../shell/Sidebar";

// The route group puts the shell around every tool route; `/s/[token]` sits outside it on purpose.
export default function ToolsLayout({ children }: { children: ReactNode }) {
  return <StudioShell>{children}</StudioShell>;
}
