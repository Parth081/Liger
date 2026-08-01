"use client";

/** Dealer Pay Now (P4-T4-01). Part-payment allowed (DEC-09). */
import { useEffect, useState } from "react";

import {
  Button,
  Card,
  EmptyState,
  Field,
  Money,
  Skeleton,
  Table,
  inputClass,
} from "../components/ui";
import { ApiException, api, newIdempotencyKey } from "../lib/api";
import { rupeesToPaise } from "../lib/money";

type Invoice = {
  uid: string;
  invoice_no: string;
  total_paise: number;
  outstanding_paise: number;
  due_date: string;
  status: string;
  days_overdue: number;
};

export default function PayPage() {
  const [invoices, setInvoices] = useState<Invoice[] | null>(null);
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("upi");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const result = await api.get<{ items: Invoice[] }>(
          "/invoices?status=open&limit=100",
        );
        setInvoices(result.items);
        const due = result.items.reduce((sum, i) => sum + i.outstanding_paise, 0);
        if (due > 0) setAmount(String(due / 100));
      } catch {
        setInvoices([]);
      }
    })();
  }, []);

  const totalDue =
    invoices?.reduce((sum, invoice) => sum + invoice.outstanding_paise, 0) ?? 0;

  async function pay() {
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.post<{
        payment_uid: string;
        checkout: Record<string, unknown>;
      }>(
        "/payments/online/initiate",
        { amount_paise: rupeesToPaise(amount), method },
        newIdempotencyKey(), // R6
      );
      // The gateway SDK takes over here; the ledger only moves on the
      // signed webhook (BR-PAY-03), never on this response.
      setMessage(
        `Payment started (${result.payment_uid}). Complete it in the gateway window — your credit frees up the moment it is captured.`,
      );
    } catch (e) {
      setMessage(e instanceof ApiException ? e.message : "Could not start the payment");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Pay dues</h1>

      <Card title="What you owe">
        {invoices === null ? (
          <Skeleton />
        ) : invoices.length === 0 ? (
          <EmptyState title="Nothing outstanding" hint="You are fully paid up." />
        ) : (
          <>
            <Table head={["Invoice", "Due date", "Outstanding", "Overdue"]}>
              {invoices.map((invoice) => (
                <tr key={invoice.uid}>
                  <td className="px-3 py-2 font-medium">{invoice.invoice_no}</td>
                  <td className="px-3 py-2 text-slate-600">{invoice.due_date}</td>
                  <td className="px-3 py-2">
                    <Money paise={invoice.outstanding_paise} />
                  </td>
                  <td className="px-3 py-2">
                    {invoice.days_overdue > 0 ? (
                      <span className="font-semibold text-red-700">
                        {invoice.days_overdue} days
                      </span>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            <p className="mt-3 text-right text-sm">
              Total due <Money paise={totalDue} className="font-semibold" />
            </p>
          </>
        )}
      </Card>

      <Card title="Pay now">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Amount (₹)" hint="You can pay part of the total">
            <input
              className={inputClass}
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </Field>
          <Field label="Method">
            <select
              className={inputClass}
              value={method}
              onChange={(e) => setMethod(e.target.value)}
            >
              <option value="upi">UPI</option>
              <option value="card">Card</option>
              <option value="netbanking">Net banking</option>
              <option value="wallet">Wallet</option>
            </select>
          </Field>
          <div className="flex items-end">
            <Button
              className="w-full"
              disabled={busy || rupeesToPaise(amount) <= 0}
              onClick={() => void pay()}
            >
              {busy ? "Starting…" : "Pay"}
            </Button>
          </div>
        </div>
        {message && (
          <p className="mt-4 rounded bg-slate-100 px-3 py-2 text-sm">{message}</p>
        )}
      </Card>
    </div>
  );
}
