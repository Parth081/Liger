"use client";

/** Dealer home — credit status, dues, quick actions. */
import Link from "next/link";
import { useEffect, useState } from "react";

import { CreditStrip, type CreditDecision } from "./components/CreditStrip";
import {
  Card,
  EmptyState,
  ErrorState,
  Money,
  Skeleton,
  StatusPill,
  Table,
} from "./components/ui";
import { api } from "./lib/api";

type Status = {
  customer_uid: string;
  colour: string;
  outstanding_paise: number;
  exposure_paise: number;
  effective_limit_paise: number;
  available_paise: number;
  overdue_invoices: CreditDecision["overdue_invoices"];
};

type OrderRow = {
  uid: string;
  order_no: string;
  status: string;
  order_date: string;
  grand_total_paise: number;
};

export default function DealerHome() {
  const [status, setStatus] = useState<Status | null>(null);
  const [orders, setOrders] = useState<OrderRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const recent = await api.get<{ items: OrderRow[] }>("/orders?limit=5");
        setOrders(recent.items);
      } catch {
        setError("Could not load your orders");
        return;
      }
      try {
        const list = await api.get<{ items: Status[] }>("/credit/ageing?limit=1");
        if (list.items[0]) setStatus(list.items[0]);
      } catch {
        /* the credit summary is optional on this screen */
      }
    })();
  }, []);

  if (error) return <ErrorState message={error} />;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Your account</h1>

      {status && (
        <CreditStrip
          credit={{
            decision: status.colour === "blocked" ? "BLOCK" : "ALLOW",
            reasons: [],
            effective_limit_paise: status.effective_limit_paise,
            exposure_paise: status.exposure_paise,
            available_paise: status.available_paise,
            outstanding_paise: status.outstanding_paise,
            overdue_invoices: status.overdue_invoices ?? [],
            suggested_payment_paise: 0,
          }}
        />
      )}

      <div className="grid gap-4 sm:grid-cols-3">
        <Link
          href="/order"
          className="rounded-xl bg-slate-900 p-5 text-white transition hover:bg-slate-800"
        >
          <p className="text-lg font-semibold">Place a new order</p>
          <p className="mt-1 text-sm text-slate-300">
            Design number, size, quantity — the total updates as you type.
          </p>
        </Link>
        <Link
          href="/pay"
          className="rounded-xl border border-slate-200 bg-white p-5 transition hover:border-slate-300"
        >
          <p className="text-lg font-semibold">Pay dues</p>
          <p className="mt-1 text-sm text-slate-500">
            UPI, card or net banking. Credit frees up immediately.
          </p>
        </Link>
        <Link
          href="/ledger"
          className="rounded-xl border border-slate-200 bg-white p-5 transition hover:border-slate-300"
        >
          <p className="text-lg font-semibold">Statement</p>
          <p className="mt-1 text-sm text-slate-500">
            Every invoice and payment, in order.
          </p>
        </Link>
      </div>

      <Card
        title="Recent orders"
        action={
          <Link href="/orders" className="text-xs text-slate-500 hover:underline">
            View all
          </Link>
        }
      >
        {orders === null ? (
          <Skeleton />
        ) : orders.length === 0 ? (
          <EmptyState
            title="No orders yet"
            hint="Your first order will appear here."
          />
        ) : (
          <Table head={["Order", "Date", "Status", "Amount"]}>
            {orders.map((order) => (
              <tr key={order.uid}>
                <td className="px-3 py-2 font-medium">{order.order_no}</td>
                <td className="px-3 py-2 text-slate-600">{order.order_date}</td>
                <td className="px-3 py-2">
                  <StatusPill status={order.status} />
                </td>
                <td className="px-3 py-2">
                  <Money paise={order.grand_total_paise} />
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
