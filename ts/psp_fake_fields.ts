// Our glue for the fake PSP's card fields (src/mor/psp/fake_client.py
// `card_fields`): mount its form where the pay page left room for it, and
// hide the link to its hosted page only once the form is there. Without
// JavaScript, or if the PSP's script did not load, the link stays and the
// buyer pays on the PSP's page (docs/plan-payments.md §4, card fields).

export {}; // a module: its names stay out of the global scope

const container = document.getElementById("card-fields") as HTMLElement;
const link = document.getElementById("hosted-link") as HTMLElement;
const psp = window.FakePsp;

if (psp !== undefined) {
  psp.mount(container, container.dataset.secret ?? "");
  link.hidden = true;
}
