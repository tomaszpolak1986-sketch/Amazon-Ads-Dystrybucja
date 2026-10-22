"use client";

import clsx from "clsx";
import {
  BarChart3,
  CalendarRange,
  Globe,
  History,
  LayoutDashboard,
  Layers,
  Ban,
  MapPin,
  Lightbulb,
  LogOut,
  Sparkles,
  ShieldCheck,
  Workflow,
  Moon,
  Package,
  RefreshCw,
  Search,
  Sun,
  PlusCircle,
  Target,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RANGE_PRESETS, useFilters } from "@/lib/filters";
import { Button } from "./ui";

const NAV = [
  { href: "/", label: "Przegląd", icon: LayoutDashboard },
  { href: "/portfolios", label: "Portfolia", icon: Layers },
  { href: "/campaigns", label: "Kampanie", icon: BarChart3 },
  { href: "/campaigns/new", label: "Nowa kampania", icon: PlusCircle },
  { href: "/targets", label: "Targetowanie", icon: Target },
  { href: "/search-terms", label: "Frazy wyszukiwania", icon: Search },
  { href: "/placements", label: "Placementy", icon: MapPin },
  { href: "/negatives", label: "Wykluczenia", icon: Ban },
  { href: "/opportunities", label: "Analiza", icon: Lightbulb },
  { href: "/rules", label: "Reguły", icon: Workflow },
  { href: "/approvals", label: "Do zatwierdzenia", icon: ShieldCheck },
  { href: "/products", label: "Produkty", icon: Package },
  { href: "/total-sales", label: "Sprzedaż całkowita", icon: Wallet },
  { href: "/changes", label: "Historia zmian", icon: History },
  { href: "/suggestions", label: "Sugestie", icon: Sparkles },
] as const;

function ThemeToggle() {
  // Motyw zyje wylacznie w atrybucie data-theme (ustawianym przez skrypt
  // w layoucie jeszcze przed pierwszym malowaniem) i w localStorage.
  // Ikone przelacza CSS, wiec komponent nie potrzebuje zadnego stanu.
  function toggle() {
    const root = document.documentElement;
    const isDark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = isDark ? "light" : "dark";
    root.dataset.theme = next;
    localStorage.setItem("adspanel.theme", next);
  }

  return (
    <Button variant="ghost" size="sm" onClick={toggle} title="Przełącz motyw">
      <Sun className="icon-dark size-4" />
      <Moon className="icon-light size-4" />
    </Button>
  );
}

function LogoutButton() {
  const queryClient = useQueryClient();
  return (
    <Button
      variant="ghost"
      size="sm"
      title="Wyloguj"
      onClick={async () => {
        await api.logout();
        queryClient.clear();
      }}
    >
      <LogOut className="size-4" />
    </Button>
  );
}


function SyncButton() {
  const { bump } = useFilters();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // Backend przyjmuje jedna synchronizacje naraz. Pytamy co 15 s, czy jakas
  // trwa - wtedy przycisk jest wyszarzony, zamiast odpowiadac "juz trwa"
  // dopiero po klikniecu. Po jej zakonczeniu odswiezamy dane na ekranie.
  const status = useQuery({
    queryKey: ["sync-status"],
    queryFn: api.syncStatus,
    refetchInterval: 15_000,
  });
  const running = status.data?.running ?? false;
  const [wasRunning, setWasRunning] = useState(false);
  if (running !== wasRunning) {
    setWasRunning(running);
    if (!running && wasRunning) bump();
  }

  async function run() {
    setBusy(true);
    setMsg(null);
    try {
      await api.syncAll(14);
      setMsg("Sync uruchomiony w tle");
      status.refetch();
      setTimeout(() => setMsg(null), 4000);
    } catch (err) {
      const e = err as { status?: number; message: string };
      // 409 = inna synchronizacja wlasnie trwa; to informacja, nie awaria
      setMsg(e.status === 409 ? "Synchronizacja już trwa" : e.message.slice(0, 80));
      status.refetch();
    } finally {
      setBusy(false);
    }
  }

  const title = running
    ? `Trwa: ${status.data?.what ?? "synchronizacja"}`
    : "Pobierz świeże dane z Amazona";

  return (
    <div className="flex items-center gap-2">
      {msg && <span className="text-xs text-muted">{msg}</span>}
      <Button size="sm" onClick={run} disabled={busy || running} title={title}>
        <RefreshCw className={clsx("size-3.5", (busy || running) && "animate-spin")} />
        {running ? "Trwa sync…" : "Sync"}
      </Button>
    </div>
  );
}

