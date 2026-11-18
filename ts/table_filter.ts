// A filter over a staff table (src/mor/web/staff_data_html.py): each
// `[data-filters]` block names its table's id, and holds a search box and a
// checkbox whose value is the row class to keep ("open", "differs"). The
// blocks are hidden without this script; then every row shows.

export {}; // a module: its names stay out of the global scope

for (const block of document.querySelectorAll<HTMLElement>("[data-filters]")) {
  const table = document.getElementById(block.dataset.filters ?? "") as HTMLTableElement;
  const search = block.querySelector("input[type=search]") as HTMLInputElement;
  const only = block.querySelector("input[type=checkbox]") as HTMLInputElement;
  const count = block.querySelector("[data-count]") as HTMLElement;
  // The first row holds the headings.
  const rows = [...table.rows].slice(1);

  const apply = (): void => {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter((word) => word !== "");
    let shown = 0;
    for (const row of rows) {
      const text = row.textContent?.toLowerCase() ?? "";
      const matches = words.every((word) => text.includes(word));
      const kept = !only.checked || row.classList.contains(only.value);
      row.hidden = !(matches && kept);
      if (!row.hidden) {
        shown += 1;
      }
    }
    count.textContent = `${shown} of ${rows.length} rows`;
  };

  search.addEventListener("input", apply);
  only.addEventListener("change", apply);
  block.hidden = false;
  apply();
}
