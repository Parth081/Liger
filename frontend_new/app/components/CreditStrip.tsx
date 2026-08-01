"use client";

/**
 * The persistent credit strip on the order screen (P2-T4-02).
 * Server decides; this only renders (BR-CR-22).
 */
import { CreditPill, Money, type CreditColour } from "./ui";

export type CreditDecision = {
  decision: "ALLOW" | "WARN" | "BLOCK" | "NEEDS_APPROVAL";
  reasons: string[];
  effective_limit_paise: number;
  exposure_paise: number;
  available_paise: number;
  outstanding_paise: number;
  overdue_invoices: {
    invoice_no: string;
    amount_paise: number;
    due_date: string;
    days_overdue: number;
  }[];
  suggested_payment_paise: number;
  shadow?: boolean;
};

function colourFor(decision: CreditDecision, cartTotal: number): CreditColour {
  if (decision.decision === "BLOCK") return "blocked";
  const limit = decision.effective_limit_paise;
  if (limit > 0 && decision.exposure_paise + cartTotal >= limit * 0.9) return "red";
  if (decision.decision === "NEEDS_APPROVAL") return "red";
  if (decision.decision === "WARN") return "amber";
  if (limit > 0 && decision.exposure_paise + cartTotal >= limit * 0.6) return "amber";
  return "green";
}

export function CreditStrip({
  credit,
  cartTotalPaise = 0,
}: {
  credit: CreditDecision;
  cartTotalPaise?: number;
}) {
  const colour = colourFor(credit, cartTotalPaise);
  const remaining = credit.available_paise - cartTotalPaise;
  const over = remaining < 0;

  const bar = {
    green: "border-emerald-200 bg-emerald-50",
    amber: "border-amber-200 bg-amber-50",
    red: "border-red-200 bg-red-50",
    blocked: "border-slate-700 bg-slate-800 text-white",
  }[colour];

  return (
    <div className={`rounded-xl border px-4 py-3 ${bar}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span>
            Limit <Money paise={credit.effective_limit_paise} className="font-semibold" />
          </span>
          <span>
            Outstanding{" "}
            <Money paise={credit.outstanding_paise} className="font-semibold" />
          </span>
          <span>
            Available{" "}
            <Money
              paise={credit.available_paise}
              className={`font-semibold ${
                credit.available_paise < 0 ? "text-red-700" : ""
              }`}
            />
          </span>
        </div>
        <CreditPill colour={colour} />
      </div>

      {cartTotalPaise > 0 && (
        <p className={`mt-2 text-sm ${over ? "font-semibold text-red-700" : ""}`}>
          {over ? (
            <>
              This cart is over the available credit by{" "}
              <Money paise={Math.abs(remaining)} />. Pay that much now, or ask
              for approval.
            </>
          ) : (
            <>
              After this cart: <Money paise={remaining} /> left.
            </>
          )}
        </p>
      )}

      {credit.overdue_invoices.length > 0 && (
        <p className="mt-2 text-xs">
          {credit.overdue_invoices.length} unpaid invoice
          {credit.overdue_invoices.length > 1 ? "s" : ""} — oldest{" "}
          {Math.max(...credit.overdue_invoices.map((i) => i.days_overdue))} days
          overdue.
        </p>
      )}

      {credit.shadow && (
        <p className="mt-2 text-xs italic opacity-80">
          Shadow mode: this would have been blocked once enforcement is on.
        </p>
      )}
    </div>
  );
}

/** The blocked-order screen (BR-CR-10/11) — shows exactly what to pay. */
export function BlockedNotice({
  details,
  onPay,
}: {
  details: CreditDecision;
  onPay?: () => void;
}) {
  return (
    <div className="rounded-xl border border-red-300 bg-red-50 p-5">
      <h3 className="text-base font-semibold text-red-900">
        Please clear your dues to place a new order
      </h3>
      <p className="mt-1 text-sm text-red-800">
        Outstanding <Money paise={details.outstanding_paise} className="font-semibold" />
      </p>

      {details.overdue_invoices.length > 0 && (
        <table className="mt-4 w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-red-700">
              <th className="py-1">Invoice</th>
              <th className="py-1">Due date</th>
              <th className="py-1 text-right">Amount</th>
              <th className="py-1 text-right">Overdue</th>
            </tr>
          </thead>
          <tbody>
            {details.overdue_invoices.map((invoice) => (
              <tr key={invoice.invoice_no} className="border-t border-red-200">
                <td className="py-1.5 font-medium">{invoice.invoice_no}</td>
                <td className="py-1.5">{invoice.due_date}</td>
                <td className="py-1.5 text-right">
                  <Money paise={invoice.amount_paise} />
                </td>
                <td className="py-1.5 text-right font-semibold">
                  {invoice.days_overdue} days
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {onPay && (
        <button
          onClick={onPay}
          className="mt-4 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500"
        >
          Pay {details.suggested_payment_paise > 0 ? "" : "now"}
          {details.suggested_payment_paise > 0 && (
            <>
              {" "}
              <Money paise={details.suggested_payment_paise} /> now
            </>
          )}
        </button>
      )}
    </div>
  );
}
