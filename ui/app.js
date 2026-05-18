const profileStats = document.querySelector("#profile-stats");
const topVibes = document.querySelector("#top-vibes");
const manualVibes = document.querySelector("#manual-vibes");
const searchForm = document.querySelector("#search-form");
const searchResults = document.querySelector("#search-results");
const libraryTitle = document.querySelector("#library-title");
const libraryList = document.querySelector("#library-list");
const importButton = document.querySelector("#import-button");
const importStatus = document.querySelector("#import-status");
const vibeForm = document.querySelector("#vibe-form");

let currentProfile = null;
let activeLibrary = "watchlist";

const libraries = {
  rated: { label: "Seen & Rated", countKey: "rated" },
  favorites: { label: "Hearts", countKey: "favorites" },
  watchlist: { label: "Watchlist", countKey: "watchlist" },
  disliked: { label: "Avoid", countKey: "disliked" },
};

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || response.statusText);
  }
  return response.json();
}

function itemState(item) {
  return item.state || {
    rating: currentProfile?.ratings?.[item.id],
    liked: currentProfile?.liked?.includes(item.id),
    disliked: currentProfile?.disliked?.includes(item.id),
    favorite: currentProfile?.favorites?.includes(item.id),
    watchlisted: currentProfile?.watchlist?.includes(item.id),
    seen: currentProfile?.seen?.includes(item.id),
  };
}

function latestState(itemId) {
  return {
    rating: currentProfile?.ratings?.[itemId],
    liked: currentProfile?.liked?.includes(itemId),
    disliked: currentProfile?.disliked?.includes(itemId),
    favorite: currentProfile?.favorites?.includes(itemId),
    watchlisted: currentProfile?.watchlist?.includes(itemId),
    seen: currentProfile?.seen?.includes(itemId),
  };
}

function renderStars(item) {
  const state = itemState(item);
  const rating = Number(state.rating || 0);
  const stars = document.createElement("div");
  stars.className = "stars";

  for (let value = 0.5; value <= 5; value += 0.5) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `star half-star ${value <= rating ? "active" : ""}`;
    button.textContent = value % 1 === 0 ? "★" : "☆";
    button.title = `Rate ${value}`;
    button.addEventListener("click", async () => {
      await request(`/profile/ratings/${item.id}?rating=${value}`, { method: "POST" });
      await refreshAll();
      await repeatSearch();
    });
    stars.appendChild(button);
  }

  return stars;
}

async function clearRating(item) {
  await request(`/profile/ratings/${item.id}`, { method: "DELETE" });
  await refreshAll();
  await repeatSearch();
}

function actionButton(label, active, onClick, danger = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `small ${active ? "active" : ""} ${danger ? "danger" : ""}`;
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function renderItem(item) {
  const state = itemState(item);
  const row = document.createElement("article");
  row.className = "item";

  const poster = document.createElement("img");
  poster.className = "poster";
  poster.src = item.poster;
  poster.alt = "";
  poster.loading = "lazy";

  const main = document.createElement("div");
  main.className = "item-main";

  const title = document.createElement("div");
  title.className = "item-title";
  title.innerHTML = `<strong>${item.name}</strong>`;
  if (item.year) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = item.year;
    title.appendChild(badge);
  }

  const meta = document.createElement("p");
  meta.textContent = [item.type, ...(item.genres || [])].filter(Boolean).join(" · ");

  const overview = document.createElement("p");
  overview.className = "overview";
  overview.textContent = item.overview || "";

  const actions = document.createElement("div");
  actions.className = "actions";
  actions.appendChild(renderStars(item));
  if (state.rating) {
    const clearButton = actionButton("Clear rating", false, async () => {
      await clearRating(item);
    });
    clearButton.classList.add("clear-rating");
    actions.appendChild(clearButton);
  }
  const heartButton = actionButton("♥", state.favorite, async () => {
    const active = latestState(item.id).favorite;
    await request(`/profile/favorites/${item.id}`, { method: active ? "DELETE" : "POST" });
    await refreshAll();
    await repeatSearch();
  });
  heartButton.classList.add("heart");
  heartButton.title = "Heart";
  heartButton.setAttribute("aria-label", "Heart");
  actions.appendChild(heartButton);
  actions.appendChild(actionButton("Watchlist", state.watchlisted, async () => {
    const active = latestState(item.id).watchlisted;
    await request(`/profile/watchlist/${item.id}`, { method: active ? "DELETE" : "POST" });
    await refreshAll();
    await repeatSearch();
  }));
  actions.appendChild(actionButton("Seen", state.seen, async () => {
    const active = latestState(item.id).seen;
    await request(`/profile/seen/${item.id}`, { method: active ? "DELETE" : "POST" });
    await refreshAll();
    await repeatSearch();
  }));
  actions.appendChild(actionButton("Avoid", state.disliked, async () => {
    const active = latestState(item.id).disliked;
    await request(`/profile/dislikes/${item.id}`, { method: active ? "DELETE" : "POST" });
    await refreshAll();
    await repeatSearch();
  }, true));

  main.append(title, meta, overview, actions);
  row.append(poster, main);
  return row;
}

