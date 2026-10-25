"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LocaleProvider } from "@oc/web-core";
import { useState, type ReactNode } from "react";
import { ShellI18nProvider } from "../shell/i18n";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } }));
  return (
    <QueryClientProvider client={client}>
      <LocaleProvider>
        <ShellI18nProvider>{children}</ShellI18nProvider>
      </LocaleProvider>
    </QueryClientProvider>
  );
}
