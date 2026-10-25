import type { ReactNode } from "react";
import { I18nProvider } from "../../features/camera-sketch/i18n";

export default function CameraLayout({ children }: { children: ReactNode }) {
  return (
    <div className="theme-camera min-h-screen bg-app text-ink">
      <I18nProvider>{children}</I18nProvider>
    </div>
  );
}
