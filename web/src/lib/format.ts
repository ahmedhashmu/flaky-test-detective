// Display helpers.
//
// The precision rules match the CLI's: never show more precision than the sample size
// supports, and never round a rate up to look tidier than the data.

export const pct = (value: number, digits = 0) => `${(value * 100).toFixed(digits)}%`;

export const score2 = (value: number) => value.toFixed(2);

/** Minutes and hours, but never "0.0h" for something under a minute. */
export function duration(seconds: number): string {
  if (!seconds || seconds <= 0) return "0s";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${minutes.toFixed(minutes < 10 ? 1 : 0)}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${Math.round(minutes % 60)}m`;
}

/** Trims a long test id from the left: the tail is what identifies it. */
export function shortId(testId: string, width = 52): string {
  if (testId.length <= width) return testId;
  return `…${testId.slice(-(width - 1))}`;
}

/** The part after the last `::`, which is what people actually say out loud. */
export const leafName = (testId: string) => testId.split("::").pop() ?? testId;

export const shortSha = (sha: string | null) => (sha ? sha.slice(0, 8) : "—");

export function when(iso: string | null): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso.slice(0, 16).replace("T", " ");
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function relative(iso: string | null): string {
  if (!iso) return "—";
  const parsed = new Date(iso).getTime();
  if (Number.isNaN(parsed)) return "—";
  const seconds = (Date.now() - parsed) / 1000;
  if (seconds < 90) return "just now";
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.round(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** Collapses whitespace so a multi-line failure message fits one row. */
export const oneLine = (text: string | null, width = 160) => {
  if (!text) return "";
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= width ? flat : `${flat.slice(0, width - 1)}…`;
};

export const CAUSE_LABELS: Record<string, string> = {
  timeout: "Timeout",
  race: "Race condition",
  order_dependence: "Order dependence",
  network: "Network",
  resource: "Resource",
  time_dependence: "Time dependence",
  randomness: "Randomness",
  assertion: "Assertion",
  unknown: "Unknown",
};

export const causeLabel = (cause: string | null) =>
  cause ? (CAUSE_LABELS[cause] ?? cause) : "—";
