"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Loader2, ShieldCheck } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import { Button } from "./ui";

/**
 * Brama logowania.
 *
 * Sesja zyje w ciasteczku HttpOnly, wiec nie ma jej w JavaScripcie i nie da sie
 * jej odczytac ze skryptu. Stan logowania ustalamy pytajac backend o /api/auth/me.
 * Dopoki nie wiadomo, nie renderujemy panelu - inaczej wszystkie ekrany
 * wystrzelilyby zapytania, ktore i tak wrocilyby z 401.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => api.me(),
    retry: false,
    staleTime: 60_000,
  });

  const login = useMutation({
    mutationFn: (value: string) => api.login(value),
    onSuccess: () => {
      setPassword("");
      setError(null);
      queryClient.clear(); // dane sprzed logowania sa i tak puste
      void me.refetch();
    },
    onError: (err: Error) => {
      setError(
        err.message.includes("401")
          ? "Nieprawidłowe hasło."
          : err.message.slice(0, 180),
      );
    },
  });

  if (me.isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
        <Loader2 className="size-5 animate-spin text-muted" />
      </div>
    );
  }

  // 403 na /api/auth/me oznacza wylacznie brak waznej licencji - logowanie
  // nic by tu nie zmienilo, wiec mowimy wprost, do kogo sie zwrocic.
  if (me.isError && me.error instanceof ApiError && me.error.status === 403) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg p-6">
        <div className="w-full max-w-md rounded-xl border border-warn/30 bg-warn-soft p-5">
          <p className="text-sm font-medium text-warn">Licencja panelu jest nieaktywna</p>
          <p className="mt-1 text-xs text-muted">{opisLicencji(me.error.message)}</p>
          <p className="mt-3 text-xs text-muted">
            Dane pozostają nienaruszone. Po wpisaniu nowego klucza licencyjnego panel wróci do
            działania bez utraty historii. W sprawie przedłużenia skontaktuj się z dostawcą panelu.
          </p>
        </div>
      </div>
    );
  }

  // Backend nieosiagalny - pokaz to wprost zamiast ekranu logowania,
  // ktory i tak by nie zadzialal
  if (me.isError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg p-6">
        <div className="w-full max-w-md rounded-xl border border-bad/30 bg-bad-soft p-5">
          <p className="text-sm font-medium text-bad">Brak połączenia z backendem</p>
          <p className="mt-1 font-mono text-xs break-all text-muted">
            {(me.error as Error).message}
          </p>
          <p className="mt-3 text-xs text-muted">
            Sprawdź, czy serwer API działa i czy adres w{" "}
            <code className="rounded bg-surface-2 px-1">NEXT_PUBLIC_API_URL</code> jest poprawny.
          </p>
        </div>
      </div>
    );
  }

  if (me.data?.authenticated) {
    return <>{children}</>;
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg p-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2.5">
          <div className="flex size-9 items-center justify-center rounded-lg bg-accent text-base font-bold text-white">
            A
          </div>
          <div>
            <div className="text-base leading-tight font-semibold">AdsPanel</div>
            <div className="text-xs leading-tight text-muted">Amazon Ads</div>
          </div>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (password) login.mutate(password);
          }}
          className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5"
        >
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-muted">Hasło</span>
            <div className="flex items-center gap-2 rounded-lg border border-border bg-bg px-2.5 focus-within:border-accent">
              <KeyRound className="size-4 shrink-0 text-muted" />
              <input
                type="password"
                autoFocus
                autoComplete="current-password"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                  setError(null);
                }}
                className="w-full bg-transparent py-2 text-sm outline-none"
              />
            </div>
          </label>

          {!me.data?.configured && (
            <p className="rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">
              Hasło nie zostało jeszcze ustawione. Uruchom na serwerze:{" "}
              <code className="font-mono">cli.py setpassword</code>
            </p>
          )}

          {error && (
            <p className="rounded-lg bg-bad-soft px-3 py-2 text-xs text-bad">{error}</p>
          )}

          <Button
            type="submit"
            variant="primary"
            disabled={!password || login.isPending}
          >
            {login.isPending && <Loader2 className="size-3.5 animate-spin" />}
            Zaloguj
          </Button>
        </form>

        <p className="mt-4 flex items-start gap-1.5 text-[11px] text-muted">
          <ShieldCheck className="mt-0.5 size-3.5 shrink-0" />
          Panel zarządza prawdziwym kontem reklamowym. Każda zmiana i tak wymaga osobnego
          zatwierdzenia, ale dostęp do niego jest chroniony hasłem.
        </p>
      </div>
    </div>
  );
}

/** Komunikat z backendu (`403 {"detail": ...}`) w postaci do pokazania. */
function opisLicencji(message: string): string {
  try {
    const body = JSON.parse(message.slice(message.indexOf("{")));
    const wazna = body?.licencja?.wazna_do as string | undefined;
    if (wazna) return `Licencja była ważna do ${wazna}.`;
  } catch {
    // tresc ucieta albo nie-JSON - zostaje opis ogolny
  }
  return "Brak ważnego klucza licencyjnego.";
}
