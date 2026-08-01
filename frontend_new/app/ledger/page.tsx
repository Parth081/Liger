"use client";

/** Dealer statement — reconstructed purely from ledger entries (BR-LED-03). */
import { useEffect, useState } from "react";

import {
  Card,
  EmptyState,
  ErrorState,
  Money,
  Skeleton,
  Table,
} from "../components/ui";
import { api } from "../lib/api";

type Entry = {
  entry_type: string;
  debit_paise: number;
  credit_paise: number;
  balance_after_paise: number;
  narration: string | null;
  posted_at: string;
};

export default function LedgerPage() {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const list = await api.get<{ items: { customer_uid: string }[] }>(
          "/credit/ageing?limit=1",
        );
        const uid = list.items[0]?.customer_uid;
        if (!uid) {
          setEntries([]);
          return;
        }
        const result = await api.get<{ items: Entry[] }>(
          `/customers/${uid}/ledger?limit=100`,
        );
        setEntries(result.items);
      } catch {
        setError("Could not load your statement");
      }
    })();
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Statement</h1>
      {error ? (
        <ErrorState message={error} />
      ) : entries === null ? (
        <Skeleton rows={6} />
      ) : entries.length === 0 ? (
        <Card>
          <EmptyState title="No entries yet" />
        </Card>
      ) : (
        <Card>
          <Table head={["Date", "Particulars", "Debit", "Credit", "Balance"]}>
            {entries.map((entry, i) => (
              <tr key={i}>
                <td className="px-3 py-2 text-slate-600">
                  {new Date(entry.posted_at).toLocaleDateString()}
                </td>
                <td className="px-3 py-2">
                  <div className="capitalize">
                    {entry.entry_type.replaceAll("_", " ")}
                  </div>
                  {entry.narration && (
                    <div className="text-xs text-slate-500">{entry.narration}</div>
                  )}
                </td>
                <td className="px-3 py-2">
                  {entry.debit_paise ? <Money paise={entry.debit_paise} /> : "—"}
                </td>
                <td className="px-3 py-2">
                  {entry.credit_paise ? <Money paise={entry.credit_paise} /> : "—"}
                </td>
                <td className="px-3 py-2 font-medium">
                  <Money paise={entry.balance_after_paise} />
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
    </div>
  );
}
