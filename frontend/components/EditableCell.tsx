"use client";

import clsx from "clsx";
import { Undo2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/**
 * Komorka edytowalna w miejscu.
 *
 * Zmiana NIE idzie od razu do Amazona - laduje w lokalnej mapie zmian strony.
 * Dopiero przycisk "Podgląd i zatwierdź" buduje z nich jeden plan. Inaczej
 * poprawienie dziesieciu stawek tworzyloby dziesiec osobnych planow do
 * klikania, a zasada "kazda zmiana wymaga zatwierdzenia" zamienilaby sie
 * w udrege zamiast zabezpieczenia.
 */
export function EditableNumber({
  value,
  draft,
  onChange,
  onReset,
  format,
  min = 0.02,
  max = 100000,
  disabled,
  disabledHint,
}: {
  value: number | null;
  draft: number | undefined;
  onChange: (next: number) => void;
  onReset: () => void;
  format: (v: number | null) => string;
  min?: number;
  max?: number;
  disabled?: boolean;
  disabledHint?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const current = draft ?? value;
  const changed = draft !== undefined && draft !== value;

  function start() {
    if (disabled) return;
    setText(String(current ?? ""));
    setError(false);
    setEditing(true);
  }

  function commit() {
    const parsed = Number(text.replace(",", "."));
    if (!Number.isFinite(parsed) || parsed < min || parsed > max) {
      setError(true);
      return;
    }
    setEditing(false);
    if (parsed !== value) onChange(Number(parsed.toFixed(2)));
    else onReset();
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={text}
        inputMode="decimal"
        autoFocus
        onChange={(e) => {
          setText(e.target.value);
          setError(false);
        }}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setEditing(false);
        }}
        className={clsx(
          "tnum w-20 rounded border bg-bg px-1.5 py-0.5 text-right text-sm outline-none",
          error ? "border-bad" : "border-accent",
        )}
      />
    );
  }

  return (
    <span className="inline-flex items-center gap-1">
      {changed && (
        <button
          onClick={onReset}
          title="Cofnij zmianę"
          className="text-muted hover:text-text"
        >
          <Undo2 className="size-3" />
        </button>
      )}
      <button
        onClick={start}
        disabled={disabled}
        title={disabled ? disabledHint : "Kliknij, żeby zmienić"}
        className={clsx(
          "tnum rounded px-1 py-0.5 text-right",
          disabled
            ? "cursor-default text-muted"
            : "cursor-text hover:bg-surface-2 hover:ring-1 hover:ring-border",
          changed && "bg-warn-soft font-medium text-warn",
        )}
      >
        {format(current)}
        {changed && value !== null && (
          <span className="ml-1 text-[10px] text-muted line-through">{format(value)}</span>
        )}
      </button>
    </span>
  );
}

/** Pasek podsumowujacy niezapisane zmiany. */
export function DraftBar({
  count,
  onDiscard,
  onSubmit,
  busy,
  label,
}: {
  count: number;
  onDiscard: () => void;
  onSubmit: () => void;
  busy?: boolean;
  label: string;
}) {
  if (count === 0) return null;
  return (
    <div className="sticky bottom-4 z-30 mt-4">
      <div className="mx-auto flex max-w-2xl flex-wrap items-center gap-3 rounded-xl border border-warn/40 bg-warn-soft p-3 shadow-lg">
        <span className="text-sm font-medium text-warn">
          {count} {label} — jeszcze niewysłane
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={onDiscard}
            disabled={busy}
            className="rounded-lg px-2.5 py-1 text-xs text-muted hover:text-text"
          >
            Odrzuć zmiany
          </button>
          <button
            onClick={onSubmit}
            disabled={busy}
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            Podgląd i zatwierdź
          </button>
        </div>
      </div>
    </div>
  );
}
