import type { Metadata, Viewport } from "next";
import { Inter, Noto_Sans_KR } from "next/font/google";
import type { ReactNode } from "react";
import "./globals.css";
import { Providers } from "./providers";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const noto = Noto_Sans_KR({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-noto-kr", display: "swap" });

export const metadata: Metadata = {
  title: "Product Lock",
  description: "Product ads that keep your product's logo, text and shape from the original photo.",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#F5F4F0" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${noto.variable}`}>
      <body className="min-h-screen font-sans antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
