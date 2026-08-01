"use client";

/** Owner dashboard (BR-AN-01). Every tile drills through (BR-AN-04). */
import Link from "next/link";
import { useEffect, useState } from "react";

import {
  Card,
  ErrorState,
  Money,
  Skeleton,
  Stat,
  Table,
} from "../components/ui";
import { api } from "../lib/api";
import { formatINR } from "../lib/money";

type Dashboard = {
  as_of: string;
  sales: {
    mtd_paise: number;
    mtd_orders: number;
    last_month_paise: number;
    same_month_last_year_paise: number;
    mom_change_pct: number | null;
    yoy_change_pct: number | null;
    qtd_paise: number;
    ytd_paise: number;
  };
  money: {
    outstanding_paise: number;
    overdue_paise: number;
    blocked_revenue_paise: number;
    collected_mtd_paise: number;
    collection_efficiency_pct: number | null;
    dso_days: number | null;
  };
  ageing: Record<string, number>;
  customers: Record<string, number>;
};

type TopCustomer = { customer: string; orders: number; value_paise: number };

function Delta({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-slate-400">—</span>;
  const positive = pct >= 0;
  return (
    <span className={positive ? "text-emerald-700" : "text-red-700"}>
      {positive ? "▲" : "▼"} {Math.abs(pct)}%
    </span>
  );
}

export default function AdminDashboard() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [top, setTop] = useState<TopCustomer[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setData(await api.get<Dashboard>("/analytics/dashboard"));
        const leaders = await api.get<{ items: TopCustomer[] }>(
          "/analytics/top-customers?limit=8",
        );
        setTop(leaders.items);
      } catch {
        setError("Could not load the dashboard");
      }
    })();
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!data) return <Skeleton rows={6} />;

  const ageingRows = [
    ["Current", data.ageing.current ?? 0],
    ["1–30 days", data.ageing["1-30"] ?? 0],
    ["31–60 days", data.ageing["31-60"] ?? 0],
    ["61–90 days", data.ageing["61-90"] ?? 0],
    ["90+ days", data.ageing["90+"] ?? 0],
  ] as const;
  const ageingTotal = ageingRows.reduce((sum, [, value]) => sum + value, 0);

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold">Business today</h1>
        <span className="text-xs text-slate-500">as of {data.as_of}</span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Sales this month"
          value={formatINR(data.sales.mtd_paise, { compact: true })}
          hint={
            <>
              {data.sales.mtd_orders} orders · vs last month{" "}
              <Delta pct={data.sales.mom_change_pct} /> · vs last year{" "}
              <Delta pct={data.sales.yoy_change_pct} />
            </>
          }
        />
        <Stat
          label="Outstanding"
          value={formatINR(data.money.outstanding_paise, { compact: true })}
          hint="Money owed to Liger right now"
          tone={data.money.outstanding_paise > 0 ? "warn" : "neutral"}
        />
        <Stat
          label="Overdue"
          value={formatINR(data.money.overdue_paise, { compact: true })}
          hint="Past its due date"
          tone={data.money.overdue_paise > 0 ? "bad" : "good"}
        />
        <Stat
          label="Frozen behind blocks"
          value={formatINR(data.money.blocked_revenue_paise, { compact: true })}
          hint={`${data.customers.blocked ?? 0} blocked dealers`}
          tone={data.money.blocked_revenue_paise > 0 ? "bad" : "good"}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Ageing">
          <ul className="space-y-2 text-sm">
            {ageingRows.map(([label, value]) => {
              const share = ageingTotal ? (value / ageingTotal) * 100 : 0;
              const bad = label !== "Current";
              return (
                <li key={label}>
                  <div className="flex justify-between">
                    <span className="text-slate-600">{label}</span>
                    <Money paise={value} className="font-medium" />
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded bg-slate-100">
                    <div
                      className={`h-full ${bad ? "bg-red-400" : "bg-emerald-400"}`}
                      style={{ width: `${share}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
          <Link
            href="/admin/credit"
            className="mt-4 block text-xs text-slate-500 hover:underline"
          >
            Open the credit centre →
          </Link>
        </Card>

        <Card title="Collections">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-600">Collected this month</dt>
              <dd>
                <Money paise={data.money.collected_mtd_paise} />
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-600">Collection efficiency</dt>
              <dd className="font-medium">
                {data.money.collection_efficiency_pct === null
                  ? "—"
                  : `${data.money.collection_efficiency_pct}%`}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-600">Average days to pay</dt>
              <dd className="font-medium">
                {data.money.dso_days === null ? "—" : `${data.money.dso_days} days`}
              </dd>
            </div>
          </dl>
          <Link
            href="/admin/cash"
            className="mt-4 block text-xs text-slate-500 hover:underline"
          >
            Cash awaiting confirmation →
          </Link>
        </Card>

        <Card title="Dealers">
          <dl className="space-y-2 text-sm">
            {[
              ["Good standing", "green", "text-emerald-700"],
              ["Watch", "amber", "text-amber-700"],
              ["At risk", "red", "text-red-700"],
              ["Blocked", "blocked", "text-slate-900"],
            ].map(([label, key, tone]) => (
              <div key={key} className="flex justify-between">
                <dt className="text-slate-600">{label}</dt>
                <dd className={`font-semibold ${tone}`}>
                  {data.customers[key] ?? 0}
                </dd>
              </div>
            ))}
            <div className="flex justify-between border-t border-slate-200 pt-2">
              <dt className="text-slate-600">Total</dt>
              <dd className="font-semibold">{data.customers.total ?? 0}</dd>
            </div>
          </dl>
        </Card>
      </div>

      <Card title="Top customers this month">
        {top === null ? (
          <Skeleton />
        ) : (
          <Table head={["Customer", "Orders", "Value"]} empty={top.length === 0}>
            {top.map((row) => (
              <tr key={row.customer}>
                <td className="px-3 py-2 font-medium">{row.customer}</td>
                <td className="px-3 py-2">{row.orders}</td>
                <td className="px-3 py-2">
                  <Money paise={row.value_paise} />
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
