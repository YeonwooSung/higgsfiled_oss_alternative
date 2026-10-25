import type { ReactNode } from "react";
import { I18nProvider } from "../../../features/pocket-vfx/i18n";

export default function VfxLayout({ children }: { children: ReactNode }) {
  return (
    <div className="theme-vfx min-h-screen bg-app text-ink">
      <I18nProvider>{children}</I18nProvider>
    </div>
  );
}
