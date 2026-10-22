"use client";

/**
 * Generyczna tabela na TanStack Table v9.
 *
 * Sortowanie zostawiamy bibliotece, natomiast zaznaczanie wierszy obslugujemy
 * sami (Set identyfikatorow przekazywany z gory). Dzieki temu pasek akcji
 * masowych ma bezposredni dostep do zaznaczenia bez przechodzenia przez
 * generyki feature'ow, a kolumna z checkboxem nie musi byc czescia modelu kolumn.
 */

import {
  createSortedRowModel,
  rowSortingFeature,
  tableFeatures,
  useTable,
  type SortingState,
} from "@tanstack/react-table";
import clsx from "clsx";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState } from "./ui";

export const tableFeaturesSet = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
});

/* TanStack definiuje RowData jako Record<string, any> | Array<any>, wiec
   ograniczenie generyka musi uzywac `any` - inaczej zwykle interfejsy
   (bez indeksu sygnaturowego) nie przechodza. */
/* eslint-disable @typescript-eslint/no-explicit-any */

export interface ColumnMeta {
  align?: "left" | "right";
  width?: string;
}

interface DataTableProps<T extends Record<string, any>> {
  data: T[];
  columns: any[];
  getRowId: (row: T) => string;
  initialSort?: SortingState;
  selected?: Set<string>;
  onSelectedChange?: (next: Set<string>) => void;
  emptyLabel?: string;
  emptyHint?: string;
  maxHeight?: string;
  /** Klikniecie wiersza. Klikniecie w checkbox lub link nie wywoluje akcji. */
  onRowClick?: (row: T) => void;
}

export function DataTable<T extends Record<string, any>>({
  data,
  columns,
  getRowId,
  initialSort,
  selected,
  onSelectedChange,
  emptyLabel = "Brak danych",
  emptyHint,
  maxHeight = "calc(100vh - 20rem)",
  onRowClick,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSort ?? []);
  const selectable = Boolean(selected && onSelectedChange);

  const table = useTable({
    features: tableFeaturesSet,
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
  });

  const rows = table.getRowModel().rows;

  const allIds = useMemo(() => data.map(getRowId), [data, getRowId]);
  const allSelected = selectable && allIds.length > 0 && allIds.every((id) => selected!.has(id));
  const someSelected = selectable && allIds.some((id) => selected!.has(id));

  function toggleAll() {
    if (!onSelectedChange) return;
    onSelectedChange(allSelected ? new Set() : new Set(allIds));
  }

  function toggleOne(id: string) {
    if (!onSelectedChange || !selected) return;
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSelectedChange(next);
  }

  if (data.length === 0) {
    return <EmptyState title={emptyLabel} hint={emptyHint} />;
  }

  return (
    <div className="overflow-auto" style={{ maxHeight }}>
      <table className="w-full border-collapse text-sm">
        <thead className="sticky top-0 z-10 bg-surface-2">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id} className="border-b border-border">
              {selectable && (
                <th className="w-9 px-3 py-2">
                  <input
                    type="checkbox"
                    aria-label="Zaznacz wszystkie"
                    className="size-3.5 accent-[var(--accent)]"
                    checked={allSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = !allSelected && someSelected;
                    }}
                    onChange={toggleAll}
                  />
                </th>
              )}
              {group.headers.map((header) => {
                const meta = (header.column.columnDef.meta ?? {}) as ColumnMeta;
                const sortDir = header.column.getIsSorted();
                const canSort = header.column.getCanSort();
                return (
                  <th
                    key={header.id}
                    style={meta.width ? { width: meta.width } : undefined}
                    className={clsx(
                      "px-3 py-2 text-xs font-semibold whitespace-nowrap text-muted",
                      meta.align === "right" ? "text-right" : "text-left",
                    )}
                  >
                    {header.isPlaceholder ? null : (
                      <button
                        type="button"
                        disabled={!canSort}
                        onClick={header.column.getToggleSortingHandler()}
                        className={clsx(
                          "inline-flex items-center gap-1",
                          meta.align === "right" && "flex-row-reverse",
                          canSort && "cursor-pointer hover:text-text",
                        )}
                      >
                        <table.FlexRender header={header} />
                        {canSort &&
                          (sortDir === "asc" ? (
                            <ArrowUp className="size-3" />
                          ) : sortDir === "desc" ? (
                            <ArrowDown className="size-3" />
                          ) : (
                            <ChevronsUpDown className="size-3 opacity-40" />
                          ))}
                      </button>
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {rows.map((row) => {
            const id = getRowId(row.original as T);
            const isSelected = selectable && selected!.has(id);
            return (
              <tr
                key={row.id}
                onClick={
                  onRowClick
                    ? (e) => {
                        // Zaznaczanie wierszy nie moze wywolywac nawigacji
                        if ((e.target as HTMLElement).closest("input,button,a")) return;
                        onRowClick(row.original as T);
                      }
                    : undefined
                }
                className={clsx(
                  "border-b border-border/60 transition-colors",
                  isSelected ? "bg-accent-soft" : "hover:bg-surface-2",
                  onRowClick && "cursor-pointer",
                )}
              >
                {selectable && (
                  <td className="px-3 py-1.5">
                    <input
                      type="checkbox"
                      aria-label="Zaznacz wiersz"
                      className="size-3.5 accent-[var(--accent)]"
                      checked={isSelected}
                      onChange={() => toggleOne(id)}
                    />
                  </td>
                )}
                {row.getAllCells().map((cell) => {
                  const meta = (cell.column.columnDef.meta ?? {}) as ColumnMeta;
                  return (
                    <td
                      key={cell.id}
                      className={clsx(
                        "px-3 py-1.5",
                        meta.align === "right" ? "tnum text-right" : "text-left",
                      )}
                    >
                      <table.FlexRender cell={cell} />
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
