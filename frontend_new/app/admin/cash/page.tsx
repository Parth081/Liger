"use client";

/**
 * The cash queue (P4-T4-04, BR-PAY-05).
 * Confirming here is what actually frees the dealer's credit — so the screen
 * shows the credit impact before the click, not after.
 */
import { useCallback, useEffect, useState } from "react";

import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Money,
  Skeleton,
  Table,
  inputClass,
} from "../../components/ui";
import { ApiException, api } from "../../lib/api";

type Payment = {
  uid: string;
  amount_paise: number;
  method: string;
  status: string;
  reference_no: string | null;
  created_at: string;
};

export default function CashQueue() {
  const [rows, setRows] = useState<Payment[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyUid, setBusyUid] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const [reloadToken, setReloadToken] = useState(0);
  const load = useCallback(() => setReloadToken((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await api.get<{ items: Payment[] }>(
          "/payments?status=pending_confirmation&limit=100",
        );
        if (!cancelled) {
          setRows(result.items);
          setError(null);
        }
      } catch {
        if (!cancelled) setError("Could not load the cash queue");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  async function confirm(uid: string) {
    setBusyUid(uid);
    setMessage(null);
    try {
      await api.post(`/payments/${uid}/confirm`);
      setMessage("Cash confirmed — the dealer's credit has been released.");
      load();
    } catch (e) {
      setMessage(e instanceof ApiException ? e.message : "Could not confirm");
    } finally {
      setBusyUid(null);
    }
  }

  async function reject(uid: string) {
    setBusyUid(uid);
    try {
      await api.post(`/payments/${uid}/reject`, { reason });
      setMessage("Payment rejected. Nothing was posted to the ledger.");
      setRejecting(null);
      setReason("");
      load();
    } catch (e) {
      setMessage(e instanceof ApiException ? e.message : "Could not reject");
    } finally {
      setBusyUid(null);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Cash awaiting confirmation</h1>
        <p className="mt-1 text-sm text-slate-600">
          Cash and cheques do not free any credit until confirmed here. Until you
          confirm, the dealer stays exactly where they were.
        </p>
      </div>

      {message && (
        <p className="rounded-lg bg-slate-100 px-4 py-3 text-sm">{message}</p>
      )}

      {error ? (
        <ErrorState message={error} onRetry={load} />
      ) : rows === null ? (
        <Skeleton rows={4} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState
            title="Nothing waiting"
            hint="Cash recorded by staff will appear here for your confirmation."
          />
        </Card>
      ) : (
        <Card title={`${rows.length} awaiting`}>
          <Table head={["Recorded", "Method", "Reference", "Amount", ""]}>
            {rows.map((payment) => (
              <tr key={payment.uid}>
                <td className="px-3 py-2 text-slate-600">
                  {new Date(payment.created_at).toLocaleString()}
                </td>
                <td className="px-3 py-2 capitalize">
                  {payment.method.replaceAll("_", " ")}
                </td>
                <td className="px-3 py-2 text-slate-600">
                  {payment.reference_no ?? "—"}
                </td>
                <td className="px-3 py-2 font-semibold">
                  <Money paise={payment.amount_paise} />
                </td>
                <td className="px-3 py-2">
                  {rejecting === payment.uid ? (
                    <div className="flex items-end gap-2">
                      <Field label="Reason">
                        <input
                          className={inputClass}
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                          autoFocus
                        />
                      </Field>
                      <Button
                        variant="danger"
                        disabled={busyUid === payment.uid || reason.trim().length < 3}
                        onClick={() => void reject(payment.uid)}
                      >
                        Confirm reject
                      </Button>
                      <Button variant="ghost" onClick={() => setRejecting(null)}>
                        Cancel
                      </Button>
                    </div>
                  ) : (
                    <div className="flex justify-end gap-2">
                      <Button
                        disabled={busyUid === payment.uid}
                        onClick={() => void confirm(payment.uid)}
                      >
                        {busyUid === payment.uid ? "Confirming…" : "Cash received"}
                      </Button>
                      <Button
                        variant="secondary"
                        onClick={() => setRejecting(payment.uid)}
                      >
                        Reject
                      </Button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
    </div>
  );
}
