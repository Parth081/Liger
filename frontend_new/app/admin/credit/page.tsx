"use client";

/** Credit centre (P3-T4-01…08): ageing, blocked list, limits, shadow review. */
import { useCallback, useEffect, useState } from "react";

import {
  Button,
  Card,
  CreditPill,
  ErrorState,
  Field,
  Money,
  Skeleton,
  Table,
  inputClass,
  type CreditColour,
} from "../../components/ui";
import { ApiException, api } from "../../lib/api";
import { rupeesToPaise } from "../../lib/money";

type Row = {
  customer_uid: string;
  business_name: string;
  status: string;
  colour: CreditColour;
  outstanding_paise: number;
  exposure_paise: number;
  effective_limit_paise: number;
  available_paise: number;
  ageing: Record<string, number>;
  overdue_invoices: { invoice_no: string; days_overdue: number; amount_paise: number }[];
};

type ShadowEvent = {
  event_type: string;
  reason: string | null;
  detail: Record<string, unknown> | null;
  is_shadow: boolean;
  at: string;
};

export default function CreditCentre() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"ageing" | "blocked" | "shadow">("ageing");
  const [selected, setSelected] = useState<Row | null>(null);
  const [shadow, setShadow] = useState<ShadowEvent[] | null>(null);
  const [limitInput, setLimitInput] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const [reloadToken, setReloadToken] = useState(0);
  const load = useCallback(() => setReloadToken((n) => n + 1), []);

  useEffect(() => {
    if (tab === "shadow") return;
    let cancelled = false;
    void (async () => {
      try {
        const path =
          tab === "blocked" ? "/credit/blocked" : "/credit/ageing?limit=100";
        const result = await api.get<{ items: Row[] }>(path);
        if (!cancelled) {
          setRows(result.items);
          setError(null);
        }
      } catch {
        if (!cancelled) setError("Could not load credit data");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tab, reloadToken]);

  async function openCustomer(row: Row) {
    setSelected(row);
    setLimitInput(String(row.effective_limit_paise / 100));
    setReason("");
    setMessage(null);
    try {
      const events = await api.get<{ items: ShadowEvent[] }>(
        `/customers/${row.customer_uid}/credit-events?limit=20`,
      );
      setShadow(events.items);
    } catch {
      setShadow(null);
    }
  }

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      setMessage(ok);
      load();
    } catch (e) {
      setMessage(e instanceof ApiException ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Credit centre</h1>

      <div className="flex gap-1 rounded-lg border border-slate-200 bg-white p-1">
        {(
          [
            ["ageing", "Ageing"],
            ["blocked", "Blocked dealers"],
            ["shadow", "Shadow review"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              tab === key ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-50"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "shadow" ? (
        <Card title="Decisions the engine would have made">
          <p className="mb-4 text-sm text-slate-600">
            While enforcement is in shadow mode (BR-CR-40), nobody is blocked. Open a
            dealer below to see what would have happened. Review these before switching
            enforcement on in Settings.
          </p>
          {selected && shadow ? (
            <Table head={["When", "Event", "Reason"]} empty={shadow.length === 0}>
              {shadow.map((event, i) => (
                <tr key={i}>
                  <td className="px-3 py-2 text-slate-600">
                    {new Date(event.at).toLocaleString()}
                  </td>
                  <td className="px-3 py-2">
                    {event.event_type}
                    {event.is_shadow && (
                      <span className="ml-2 rounded bg-slate-200 px-1.5 text-[10px] font-semibold">
                        SHADOW
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-slate-600">{event.reason ?? "—"}</td>
                </tr>
              ))}
            </Table>
          ) : (
            <p className="text-sm text-slate-500">
              Pick a dealer from the Ageing tab first.
            </p>
          )}
        </Card>
      ) : error ? (
        <ErrorState message={error} onRetry={load} />
      ) : rows === null ? (
        <Skeleton rows={6} />
      ) : (
        <Card title={`${rows.length} dealers`}>
          <Table
            head={["Dealer", "State", "Outstanding", "Available", "Oldest overdue", ""]}
            empty={rows.length === 0}
          >
            {rows.map((row) => (
              <tr key={row.customer_uid}>
                <td className="px-3 py-2">
                  <div className="font-medium">{row.business_name}</div>
                  <CreditPill colour={row.colour} />
                </td>
                <td className="px-3 py-2 text-slate-600">{row.status}</td>
                <td className="px-3 py-2">
                  <Money paise={row.outstanding_paise} />
                </td>
                <td className="px-3 py-2">
                  <Money
                    paise={row.available_paise}
                    className={row.available_paise < 0 ? "text-red-700" : ""}
                  />
                </td>
                <td className="px-3 py-2">
                  {row.overdue_invoices.length > 0
                    ? `${Math.max(
                        ...row.overdue_invoices.map((i) => i.days_overdue),
                      )} days`
                    : "—"}
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={() => void openCustomer(row)}
                    className="text-xs text-slate-600 hover:underline"
                  >
                    Manage
                  </button>
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      )}

      {selected && (
        <Card title={`Manage ${selected.business_name}`}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-3">
              <Field
                label="Credit limit (₹)"
                hint="A reason is required and is recorded against your name (BR-CR-51)."
              >
                <input
                  className={inputClass}
                  value={limitInput}
                  onChange={(e) => setLimitInput(e.target.value)}
                  inputMode="decimal"
                />
              </Field>
              <Field label="Reason">
                <input
                  className={inputClass}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Consistent early payer"
                />
              </Field>
              <Button
                disabled={busy || reason.trim().length < 3}
                onClick={() =>
                  void act(
                    () =>
                      api.patch(`/customers/${selected.customer_uid}/limit`, {
                        credit_limit_paise: rupeesToPaise(limitInput),
                        reason,
                      }),
                    "Limit updated",
                  )
                }
              >
                Save limit
              </Button>
            </div>

            <div className="space-y-3">
              <p className="text-sm text-slate-600">
                Blocking here is a manual decision and outranks the automatic
                engine — only an admin can clear it (BR-CR-52).
              </p>
              <div className="flex gap-2">
                <Button
                  variant="danger"
                  disabled={busy || reason.trim().length < 3}
                  onClick={() =>
                    void act(
                      () =>
                        api.post(`/customers/${selected.customer_uid}/block`, {
                          reason,
                        }),
                      "Dealer blocked",
                    )
                  }
                >
                  Block
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy || reason.trim().length < 3}
                  onClick={() =>
                    void act(
                      () =>
                        api.post(`/customers/${selected.customer_uid}/unblock`, {
                          reason,
                        }),
                      "Dealer unblocked",
                    )
                  }
                >
                  Unblock
                </Button>
              </div>
            </div>
          </div>

          {message && (
            <p className="mt-4 rounded bg-slate-100 px-3 py-2 text-sm">{message}</p>
          )}
          <button
            onClick={() => setSelected(null)}
            className="mt-3 text-xs text-slate-500 hover:underline"
          >
            Close
          </button>
        </Card>
      )}
    </div>
  );
}