function MarketPicker() {
  const { profiles, profileId, setProfileId, loadingProfiles } = useFilters();

  if (loadingProfiles) {
    return <div className="h-7 w-40 animate-pulse rounded-lg bg-surface-2" />;
  }

  return (
    <div className="flex items-center gap-1 rounded-lg border border-border bg-surface p-0.5">
      <button
        onClick={() => setProfileId(null)}
        className={clsx(
          "flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
          profileId === null ? "bg-accent text-white" : "text-muted hover:text-text",
        )}
      >
        <Globe className="size-3" />
        Wszystkie
      </button>
      {profiles.map((p) => (
        <button
          key={p.profile_id}
          onClick={() => setProfileId(p.profile_id)}
          title={p.account_name ?? undefined}
          className={clsx(
            "rounded-md px-2 py-1 text-xs font-medium transition-colors",
            profileId === p.profile_id ? "bg-accent text-white" : "text-muted hover:text-text",
          )}
        >
          {p.country_code ?? p.profile_id}
        </button>
      ))}
    </div>
  );
}

function RangePicker() {
  const { days, setDays, start, end, setRange, customRange } = useFilters();
  const [otwarty, setOtwarty] = useState(false);
  const [od, setOd] = useState(start);
  const [doo, setDoo] = useState(end);

  // Przy otwarciu pokazujemy aktualnie obowiazujacy zakres, zeby dalo sie go
  // poprawic o kilka dni zamiast wpisywac od zera.
  function otworz() {
    setOd(start);
    setDoo(end);
    setOtwarty(true);
  }

  function zastosuj() {
    if (od && doo) setRange(od, doo);
    setOtwarty(false);
  }

  // Dane siegaja wczoraj - pozniejsza data nic nie pokaze, wiec ja blokujemy.
  const wczoraj = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10);
  }, []);

  return (
    <div className="relative flex items-center gap-2">
      <div className="flex items-center gap-0.5 rounded-lg border border-border bg-surface p-0.5">
        {RANGE_PRESETS.map((preset) => (
          <button
            key={preset.key}
            onClick={() => setDays(preset.days)}
            className={clsx(
              "rounded-md px-2 py-1 text-xs font-medium transition-colors",
              !customRange && days === preset.days
                ? "bg-accent text-white"
                : "text-muted hover:text-text",
            )}
          >
            {preset.label}
          </button>
        ))}
        <button
          onClick={otworz}
          title="Wybierz własny zakres dat"
          className={clsx(
            "flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
            customRange ? "bg-accent text-white" : "text-muted hover:text-text",
          )}
        >
          <CalendarRange className="size-3.5" />
          Zakres
        </button>
      </div>

      <button
        onClick={otworz}
        className="tnum hidden text-xs text-muted transition-colors hover:text-accent lg:inline"
        title="Kliknij, żeby zmienić zakres"
      >
        {start} → {end}
      </button>

      {otwarty && (
        <>
          {/* Kliknięcie obok zamyka bez zapisywania */}
          <button
            className="fixed inset-0 z-40 cursor-default"
            onClick={() => setOtwarty(false)}
            aria-label="Zamknij wybór zakresu"
          />
          <div className="absolute right-0 top-full z-50 mt-2 w-72 rounded-xl border border-border bg-surface p-3 shadow-lg">
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="text-[11px] font-medium text-muted">Od</span>
                <input
                  type="date"
                  value={od}
                  max={doo || wczoraj}
                  onChange={(e) => setOd(e.target.value)}
                  className="mt-0.5 w-full rounded-lg border border-border bg-bg px-2 py-1 text-xs outline-none focus:border-accent"
                />
              </label>
              <label className="block">
                <span className="text-[11px] font-medium text-muted">Do</span>
                <input
                  type="date"
                  value={doo}
                  min={od}
                  max={wczoraj}
                  onChange={(e) => setDoo(e.target.value)}
                  className="mt-0.5 w-full rounded-lg border border-border bg-bg px-2 py-1 text-xs outline-none focus:border-accent"
                />
              </label>
            </div>

            <p className="mt-2 text-[11px] text-muted">
              Dane sięgają wczoraj. Frazy wyszukiwania — około 65 dni wstecz,
              reszta do 95 dni.
            </p>

            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => setOtwarty(false)}
                className="rounded-lg px-2.5 py-1 text-xs text-muted hover:text-text"
              >
                Anuluj
              </button>
              <button
                onClick={zastosuj}
                disabled={!od || !doo}
                className="rounded-lg bg-accent px-2.5 py-1 text-xs font-medium text-white disabled:opacity-40"
              >
                Zastosuj
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// Ile dni przed koncem licencji panel zaczyna o tym przypominac. Po wygasnieciu
// blokuje sie od razu, wiec klient ma miec czas, zeby zalatwic przedluzenie.
const LICENCJA_OSTRZEZENIE_DNI = 14;

function WersjaPanelu() {
  const wersja = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    select: (h) => h.wersja,
  }).data;
  return <>Wersja {wersja ?? "…"}</>;
}

