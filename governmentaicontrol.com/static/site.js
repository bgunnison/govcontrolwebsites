(() => {
  const searchInput = document.querySelector("[data-search-input]");
  const categoryFilter = document.querySelector("[data-category-filter]");
  const cards = Array.from(document.querySelectorAll("[data-post-card]"));
  const emptyState = document.querySelector("[data-empty-state]");

  if (!cards.length || !searchInput || !categoryFilter) {
    return;
  }

  const applyFilters = () => {
    const query = searchInput.value.trim().toLowerCase();
    const category = categoryFilter.value.trim().toLowerCase();
    let visible = 0;

    cards.forEach((card) => {
      const haystack = (card.getAttribute("data-search") || "").toLowerCase();
      const cardCategory = (card.getAttribute("data-category") || "").toLowerCase();
      const matchesQuery = !query || haystack.includes(query);
      const matchesCategory = !category || category === cardCategory;
      const show = matchesQuery && matchesCategory;
      card.hidden = !show;
      if (show) {
        visible += 1;
      }
    });

    if (emptyState) {
      emptyState.hidden = visible !== 0;
    }
  };

  searchInput.addEventListener("input", applyFilters);
  categoryFilter.addEventListener("change", applyFilters);
})();
