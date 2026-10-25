import type { Metadata, Viewport } from "next";
import { Inter, Noto_Sans_KR } from "next/font/google";
import type { ReactNode } from "react";
import { en } from "../shell/i18n/en";
import "./globals.css";
import { Providers } from "./providers";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const noto = Noto_Sans_KR({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-noto-kr", display: "swap" });

export const metadata: Metadata = { title: en.appName, description: en.appDescription };
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#101216" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${noto.variable}`}>
      {/* The shell chrome uses the Camera Sketch neutrals; each tool route re-scopes its own subtree. */}
      <body className="theme-camera min-h-screen bg-app font-sans text-ink antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
