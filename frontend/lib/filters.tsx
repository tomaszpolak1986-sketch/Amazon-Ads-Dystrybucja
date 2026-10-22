"use client";

import { useQuery } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, type Profile } from "./api";
import { isoDate } from "./format";

export const RANGE_PRESETS = [
  { key: "7", label: "7 dni", days: 7 },
  { key: "14", label: "14 dni", days: 14 },
  { key: "30", label: "30 dni", days: 30 },
  { key: "60", label: "60 dni", days: 60 },
  { key: "90", label: "90 dni", days: 90 },
] as const;

interface FilterState {
  profileId: number | null;
  setProfileId: (id: number | null) => void;
  days: number;
  setDays: (d: number) => void;
  start: string;
  end: string;
  /** Ustawia dowolny zakres dat. Wylacza preset, dopoki uzytkownik go nie wybierze. */
  setRange: (start: string, end: string) => void;
  /** Czy obowiazuje zakres wpisany recznie, a nie preset. */
  customRange: boolean;
  profiles: Profile[];
  currency: string;
  /** Zwieksza sie po kazdej akcji zapisu - strony obserwuja to i przeladowuja dane. */
  revision: number;
  bump: () => void;
  loadingProfiles: boolean;
  apiError: string | null;
}

const Ctx = createContext<FilterState | null>(null);

/**
 * Odczyt zapisanych ustawien musi byc leniwy, nie w efekcie: na serwerze
 * localStorage nie istnieje, wiec zwracamy wartosc domyslna, a w przegladarce
 * odtwarzamy wybor uzytkownika juz przy pierwszym renderze.
 */
function readStored<T>(key: string, fallback: T, parse: (raw: string) => T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : parse(raw);
  } catch {
    return fallback;
  }
}

export function FiltersProvider({ children }: { children: ReactNode }) {
  const [profileId, setProfileIdRaw] = useState<number | null>(() =>
    readStored<number | null>("adspanel.profile", null, (raw) =>
      raw === "all" ? null : Number(raw),
    ),
  );
  const [days, setDaysRaw] = useState(() =>
    readStored("adspanel.days", 30, (raw) => Number(raw) || 30),
  );
  // Wlasny zakres trzymamy osobno od presetu: pusty oznacza, ze obowiazuje
  // preset. Dzieki temu powrot do "30 dni" nie wymaga pamietania, co bylo
  // wpisane wczesniej.
  const [custom, setCustom] = useState<{ start: string; end: string } | null>(() =>
    readStored<{ start: string; end: string } | null>(
      "adspanel.range",
      null,
      (raw) => {
        const v = JSON.parse(raw);
        return v && v.start && v.end ? v : null;
      },
    ),
  );
  const [revision, setRevision] = useState(0);

  const setProfileId = useCallback((id: number | null) => {
    setProfileIdRaw(id);
    localStorage.setItem("adspanel.profile", id === null ? "all" : String(id));
  }, []);

  const setDays = useCallback((d: number) => {
    setDaysRaw(d);
    setCustom(null);
    localStorage.setItem("adspanel.days", String(d));
    localStorage.removeItem("adspanel.range");
  }, []);

  const setRange = useCallback((s: string, e: string) => {
    // Odwrocone daty poprawiamy zamiast odrzucac - to oczywista pomylka,
    // a nie decyzja, ktora warto respektowac.
    const [od, doo] = s <= e ? [s, e] : [e, s];
    const zakres = { start: od, end: doo };
    setCustom(zakres);
    localStorage.setItem("adspanel.range", JSON.stringify(zakres));
  }, []);

  const profilesQuery = useQuery({
    queryKey: ["profiles", revision],
    queryFn: () => api.profiles(),
    staleTime: 5 * 60_000,
    retry: 1,
  });

  const profiles = useMemo(() => profilesQuery.data ?? [], [profilesQuery.data]);

  const { start, end } = useMemo(() => {
    if (custom) return custom;
    const e = new Date();
    e.setDate(e.getDate() - 1);
    const s = new Date(e);
    s.setDate(s.getDate() - (days - 1));
    return { start: isoDate(s), end: isoDate(e) };
  }, [days, custom]);

  const currency = useMemo(() => {
    // Widok zbiorczy sumuje rynki po przeliczeniu na EUR (kurs z dnia, EBC),
    // wiec symbol tez ma byc EUR - nie waluta pierwszego profilu z brzegu.
    if (profileId === null) return "EUR";
    return profiles.find((p) => p.profile_id === profileId)?.currency_code ?? "EUR";
  }, [profileId, profiles]);

  const bump = useCallback(() => setRevision((r) => r + 1), []);

  const value = useMemo<FilterState>(
    () => ({
      profileId,
      setProfileId,
      days,
      setDays,
      start,
      end,
      setRange,
      customRange: custom !== null,
      profiles,
      currency,
      revision,
      bump,
      loadingProfiles: profilesQuery.isPending,
      apiError: profilesQuery.error ? (profilesQuery.error as Error).message : null,
    }),
    [
      profileId,
      setProfileId,
      days,
      setDays,
      start,
      end,
      setRange,
      custom,
      profiles,
      currency,
      revision,
      bump,
      profilesQuery.isPending,
      profilesQuery.error,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useFilters(): FilterState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useFilters musi byc uzyte wewnatrz FiltersProvider");
  return ctx;
}

/** Parametry zakresu gotowe do przekazania do api.*  */
export function useRangeParams() {
  const { profileId, start, end } = useFilters();
  return useMemo(
    () => ({ profile_id: profileId ?? undefined, start, end }),
    [profileId, start, end],
  );
}
