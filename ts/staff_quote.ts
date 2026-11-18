// The staff quote page (src/mor/web/staff_quote_html.py), improved:
//
// - the quote follows the form as you type: the page is fetched again with
//   the form's values and its #result swapped in, and the address updated so
//   a reload or a shared link shows the same; the form's button does the same
//   work without this script;
// - the day beside the "Rules as of" picker and each field's error follow
//   it the same way; after Quote is pressed, missing fields are marked too;
// - "Show every part" opens or closes every option's parts at once.

export {}; // a module: its names stay out of the global scope

const form = document.getElementById("order") as HTMLFormElement;
const status = document.getElementById("live-status") as HTMLSpanElement;
const WAIT_MS = 450;

let timer: number | undefined;
let inFlight: AbortController | undefined;

// Once Quote is pressed, every later update also marks what is missing,
// as the server does for `go` (also when the page was opened with it).
let pressed = new URLSearchParams(location.search).has("go");

function query(): string {
  const params = new URLSearchParams();
  for (const [name, value] of new FormData(form)) {
    if (typeof value === "string" && value.trim() !== "") {
      params.append(name, value.trim());
    }
  }
  if (pressed) {
    params.append("go", "1");
  }
  return params.toString();
}

async function refresh(): Promise<void> {
  inFlight?.abort();
  const controller = new AbortController();
  inFlight = controller;
  const search = query();
  const address = `${form.action}?${search}`;
  status.textContent = "Updating…";
  try {
    const response = await fetch(address, { signal: controller.signal });
    if (!response.ok) {
      status.textContent = `Could not update (${response.status}); press Quote.`;
      return;
    }
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const fresh = page.getElementById("result");
    const current = document.getElementById("result");
    if (fresh === null || current === null) {
      status.textContent = "Could not update; press Quote.";
      return;
    }
    current.replaceWith(fresh);
    // Text the server renders beside a field (the day as people read it).
    for (const live of document.querySelectorAll<HTMLElement>("[data-live]")) {
      const update = page.getElementById(live.id);
      if (update !== null) {
        live.replaceWith(update);
      }
    }
    showToggle();
    history.replaceState(null, "", address);
    status.textContent = "";
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return; // a newer request replaced this one
    }
    status.textContent = "Could not update; press Quote.";
  }
}

function later(): void {
  window.clearTimeout(timer);
  timer = window.setTimeout(() => void refresh(), WAIT_MS);
}

function showToggle(): void {
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-toggle-parts]")) {
    button.hidden = false;
  }
}

form.addEventListener("input", later);
form.addEventListener("change", later);
form.addEventListener("submit", (event) => {
  event.preventDefault();
  pressed = true;
  window.clearTimeout(timer);
  void refresh();
});

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement) || !target.matches("[data-toggle-parts]")) {
    return;
  }
  const parts = [...document.querySelectorAll<HTMLDetailsElement>("details.parts")];
  const open = !parts.every((details) => details.open);
  for (const details of parts) {
    details.open = open;
  }
  target.textContent = open ? "Hide every part" : "Show every part";
});

showToggle();

// "Every part" on a card opens that option's parts below before jumping there.
document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLAnchorElement) || !target.hash.startsWith("#parts-")) {
    return;
  }
  const details = document.getElementById(target.hash.slice(1));
  if (details instanceof HTMLDetailsElement) {
    details.open = true;
  }
});
