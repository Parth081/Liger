"use client";

/**
 * The L × B order screen (P2-T4-01…07) — the calculation the business runs on.
 *
 * Keyboard-first: Design → L ft → L in → B ft → B in → Qty → Enter adds the
 * line and returns focus to Design. Staff enter 50 lines a day.
 *
 * The local preview is for responsiveness only; the server total is
 * authoritative and is shown before submit (R4, BR-SQFT-11).
 */
import { useEffect, useRef, useState } from "react";

import { ApiException, api, newIdempotencyKey } from "../lib/api";
import { formatSqft } from "../lib/money";
import { BlockedNotice, CreditStrip, type CreditDecision } from "./CreditStrip";
import {
  Button,
  Card,
  EmptyState,
  Field,
  Money,
  Table,
  inputClass,
} from "./ui";

type DesignLookup = {
  design_no: string;
  name: string;
  category: string;
  status: string;
  gst_pct: number;
  rate_paise: number | null;
  rate_source: string | null;
  rate_error?: string;
  images: { url: string; alt?: string | null }[];
};

type LinePreview = {
  design_no: string;
  design_name: string;
  raw_sqft: number;
  billable_sqft: number;
  min_rule_applied: boolean;
  line_area: number;
  rate_paise: number;
  goods_amount_paise: number;
  making_charge_paise: number;
  taxable_paise: number;
  gst_pct: number;
  notes: string[];
};

type CartLine = {
  key: string;
  design_no: string;
  length_ft: string;
  length_in: string;
  breadth_ft: string;
  breadth_in: string;
  quantity: string;
  room_label: string;
  preview?: LinePreview;
};

type QuoteResponse = {
  subtotal_paise: number;
  taxable_paise: number;
  cgst_paise: number;
  sgst_paise: number;
  igst_paise: number;
  round_off_paise: number;
  grand_total_paise: number;
  needs_approval: boolean;
  approval_reasons: string[];
};

const emptyDraft = (): CartLine => ({
  key: crypto.randomUUID(),
  design_no: "",
  length_ft: "",
  length_in: "0",
  breadth_ft: "",
  breadth_in: "0",
  quantity: "1",
  room_label: "",
});

const DRAFT_STORAGE_KEY = "liger.order.draft";