function LicencjaBanner() {
  const licencja = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    refetchInterval: 6 * 60 * 60_000,
    select: (h) => h.licencja,
  }).data;

  if (!licencja?.ok || !licencja.wazna_do) return null;
  // Dni kalendarzowe: 0 = ostatni dzien licencji (wazna do konca dnia wlacznie)
  const dzis = new Date();
  dzis.setHours(0, 0, 0, 0);
  const dni = Math.round((new Date(`${licencja.wazna_do}T00:00:00`).getTime() - dzis.getTime()) / 86_400_000);
  if (dni > LICENCJA_OSTRZEZENIE_DNI) return null;

  return (
    <div className="border-b border-warn/30 bg-warn-soft px-4 py-2 text-xs text-warn">
      Licencja panelu wygasa {licencja.wazna_do}
      {dni <= 0 ? " (dziś)" : dni === 1 ? " (jutro)" : ` (za ${dni} dni)`}. Po tym dniu panel zostanie zablokowany, a dane
      pozostaną nienaruszone. W sprawie przedłużenia skontaktuj się z dostawcą panelu.
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { profileId, revision } = useFilters();

  // Licznik przy "Do zatwierdzenia" - odswiezany co minute, zeby sugestie
  // regul byly widoczne bez przeladowywania panelu.
  const pendingCount = useQuery({
    queryKey: ["pending-plans-count", profileId, revision],
    queryFn: () => api.pendingPlans(profileId),
    refetchInterval: 60_000,
    select: (rows) => rows.filter((r) => !r.expired).length,
  }).data;

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-52 shrink-0 flex-col border-r border-border bg-surface md:flex">
        <div className="flex items-center gap-2 px-4 py-4">
          <div className="flex size-7 items-center justify-center rounded-lg bg-accent text-sm font-bold text-white">
            A
          </div>
          <div>
            <div className="text-sm leading-tight font-semibold">AdsPanel</div>
            <div className="text-[11px] leading-tight text-muted">Amazon Ads</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-2 py-2">
          {NAV.map((item) => {
            const active = pathname === item.href;
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={clsx(
                  "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors",
                  active
                    ? "bg-accent-soft font-medium text-accent"
                    : "text-muted hover:bg-surface-2 hover:text-text",
                )}
              >
                <Icon className="size-4 shrink-0" />
                <span className="flex-1">{item.label}</span>
                {item.href === "/approvals" && !!pendingCount && (
                  <span className="tnum rounded-full bg-warn px-1.5 text-[10px] font-semibold text-white">
                    {pendingCount}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-border px-3 py-3 text-[11px] text-muted">
          <WersjaPanelu />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <LicencjaBanner />
        <header className="sticky top-0 z-20 flex flex-wrap items-center gap-3 border-b border-border bg-bg/85 px-4 py-2.5 backdrop-blur">
          <MarketPicker />
          <RangePicker />
          <div className="ml-auto flex items-center gap-2">
            <SyncButton />
            <ThemeToggle />
            <LogoutButton />
          </div>
        </header>
        <main className="min-w-0 flex-1 p-4">{children}</main>
      </div>
    </div>
  );
}
