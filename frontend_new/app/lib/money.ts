/**
 * The ONLY place paise become rupees (CLAUDE.md §8, R1).
 * Never divide by 100 inline anywhere else.
 */

/** ₹1,23,45,678.90 — Indian digit grouping. */
export function formatINR(paise: number, opts?: { compact?: boolean }): string {
  if (!Number.isFinite(paise)) return "—";
  const negative = paise < 0;
  const total = Math.abs(Math.round(paise));

  if (opts?.compact) {
    const rupees = total / 100;
    const sign = negative ? "-" : "";
    if (rupees >= 1_00_00_000) return `${sign}₹${(rupees / 1_00_00_000).toFixed(2)} Cr`;
    if (rupees >= 1_00_000) return `${sign}₹${(rupees / 1_00_000).toFixed(2)} L`;
    if (rupees >= 1_000) return `${sign}₹${(rupees / 1_000).toFixed(1)}K`;
  }

  const rupees = Math.floor(total / 100);
  const paisePart = total % 100;
  let s = String(rupees);
  if (s.length > 3) {
    const head = s.slice(0, -3);
    const tail = s.slice(-3);
    const groups: string[] = [];
    let rest = head;
    while (rest.length > 2) {
      groups.unshift(rest.slice(-2));
      rest = rest.slice(0, -2);
    }
    groups.unshift(rest);
    s = `${groups.join(",")},${tail}`;
  }
  return `${negative ? "-" : ""}₹${s}.${String(paisePart).padStart(2, "0")}`;
}

/** Parse a rupee string to integer paise without float drift. */
export function rupeesToPaise(input: string): number {
  const cleaned = input.replace(/[₹,\s]/g, "").trim();
  if (!cleaned) return 0;
  const [whole, frac = ""] = cleaned.split(".");
  const paise = (frac + "00").slice(0, 2);
  const sign = whole.startsWith("-") ? -1 : 1;
  const wholeDigits = whole.replace("-", "") || "0";
  return sign * (Number(wholeDigits) * 100 + Number(paise));
}

/** Feet + inches for display: 90 inches -> 7' 6" */
export function formatFeetInches(inches: number): string {
  const ft = Math.floor(inches / 12);
  const inch = Math.round((inches % 12) * 100) / 100;
  return inch === 0 ? `${ft}'` : `${ft}' ${inch}"`;
}

export function formatSqft(value: number): string {
  return `${value.toFixed(2)} sq.ft`;
}
