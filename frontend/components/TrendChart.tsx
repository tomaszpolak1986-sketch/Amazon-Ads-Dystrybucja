"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TimePoint } from "@/lib/api";
import { money, pct, shortDate } from "@/lib/format";

const AXIS = { stroke: "var(--text-muted)", fontSize: 11 };

interface Props {
  data: TimePoint[];
  currency: string;
}

/**
 * Wydatek i sprzedaz na jednej osi, ACOS na drugiej.
 * ACOS jako linia, bo to wskaznik pochodny - nie sumuje sie i nie ma sensu
 * pokazywac go jako powierzchni.
 */
export function TrendChart({ data, currency }: Props) {
  return (
    <div className="h-72 w-full px-2 py-3">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="salesFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.28} />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={shortDate}
            tickLine={false}
            axisLine={false}
            minTickGap={24}
            {...AXIS}
          />
          <YAxis
            yAxisId="money"
            tickLine={false}
            axisLine={false}
            width={54}
            tickFormatter={(v: number) => money(v, currency)}
            {...AXIS}
          />
          <YAxis
            yAxisId="acos"
            orientation="right"
            tickLine={false}
            axisLine={false}
            width={44}
            tickFormatter={(v: number) => `${Math.round(v)}%`}
            {...AXIS}
          />
          <Tooltip
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              fontSize: 12,
              color: "var(--text)",
            }}
            labelFormatter={(label) => (typeof label === "string" ? shortDate(label) : label)}
            formatter={(value, name) => {
              const v = typeof value === "number" ? value : Number(value);
              return name === "ACOS" ? pct(v) : money(v, currency);
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: "var(--text-muted)" }}
            iconType="plainline"
          />
          <Area
            yAxisId="money"
            type="monotone"
            dataKey="sales"
            name="Sprzedaż"
            stroke="var(--accent)"
            strokeWidth={2}
            fill="url(#salesFill)"
          />
          <Line
            yAxisId="money"
            type="monotone"
            dataKey="cost"
            name="Wydatek"
            stroke="var(--warn)"
            strokeWidth={2}
            dot={false}
          />
          <Line
            yAxisId="acos"
            type="monotone"
            dataKey="acos"
            name="ACOS"
            stroke="var(--bad)"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            dot={false}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
