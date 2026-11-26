"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  // QueryClient tworzymy raz na cykl zycia aplikacji (leniwy inicjalizator),
  // zeby cache nie kasowal sie przy kazdym renderze.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            refetchOnWindowFocus: false,
            staleTime: 30_000,
            // Domyslnie trzy ponowienia. Przy zestawieniu, ktore liczy sie
            // dlugo, kazde ponowienie dokladalo serwerowi to samo liczenie
            // od nowa - a uzytkownik czekal na blad cztery razy dluzej.
            // Ponawiamy raz i tylko bledy sieci albo 502/503/504, nie 4xx.
            retry: (proby, blad) => {
              const status = (blad as { status?: number })?.status;
              if (status !== undefined && ![502, 503, 504].includes(status)) return false;
              return proby < 1;
            },
          },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