export default function OrderForm({
  customerUid,
  customerState,
}: {
  customerUid?: string;
  customerState?: string;
}) {
  const [draft, setDraft] = useState<CartLine>(emptyDraft);
  const [design, setDesign] = useState<DesignLookup | null>(null);
  const [designError, setDesignError] = useState<string | null>(null);
  // Previews and quotes are tagged with the inputs that produced them, so a
  // stale result is filtered out on render instead of cleared in an effect.
  const [previewFor, setPreviewFor] = useState<{
    signature: string;
    data: LinePreview;
  } | null>(null);
  const [quoteFor, setQuoteFor] = useState<{
    signature: string;
    data: QuoteResponse;
  } | null>(null);
  const [credit, setCredit] = useState<CreditDecision | null>(null);
  const [blocked, setBlocked] = useState<CreditDecision | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [placed, setPlaced] = useState<{
    order_no: string;
    status: string;
  } | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const designInputRef = useRef<HTMLInputElement>(null);

  // BR-ORD-11: a half-typed 40-line order must survive a lost network.
  // Read once during the initial state build so no effect writes state.
  const [lines, setLines] = useState<CartLine[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      const saved = window.localStorage.getItem(DRAFT_STORAGE_KEY);
      const parsed = saved ? (JSON.parse(saved) as CartLine[]) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  });

  useEffect(() => {
    window.localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(lines));
  }, [lines]);

  // BR-CAT-06: design preview as soon as a valid number is typed
  useEffect(() => {
    const designNo = draft.design_no.trim();
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      if (!designNo) {
        setDesign(null);
        setDesignError(null);
        return;
      }
      try {
        const found = await api.get<DesignLookup>(
          `/designs/${encodeURIComponent(designNo)}`,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setDesign(found);
        setDesignError(found.rate_error ?? null);
      } catch (error) {
        if (controller.signal.aborted) return;
        setDesign(null);
        setDesignError(
          error instanceof ApiException && error.code === "DESIGN_NOT_FOUND"
            ? `Design "${designNo}" not found`
            : "Could not look up that design",
        );
      }
    }, 250);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [draft.design_no]);

  // Signature of the inputs a preview belongs to — lets a stale response be
  // ignored on render rather than cleared with a setState in an effect.
  const draftSignature = [
    draft.design_no.trim(),
    draft.length_ft,
    draft.length_in,
    draft.breadth_ft,
    draft.breadth_in,
    draft.quantity,
  ].join("|");

  const draftReady =
    Boolean(draft.design_no.trim()) &&
    Number(draft.length_ft || 0) > 0 &&
    Number(draft.breadth_ft || 0) > 0 &&
    Number(draft.quantity || 0) > 0 &&
    design !== null &&
    design.rate_paise !== null;

  const linePreview =
    draftReady && previewFor?.signature === draftSignature
      ? previewFor.data
      : null;

  // Live line preview — same engine as the bill (BR-SQFT-11)
  useEffect(() => {
    if (!draftReady) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const preview = await api.post<LinePreview>("/pricing/calculate-line", {
          design_no: draft.design_no.trim(),
          length_ft: draft.length_ft || "0",
          length_in: draft.length_in || "0",
          breadth_ft: draft.breadth_ft || "0",
          breadth_in: draft.breadth_in || "0",
          quantity: Number(draft.quantity || 1),
        });
        if (!controller.signal.aborted) {
          setPreviewFor({ signature: draftSignature, data: preview });
        }
      } catch {
        /* the Add button stays disabled until a preview succeeds */
      }
    }, 200);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftSignature, draftReady]);

  const cartSignature = JSON.stringify(
    lines.map((line) => [
      line.design_no,
      line.length_ft,
      line.length_in,
      line.breadth_ft,
      line.breadth_in,
      line.quantity,
    ]),
  );

  const quote =
    lines.length > 0 && quoteFor?.signature === cartSignature
      ? quoteFor.data
      : null;

  useEffect(() => {
    if (lines.length === 0) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await api.post<QuoteResponse>("/pricing/quote-cart", {
          items: lines.map((line) => ({
            design_no: line.design_no,
            length_ft: line.length_ft,
            length_in: line.length_in || "0",
            breadth_ft: line.breadth_ft,
            breadth_in: line.breadth_in || "0",
            quantity: Number(line.quantity),
          })),
          customer_state: customerState,
        });
        if (!cancelled) {
          setQuoteFor({ signature: cartSignature, data: result });
          setFormError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setFormError(
            error instanceof ApiException
              ? error.message
              : "Could not price the cart",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cartSignature, customerState]);

  // Live credit state for the strip
  useEffect(() => {
    if (!customerUid) return;
    let cancelled = false;
    void (async () => {
      try {
        const status = await api.get<{
          effective_limit_paise: number;
          exposure_paise: number;
          available_paise: number;
          outstanding_paise: number;
          overdue_invoices: CreditDecision["overdue_invoices"];
          colour: string;
        }>(`/credit/customers/${customerUid}/status`);
        if (cancelled) return;
        setCredit({
          decision: status.colour === "blocked" ? "BLOCK" : "ALLOW",
          reasons: [],
          effective_limit_paise: status.effective_limit_paise,
          exposure_paise: status.exposure_paise,
          available_paise: status.available_paise,
          outstanding_paise: status.outstanding_paise,
          overdue_invoices: status.overdue_invoices ?? [],
          suggested_payment_paise: 0,
        });
      } catch {
        /* the strip is informational; the server still gates at submit */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [customerUid, placed]);

  function addLine() {
    if (!draft.design_no.trim() || !linePreview) return;
    setLines((current) => [...current, { ...draft, preview: linePreview }]);
    setDraft((current) => ({
      ...emptyDraft(),
      room_label: current.room_label, // rooms come in runs
    }));
    setDesign(null);
    designInputRef.current?.focus();
  }

  function removeLine(key: string) {
    setLines((current) => current.filter((line) => line.key !== key));
  }

  async function placeOrder() {
    if (lines.length === 0) return;
    setSubmitting(true);
    setFormError(null);
    setBlocked(null);
    try {
      const response = await api.post<{ order_no: string; status: string }>(
        "/orders",
        {
          customer_uid: customerUid,
          items: lines.map((line) => ({
            design_no: line.design_no,
            length_ft: line.length_ft,
            length_in: line.length_in || "0",
            breadth_ft: line.breadth_ft,
            breadth_in: line.breadth_in || "0",
            quantity: Number(line.quantity),
            room_label: line.room_label || null,
          })),
        },
        newIdempotencyKey(), // R6
      );
      setPlaced(response);
      setLines([]);
      window.localStorage.removeItem(DRAFT_STORAGE_KEY);
    } catch (error) {
      if (error instanceof ApiException && error.code === "CREDIT_BLOCKED") {
        setBlocked(error.details as unknown as CreditDecision);
      } else {
        setFormError(
          error instanceof ApiException
            ? error.message
            : "Could not place the order",
        );
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (placed) {
    return (
      <Card title="Order placed">
        <p className="text-sm text-slate-700">
          Order <span className="font-semibold">{placed.order_no}</span> is{" "}
          <span className="font-semibold">{placed.status.toLowerCase()}</span>.
        </p>
        <Button className="mt-4" onClick={() => setPlaced(null)}>
          Place another order
        </Button>
      </Card>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      <div className="space-y-4">
        {credit && (
          <CreditStrip
            credit={credit}
            cartTotalPaise={quote?.grand_total_paise ?? 0}
          />
        )}

        {blocked && (
          <BlockedNotice
            details={blocked}
            onPay={() => {
              window.location.href = "/pay";
            }}
          />
        )}

        <Card title="Add a line">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              addLine();
            }}
            className="grid grid-cols-2 gap-3 sm:grid-cols-12"
          >
            <div className="col-span-2 sm:col-span-3">
              <Field label="Design No">
                <input
                  ref={designInputRef}
                  className={inputClass}
                  value={draft.design_no}
                  autoFocus
                  placeholder="LGR-2201"
                  onChange={(e) =>
                    setDraft({ ...draft, design_no: e.target.value })
                  }
                />
              </Field>
            </div>
            <div className="sm:col-span-2">
              <Field label="Length ft">
                <input
                  className={inputClass}
                  inputMode="decimal"
                  value={draft.length_ft}
                  onChange={(e) =>
                    setDraft({ ...draft, length_ft: e.target.value })
                  }
                />
              </Field>
            </div>
            <div className="sm:col-span-1">
              <Field label="in">
                <input
                  className={inputClass}
                  inputMode="decimal"
                  value={draft.length_in}
                  onChange={(e) =>
                    setDraft({ ...draft, length_in: e.target.value })
                  }
                />
              </Field>
            </div>
            <div className="sm:col-span-2">
              <Field label="Breadth ft">
                <input
                  className={inputClass}
                  inputMode="decimal"
                  value={draft.breadth_ft}
                  onChange={(e) =>
                    setDraft({ ...draft, breadth_ft: e.target.value })
                  }
                />
              </Field>
            </div>
            <div className="sm:col-span-1">
              <Field label="in">
                <input
                  className={inputClass}
                  inputMode="decimal"
                  value={draft.breadth_in}
                  onChange={(e) =>
                    setDraft({ ...draft, breadth_in: e.target.value })
                  }
                />
              </Field>
            </div>
            <div className="sm:col-span-1">
              <Field label="Qty">
                <input
                  className={inputClass}
                  inputMode="numeric"
                  value={draft.quantity}
                  onChange={(e) =>
                    setDraft({ ...draft, quantity: e.target.value })
                  }
                />
              </Field>
            </div>
            <div className="col-span-2 sm:col-span-2">
              <Field label="Room (optional)">
                <input
                  className={inputClass}
                  value={draft.room_label}
                  placeholder="Bedroom 1"
                  onChange={(e) =>
                    setDraft({ ...draft, room_label: e.target.value })
                  }
                />
              </Field>
            </div>
            <div className="col-span-2 sm:col-span-12">
              <Button type="submit" disabled={!linePreview}>
                Add line
              </Button>
              <span className="ml-3 text-xs text-slate-500">
                Press Enter to add and jump back to Design No
              </span>
            </div>
          </form>

          {designError && (
            <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              {designError}
            </p>
          )}

          {linePreview && (
            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
              <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
                <span>
                  Actual{" "}
                  <span className="font-medium">
                    {formatSqft(linePreview.raw_sqft)}
                  </span>
                </span>
                <span>
                  Billable{" "}
                  <span className="font-semibold">
                    {formatSqft(linePreview.billable_sqft)}
                  </span>{" "}
                  × {draft.quantity} ={" "}
                  <span className="font-semibold">
                    {formatSqft(linePreview.line_area)}
                  </span>
                </span>
                <span>
                  Line{" "}
                  <Money
                    paise={linePreview.taxable_paise}
                    className="font-semibold"
                  />
                </span>
              </div>
              {/* BR-SQFT-07: the dealer must never be surprised by the invoice */}
              {linePreview.notes.map((note) => (
                <p
                  key={note}
                  className="mt-2 rounded bg-amber-100 px-2 py-1 text-xs text-amber-900"
                >
                  {note}
                </p>
              ))}
            </div>
          )}
        </Card>

        <Card
          title={`Cart (${lines.length} line${lines.length === 1 ? "" : "s"})`}
        >
          {lines.length === 0 ? (
            <EmptyState
              title="No lines yet"
              hint="Type a design number and the measurements above."
            />
          ) : (
            <Table head={["Design", "Size", "Qty", "Billable", "Amount", ""]}>
              {lines.map((line) => (
                <tr key={line.key}>
                  <td className="px-3 py-2">
                    <div className="font-medium">{line.design_no}</div>
                    {line.room_label && (
                      <div className="text-xs text-slate-500">
                        {line.room_label}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-slate-600">
                    {line.length_ft}′
                    {line.length_in !== "0" && ` ${line.length_in}″`} ×{" "}
                    {line.breadth_ft}′
                    {line.breadth_in !== "0" && ` ${line.breadth_in}″`}
                  </td>
                  <td className="px-3 py-2">{line.quantity}</td>
                  <td className="px-3 py-2">
                    {line.preview ? formatSqft(line.preview.line_area) : "—"}
                    {line.preview?.min_rule_applied && (
                      <span className="ml-1 rounded bg-amber-100 px-1 text-[10px] font-semibold text-amber-800">
                        MIN
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {line.preview ? (
                      <Money paise={line.preview.taxable_paise} />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => removeLine(line.key)}
                      className="text-xs text-red-600 hover:underline"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>

      <aside className="space-y-4">
        {design && (
          <Card title="Design preview">
            {design.images[0] ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={design.images[0].url}
                alt={design.images[0].alt ?? design.name}
                className="mb-3 aspect-square w-full rounded-lg object-cover"
              />
            ) : (
              <div className="mb-3 flex aspect-square w-full items-center justify-center rounded-lg bg-slate-100 text-xs text-slate-400">
                No image
              </div>
            )}
            <p className="font-medium text-slate-900">{design.name}</p>
            <p className="text-xs text-slate-500">{design.category}</p>
            {design.rate_paise !== null && (
              <p className="mt-2 text-sm">
                Rate{" "}
                <Money paise={design.rate_paise} className="font-semibold" />{" "}
                <span className="text-xs text-slate-500">/ sq.ft</span>
              </p>
            )}
          </Card>
        )}

        <Card title="Order total">
          {!quote ? (
            <EmptyState title="Add a line to see the total" />
          ) : (
            <dl className="space-y-1.5 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-600">Subtotal</dt>
                <dd>
                  <Money paise={quote.subtotal_paise} />
                </dd>
              </div>
              {quote.cgst_paise > 0 && (
                <>
                  <div className="flex justify-between">
                    <dt className="text-slate-600">CGST</dt>
                    <dd>
                      <Money paise={quote.cgst_paise} />
                    </dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-slate-600">SGST</dt>
                    <dd>
                      <Money paise={quote.sgst_paise} />
                    </dd>
                  </div>
                </>
              )}
              {quote.igst_paise > 0 && (
                <div className="flex justify-between">
                  <dt className="text-slate-600">IGST</dt>
                  <dd>
                    <Money paise={quote.igst_paise} />
                  </dd>
                </div>
              )}
              {quote.round_off_paise !== 0 && (
                <div className="flex justify-between">
                  <dt className="text-slate-600">Round off</dt>
                  <dd>
                    <Money paise={quote.round_off_paise} />
                  </dd>
                </div>
              )}
              <div className="flex justify-between border-t border-slate-200 pt-2 text-base font-semibold">
                <dt>Grand total</dt>
                <dd>
                  <Money paise={quote.grand_total_paise} />
                </dd>
              </div>
            </dl>
          )}

          {quote?.needs_approval && (
            <p className="mt-3 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Needs admin approval: {quote.approval_reasons.join("; ")}
            </p>
          )}

          {formError && (
            <p className="mt-3 rounded bg-red-50 px-3 py-2 text-xs text-red-700">
              {formError}
            </p>
          )}

          <Button
            className="mt-4 w-full"
            disabled={lines.length === 0 || submitting}
            onClick={placeOrder}
          >
            {submitting ? "Placing…" : "Place order"}
          </Button>
        </Card>
      </aside>
    </div>
  );
}
