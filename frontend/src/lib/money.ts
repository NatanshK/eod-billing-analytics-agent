/**
 * Display helpers for integer paise.
 *
 * This is a deliberate character-for-character port of `backend/app/core/money.py`.
 * The Traced Figures panel proves grounding by matching the strings the backend
 * rendered, so if these two formatters ever disagree a correct narrative would
 * look like a grounding failure. Prefer the `*_display` fields the API already
 * sends; use these only where the UI composes a value the API did not.
 */

const RUPEE = "₹";

/** Group an integer digit-string the Indian way: 1234567 -> 12,34,567. */
export function groupIndian(digits: string): string {
  if (digits.length <= 3) return digits;

  let head = digits.slice(0, -3);
  const tail = digits.slice(-3);
  const parts: string[] = [];

  while (head.length > 2) {
    parts.unshift(head.slice(-2));
    head = head.slice(0, -2);
  }
  if (head) parts.unshift(head);

  return `${parts.join(",")},${tail}`;
}

/** Render integer paise as rupees: 319000 -> "₹3,190", 4285050 -> "₹42,850.50". */
export function formatPaise(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  const absolute = Math.abs(Math.trunc(paise));
  const whole = Math.floor(absolute / 100);
  const remainder = absolute % 100;

  let body = groupIndian(String(whole));
  if (remainder) body += `.${String(remainder).padStart(2, "0")}`;

  return `${sign}${RUPEE}${body}`;
}

/** "1 visit" / "3 visits" — the owner reads these as sentences. */
export function plural(count: number, singular: string, pluralForm?: string): string {
  return `${count} ${count === 1 ? singular : (pluralForm ?? `${singular}s`)}`;
}

/** A percentage, or an em dash when the rate is undefined (nothing billed). */
export function formatPercent(value: number | null): string {
  return value === null ? "—" : `${value}%`;
}
