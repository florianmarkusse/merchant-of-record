// The fake PSP's card-fields script (src/mor/fakes/psp.py), standing for a
// PSP's own, as Stripe's js.stripe.com: served from the PSP's origin, it
// mounts the card form in a frame of that origin, so the card never
// touches the merchant's page (docs/plan-payments.md §4, card fields).
// Not ours in prod: real PSPs ship their own.

interface FakePsp {
  mount(element: HTMLElement, secret: string): void;
}

interface Window {
  FakePsp?: FakePsp;
}

// Where this script came from: the frame is served from the same origin.
const fakePspOrigin = new URL((document.currentScript as HTMLScriptElement).src).origin;

window.FakePsp = {
  mount(element: HTMLElement, secret: string): void {
    const frame = document.createElement("iframe");
    frame.src = `${fakePspOrigin}/_fake/psp/fields/${encodeURIComponent(secret)}`;
    frame.title = "Card details";
    element.replaceChildren(frame);
  },
};
