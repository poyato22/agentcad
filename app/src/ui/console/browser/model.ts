// Small pure helpers for the agent Browser panel.

/** "fetched 2 h ago", "fetched just now" — a relative time for when a snapshot was taken. */
export function fmtFetched(fetchedAt: number, now: number = Date.now() / 1000): string {
  const secs = Math.max(0, now - fetchedAt);
  if (secs < 45) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} h ago`;
  const days = Math.round(hrs / 24);
  return `${days} d ago`;
}
