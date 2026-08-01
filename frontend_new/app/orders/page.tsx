"use client";

/** Dealer order list + live status tracking. */
import { useEffect, useState } from "react";

import {
  Card,
  EmptyState,
  ErrorState,
  Money,
  Skeleton,
  StatusPill,
  Table,
} from "../components/ui";
import { api } from "../lib/api";

type OrderRow = {
  uid: string;
  order_no: string;
  status: string;
  order_date: string;
  grand_total_paise: number;
};

export default function OrdersPage() {
  const [rows, setRows] = useState<OrderRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const result = await api.get<{ items: OrderRow[] }>("/orders?limit=100");
        setRows(result.items);
      } catch {
        setError("Could not load your orders");
      }
    })();
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">My orders</h1>
      {error ? (
        <ErrorState message={error} />
      ) : rows === null ? (
        <Skeleton rows={5} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState title="No orders yet" />
        </Card>
      ) : (
        <Card>
          <Table head={["Order", "Date", "Status", "Amount"]}>
            {rows.map((order) => (
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
        </Card>
      )}
    </div>
  );
}
