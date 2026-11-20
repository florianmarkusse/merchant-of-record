// The buyer's waiting pages (src/mor/web/buyer_html.py): while a quote is
// worked out or a payment confirmed, ask the server every 2 s whether the
// page's state changed, and reload it once it has. Without this script the
// page refreshes itself through the <meta> refresh in its <noscript>.
// Agreed 19-Nov-2026 (docs/plan-quote.md, 4d-2's open list): no flicker,
// and a screen reader is not sent back to the top every 2 s.

export {}; // a module: its names stay out of the global scope

const INTERVAL_MS = 2000;
const body = document.body;
const url = body.dataset.statusUrl;
const seen = body.dataset.status;

const check = async (): Promise<void> => {
  try {
    const response = await fetch(url ?? "", { headers: { Accept: "application/json" } });
    if (response.ok) {
      const answer = (await response.json()) as { status: string };
      if (answer.status !== seen) {
        window.location.reload();
        return;
      }
    }
  } catch {
    // A dropped request: ask again next turn.
  }
  window.setTimeout(check, INTERVAL_MS);
};

if (url !== undefined && seen !== undefined) {
  window.setTimeout(check, INTERVAL_MS);
}