function renderItems(container, items, emptyText) {
  container.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("p");
    empty.textContent = emptyText;
    container.appendChild(empty);
    return;
  }
  items.forEach((item) => container.appendChild(renderItem(item)));
}

function renderProfile(profile) {
  currentProfile = profile;
  if (!profileStats) {
    return;
  }
  const matches = profile.catalog_matches || {};
  profileStats.innerHTML = "";
  Object.entries(libraries).forEach(([key, config]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `stat ${activeLibrary === key ? "active" : ""}`;
    button.innerHTML = `<strong>${matches[config.countKey] || 0}</strong><span>${config.label}</span>`;
    button.addEventListener("click", async () => {
      activeLibrary = key;
      renderProfile(currentProfile);
      await loadLibrary();
    });
    profileStats.appendChild(button);
  });

  if (!manualVibes) {
    return;
  }
  manualVibes.innerHTML = "";
  (profile.vibes || []).forEach((vibe) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "vibe-chip";
    chip.textContent = `${vibe} ×`;
    chip.addEventListener("click", async () => {
      await request(`/profile/vibes?vibe=${encodeURIComponent(vibe)}`, { method: "DELETE" });
      await refreshAll();
    });
    manualVibes.appendChild(chip);
  });
}

async function loadProfile() {
  const profile = await request("/profile");
  renderProfile(profile);
}

async function loadLibrary() {
  if (!libraryTitle || !libraryList) {
    return;
  }
  try {
    const label = libraries[activeLibrary]?.label || "Library";
    libraryTitle.textContent = label;
    const data = await request(`/profile/lists/${activeLibrary}`);
    renderItems(libraryList, data.results || [], `No ${label.toLowerCase()} items yet.`);
  } catch (error) {
    renderItems(libraryList, [], `List unavailable: ${error.message}`);
  }
}

async function loadTopVibes() {
  if (!topVibes) {
    return;
  }
  topVibes.innerHTML = "<span class=\"vibe-chip\">Analyzing</span>";
  try {
    const data = await request("/profile/top-vibes");
    topVibes.innerHTML = "";
    (data.top_vibes || []).forEach((vibe) => {
      const chip = document.createElement("span");
      chip.className = "vibe-chip";
      chip.textContent = vibe.score === null ? vibe.name : `${vibe.name} ${vibe.score}`;
      topVibes.appendChild(chip);
    });
  } catch (error) {
    topVibes.innerHTML = "";
    const chip = document.createElement("span");
    chip.className = "vibe-chip";
    chip.textContent = "Vibes unavailable";
    topVibes.appendChild(chip);
  }
}

async function refreshAll() {
  await loadProfile();
  await loadLibrary();
  loadTopVibes();
}

async function repeatSearch() {
  const query = document.querySelector("#search-query").value.trim();
  if (!query) {
    return;
  }
  await runSearch(query, document.querySelector("#search-type").value);
}

async function runSearch(query, type) {
  const data = await request(`/items/search?query=${encodeURIComponent(query)}&type=${encodeURIComponent(type)}&limit=20`);
  renderItems(searchResults, data.results || [], "No local catalog matches.");
}

if (searchForm) {
  searchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = document.querySelector("#search-query").value.trim();
    if (!query) {
      return;
    }
    try {
      await runSearch(query, document.querySelector("#search-type").value);
    } catch (error) {
      searchResults.innerHTML = `<p>Search failed: ${error.message}</p>`;
    }
  });
}

if (vibeForm) {
  vibeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.querySelector("#vibe-input");
    const vibe = input.value.trim();
    if (!vibe) {
      return;
    }
    await request(`/profile/vibes?vibe=${encodeURIComponent(vibe)}`, { method: "POST" });
    input.value = "";
    await refreshAll();
  });
}

if (importButton) {
  importButton.addEventListener("click", async () => {
    const file = document.querySelector("#import-file").files[0];
    const source = document.querySelector("#import-source").value;
    if (!file) {
      importStatus.textContent = "Choose a CSV file first.";
      return;
    }

    try {
      importStatus.textContent = "Importing...";
      const text = await file.text();
      const result = await request(`/profile/import/${source}`, {
        method: "POST",
        headers: { "Content-Type": "text/plain" },
        body: text,
      });
      importStatus.textContent = `Imported ${result.imported_ratings} ratings, ${result.imported_favorites} hearts, ${result.imported_watchlist} watchlist items, ${result.unmatched} unmatched, ${result.skipped_episodes} episodes skipped.`;
      await refreshAll();
    } catch (error) {
      importStatus.textContent = `Import failed: ${error.message}`;
    }
  });
}

refreshAll().catch((error) => {
  const text = `TasteSpace API unavailable: ${error.message}`;
  searchResults.innerHTML = `<p>${text}</p>`;
  if (profileStats) {
    profileStats.innerHTML = `<div class="stat"><strong>Offline</strong><span>API</span></div>`;
  }
});
