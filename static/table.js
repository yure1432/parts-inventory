// Progressive enhancement: search + column sort over the server-rendered table.
// All ~2k rows are in the DOM already; this just filters/reorders them.
(function () {
  const table = document.getElementById("parts");
  if (!table) return;
  const tbody = table.tBodies[0];
  const search = document.getElementById("search");
  const count = document.getElementById("count");
  const allRows = Array.from(tbody.rows);

  function updateCount() {
    const shown = allRows.filter((r) => r.style.display !== "none").length;
    count.textContent = shown + " / " + allRows.length;
  }

  // --- search ---
  if (search) {
    search.addEventListener("input", function () {
      const q = search.value.trim().toLowerCase();
      for (const row of allRows) {
        row.style.display = !q || row.textContent.toLowerCase().includes(q) ? "" : "none";
      }
      updateCount();
    });
  }

  // --- sort ---
  const headers = table.tHead.rows[0].cells;
  for (let i = 0; i < headers.length; i++) {
    const th = headers[i];
    if (!th.dataset.key || th.classList.contains("actions")) continue;
    th.addEventListener("click", function () {
      const numeric = th.dataset.key === "quantity";
      const asc = !th.classList.contains("sort-asc");
      for (const h of headers) h.classList.remove("sort-asc", "sort-desc");
      th.classList.add(asc ? "sort-asc" : "sort-desc");

      const rows = Array.from(tbody.rows);
      rows.sort(function (a, b) {
        let x = a.cells[i].textContent.trim();
        let y = b.cells[i].textContent.trim();
        if (numeric) { x = parseFloat(x) || 0; y = parseFloat(y) || 0; return asc ? x - y : y - x; }
        return asc ? x.localeCompare(y) : y.localeCompare(x);
      });
      for (const r of rows) tbody.appendChild(r);
    });
  }

  updateCount();
})();
