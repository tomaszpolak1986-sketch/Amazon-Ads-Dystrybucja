"use client";

import clsx from "clsx";
import type { ReactNode } from "react";
import { deltaLabel, deltaTone } from "@/lib/format";

export function Card({
  children,
  className,
  title,
  subtitle,
  action,
}: {
  children: ReactNode;
  className?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section
      className={clsx(
        "rounded-xl border border-border bg-surface shadow-[0_1px_2px_rgba(0,0,0,0.04)]",
        className,
      )}
    >
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 border-b border-border px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-text">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function KpiCard({
  label,
  value,
  delta,
  lowerIsBetter = false,
  hint,
}: {
  label: string;
  value: string;
  delta?: number | null;
  lowerIsBetter?: boolean;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">{label}</span>
        {delta !== undefined && (
          <span className={clsx("tnum text-xs font-semibold", deltaTone(delta, lowerIsBetter))}>
            {deltaLabel(delta)}
          </span>
        )}
      </div>
      <div className="tnum mt-1.5 text-2xl font-semibold text-text">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad" | "accent";
}) {
  const tones = {
    neutral: "bg-surface-2 text-muted",
    good: "bg-good-soft text-good",
    warn: "bg-warn-soft text-warn",
    bad: "bg-bad-soft text-bad",
    accent: "bg-accent-soft text-accent",
  } as const;
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  size = "md",
  disabled,
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  size?: "sm" | "md";
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  const variants = {
    default: "border border-border bg-surface hover:bg-surface-2 text-text",
    primary: "bg-accent text-white hover:opacity-90 border border-transparent",
    danger: "bg-bad text-white hover:opacity-90 border border-transparent",
    ghost: "text-muted hover:text-text hover:bg-surface-2 border border-transparent",
  } as const;
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm",
        variants[variant],
      )}
    >
      {children}
    </button>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-6 py-14 text-center">
      <p className="text-sm font-medium text-text">{title}</p>
      {hint && <p className="max-w-md text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  const offline = /fetch|network|failed/i.test(message);
  return (
    <div className="m-4 rounded-lg border border-bad/30 bg-bad-soft px-4 py-3">
      <p className="text-sm font-medium text-bad">
        {offline ? "Brak połączenia z backendem" : "Błąd API"}
      </p>
      <p className="mt-1 font-mono text-xs break-all text-muted">{message}</p>
      {offline && (
        <p className="mt-2 text-xs text-muted">
          Uruchom backend:{" "}
          <code className="rounded bg-surface-2 px-1 py-0.5">
            .venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --port 8000
          </code>
        </p>
      )}
    </div>
  );
}

export function Skeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-4">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-8 animate-pulse rounded bg-surface-2" />
      ))}
    </div>
  );
}
