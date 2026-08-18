// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATUS = {
  none: { label: "Not yet contacted", classes: "bg-stone-100 border-stone-300 text-stone-600", dot: "bg-stone-400" },
  purple: { label: "Potential spot", classes: "bg-purple-100 border-purple-300 text-purple-800", dot: "bg-purple-500" },
  blue: { label: "Special note", classes: "bg-blue-100 border-blue-300 text-blue-800", dot: "bg-blue-500" },
  yellow: { label: "In communication", classes: "bg-yellow-100 border-yellow-300 text-yellow-800", dot: "bg-yellow-500" },
  red: { label: "Offered — no response / declined", classes: "bg-red-100 border-red-300 text-red-800", dot: "bg-red-500" },
  green: { label: "Confirmed", classes: "bg-green-100 border-green-300 text-green-800", dot: "bg-green-500" },
};
const STATUS_ORDER = ["none", "purple", "blue", "yellow", "red", "green"];
const RETENTION_YEARS = 3;

let state = {
  entries: [],
  roster: [],
  brackets: [],
  lookaheadDays: 120,
  lastSyncedAt: null,
  sheetUrl: "",
  archiveSheetUrl: "",
  syncing: false,
  archiveSyncing: false,
  activeTab: "openings",
  classroomFilter: "All",
  search: "",
  archiveSearch: "",
  sortDir: "asc",
  selectedEntryId: null,
  busyEntryId: null,   // id of the entry currently mid Google-Sheets operation
  busyLabel: "",        // short text describing what's happening
  addFamilyBusy: false,
};

function setEntryBusy(id, label) { state.busyEntryId = id; state.busyLabel = label; }
function clearEntryBusy() { state.busyEntryId = null; state.busyLabel = ""; }

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function esc(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatAge(months) {
  if (months == null) return "Unknown age";
  const y = Math.floor(months / 12), m = months % 12;
  if (y === 0) return `${m} mo`;
  if (m === 0) return `${y} yr`;
  return `${y} yr ${m} mo`;
}

function daysBetween(a, b) {
  return Math.round((b - a) / 86400000);
}

function looseMatch(a, b) {
  if (!a || !b) return true;
  const na = a.toLowerCase(), nb = b.toLowerCase();
  return na.includes(nb) || nb.includes(na);
}

function parseISODateLocal(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function toast(text, type = "success") {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.className = `text-sm rounded-lg px-4 py-2.5 border ${type === "error" ? "bg-red-50 border-red-200 text-red-700" : "bg-teal-50 border-teal-200 text-teal-800"}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = "hidden"; }, 5000);
}

function emptyStateHtml(icon, title, message) {
  return `
    <div class="flex flex-col items-center text-center gap-2 py-14 px-6 bg-white border border-dashed border-stone-300 rounded-2xl">
      <i data-lucide="${icon}" style="width:28px;height:28px;" class="text-stone-300 mb-1"></i>
      <p class="font-medium text-stone-700">${title}</p>
      <p class="text-sm text-stone-500 max-w-sm">${message}</p>
    </div>`;
}

function statusPillHtml(status) {
  const s = STATUS[status] || STATUS.none;
  return `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${s.classes}">
    <span class="w-1.5 h-1.5 rounded-full ${s.dot}"></span>${s.label}
  </span>`;
}

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
  const isForm = opts.body instanceof FormData;
  const res = await fetch(`/api${path}`, {
    headers: isForm ? undefined : { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.error || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

// ---------------------------------------------------------------------------
// Bootstrap / load
// ---------------------------------------------------------------------------

async function loadAll() {
  const data = await api("/bootstrap");
  state.entries = data.entries;
  state.roster = data.roster;
  state.brackets = data.brackets;
  state.lookaheadDays = data.lookaheadDays;
  state.lastSyncedAt = data.lastSyncedAt;
  state.sheetUrl = data.sheetUrl || "";
  state.archiveSheetUrl = data.archiveSheetUrl || "";
  render();
  if (state.selectedEntryId != null) renderEntryModal();
}

function init() {
  document.getElementById("roster-file-input").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) importRosterFile(f);
    e.target.value = "";
  });
  loadAll().catch((err) => toast(`Failed to load data: ${err.message}`, "error"));
}

// ---------------------------------------------------------------------------
// Derived data
// ---------------------------------------------------------------------------

function activeEntries() { return state.entries.filter((e) => !e.archived); }
function archivedEntries() { return state.entries.filter((e) => e.archived); }

function classroomOptions() {
  const names = [...state.brackets].sort((a, b) => a.minMonths - b.minMonths).map((b) => b.name);
  const hasUnclassified = activeEntries().some((e) => !e.currentBracket);
  return hasUnclassified ? [...names, "Unclassified"] : names;
}

function findNextToCall(classroomName) {
  return activeEntries()
    .filter((e) => e.currentBracket && looseMatch(e.currentBracket.name, classroomName))
    .filter((e) => !["green", "red"].includes(e.status))
    .sort((a, b) => new Date(a.timestamp || 0) - new Date(b.timestamp || 0))[0] || null;
}

function filteredWaitlist() {
  let list = activeEntries();
  if (state.classroomFilter !== "All") {
    list = list.filter((e) => (e.currentBracket ? e.currentBracket.name : "Unclassified") === state.classroomFilter);
  }
  if (state.search.trim()) {
    const q = state.search.toLowerCase();
    list = list.filter((e) => (e.childName + " " + e.parentName).toLowerCase().includes(q));
  }
  return [...list].sort((a, b) => {
    const da = new Date(a.timestamp || 0), db_ = new Date(b.timestamp || 0);
    return state.sortDir === "asc" ? da - db_ : db_ - da;
  });
}

function windowedOpenings() {
  return state.roster
    .filter((r) => (r.status || "").toLowerCase() === "active" && r.currentBracket && r.ageOutDate)
    .map((r) => ({ ...r, ageOutDateObj: parseISODateLocal(r.ageOutDate) }))
    .sort((a, b) => a.ageOutDateObj - b.ageOutDateObj)
    .filter((o) => {
      const d = daysBetween(new Date(), o.ageOutDateObj);
      return d <= state.lookaheadDays && d >= -14;
    });
}

function septemberCount() {
  return windowedOpenings().filter((o) => o.ageOutDateObj.getMonth() === 8).length;
}

function unclassifiedRoster() {
  return state.roster.filter((r) => (r.status || "").toLowerCase() === "active" && !r.currentBracket);
}

// ---------------------------------------------------------------------------
// Top-level render
// ---------------------------------------------------------------------------

function render() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    const active = btn.dataset.tab === state.activeTab;
    btn.className = `tab-btn flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition ${
      active ? "bg-white text-teal-800 shadow-sm" : "text-stone-500 hover:text-stone-700"
    }`;
  });
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.dataset.tab !== state.activeTab));

  if (state.activeTab === "openings") renderOpenings();
  if (state.activeTab === "waitlist") renderWaitlist();
  if (state.activeTab === "archive") renderArchive();
  if (state.activeTab === "settings") renderSettings();

  if (window.lucide) lucide.createIcons();
}

function setTab(tab) { state.activeTab = tab; render(); }

// ---------------------------------------------------------------------------
// Openings tab
// ---------------------------------------------------------------------------

function renderOpenings() {
  const panel = document.getElementById("panel-openings");
  const openings = windowedOpenings();
  const unclassified = unclassifiedRoster();
  const sc = septemberCount();
  const hasRoster = state.roster.length > 0;

  let listHtml;
  if (!hasRoster) {
    listHtml = emptyStateHtml("calendar-days", "No roster imported yet", "Import the Lillio enrollment export (CSV) to see upcoming openings.");
  } else if (openings.length === 0) {
    listHtml = emptyStateHtml("calendar-days", "No upcoming openings", `No active enrollments are due to age out of their group in the next ${state.lookaheadDays} days.`);
  } else {
    listHtml = `<div class="space-y-2.5">` + openings.map((o) => {
      const days = daysBetween(new Date(), o.ageOutDateObj);
      const next = findNextToCall(o.currentBracket.name);
      const moveLabel = o.nextBracket ? `→ ${esc(o.nextBracket.name)}` : `→ leaving the program`;
      return `
      <div class="bg-white border border-stone-200 rounded-xl p-4 flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-4">
          <div class="w-14 h-14 rounded-xl flex flex-col items-center justify-center text-xs font-semibold ${days <= 30 ? "bg-red-50 text-red-700" : "bg-stone-100 text-stone-600"}">
            <span class="text-lg leading-none">${days}</span>
            <span class="text-xs font-normal">days</span>
          </div>
          <div>
            <p class="font-medium text-stone-800">${esc(o.firstName)} ${esc(o.lastName)}</p>
            <p class="text-sm text-stone-500">
              ${esc(o.currentBracket.name)} ${moveLabel} · ${o.ageOutDateObj.toLocaleDateString()}
              ${days <= 30 ? `<span class="text-red-600 font-medium">· within 30 days</span>` : ""}
            </p>
            ${o.classroomMismatch ? `
            <p class="text-xs text-amber-600 flex items-center gap-1 mt-0.5">
              <i data-lucide="alert-triangle" style="width:12px;height:12px;"></i>
              Lillio still lists them in ${esc(o.classroom)}
            </p>` : ""}
          </div>
        </div>
        <div class="text-sm">
          ${next
            ? `<div class="flex items-center gap-2 bg-teal-50 border border-teal-200 rounded-lg px-3 py-2">
                 <i data-lucide="phone-call" style="width:14px;height:14px;" class="text-teal-700"></i>
                 <span class="text-teal-800">Next to call: <span class="font-semibold">${esc(next.childName)}</span></span>
               </div>`
            : `<span class="text-stone-400">No matching waitlist entries</span>`}
        </div>
      </div>`;
    }).join("") + `</div>`;
  }

  panel.innerHTML = `
    <div class="space-y-5">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 class="text-lg font-semibold text-stone-800">Upcoming openings</h2>
          <p class="text-sm text-stone-500">Computed from each child's date of birth against your age-group cutoffs — not from Lillio's Grad date field, which isn't kept up to date.</p>
        </div>
        <div class="flex items-center gap-3">
          <label class="text-sm text-stone-500 flex items-center gap-2">
            Look ahead
            <input type="number" min="7" max="365" value="${state.lookaheadDays}" onchange="setLookahead(this.value)"
              class="w-20 px-2 py-1 border border-stone-300 rounded-lg text-sm" />
            days
          </label>
          <button onclick="document.getElementById('roster-file-input').click()" class="flex items-center gap-1.5 px-3.5 py-2 bg-teal-700 hover:bg-teal-800 text-white rounded-lg text-sm font-medium">
            <i data-lucide="upload" style="width:15px;height:15px;"></i> Import Lillio export
          </button>
        </div>
      </div>

      ${sc > 0 ? `
      <div class="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
        <i data-lucide="alert-triangle" style="width:18px;height:18px;" class="text-amber-600 mt-0.5 shrink-0"></i>
        <p class="text-sm text-amber-800">
          <span class="font-semibold">${sc} spot${sc === 1 ? "" : "s"} opening in September</span> — the busiest turnover period, as
          preschoolers age out and toddlers move up. Enrollment visits typically start in April, so plan outreach accordingly.
        </p>
      </div>` : ""}

      ${unclassified.length > 0 ? `
      <div class="flex items-start gap-3 bg-stone-100 border border-stone-300 rounded-xl px-4 py-3">
        <i data-lucide="alert-triangle" style="width:18px;height:18px;" class="text-stone-500 mt-0.5 shrink-0"></i>
        <p class="text-sm text-stone-700">
          <span class="font-semibold">${unclassified.length} enrolled ${unclassified.length === 1 ? "child doesn't" : "children don't"} match any configured age group</span>
          — ${unclassified.map((u) => esc(u.firstName + " " + u.lastName)).join(", ")}.
          Check their date of birth, or adjust the age-group cutoffs in Settings.
        </p>
      </div>` : ""}

      ${listHtml}
    </div>
  `;
  lucide.createIcons();
}

async function setLookahead(value) {
  const n = Number(value) || 120;
  try {
    await api("/settings", { method: "PUT", body: JSON.stringify({ lookaheadDays: n }) });
    await loadAll();
  } catch (err) {
    toast(`Couldn't save setting: ${err.message}`, "error");
  }
}

async function saveSheetUrl() {
  const value = document.getElementById("sheet-url-input").value.trim();
  try {
    await api("/settings", { method: "PUT", body: JSON.stringify({ sheetUrl: value }) });
    await loadAll();
    toast(value ? "Sheet link saved. It'll be used on the next sync." : "Sheet link cleared — back to the default.");
  } catch (err) {
    toast(`Couldn't save that link: ${err.message}`, "error");
  }
}

async function saveArchiveSheetUrl() {
  const value = document.getElementById("archive-sheet-url-input").value.trim();
  try {
    await api("/settings", { method: "PUT", body: JSON.stringify({ archiveSheetUrl: value }) });
    await loadAll();
    toast(value ? "Archive sheet link saved." : "Archive sheet link cleared — archived families will no longer be copied to a sheet.");
  } catch (err) {
    toast(`Couldn't save that link: ${err.message}`, "error");
  }
}

async function importRosterFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const data = await api("/roster/import", { method: "POST", body: fd });
    await loadAll();
    toast(`Roster updated — ${data.count} enrolled children loaded.`);
  } catch (err) {
    toast(`Couldn't read that file: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Waitlist tab
// ---------------------------------------------------------------------------

function renderWaitlist() {
  const panel = document.getElementById("panel-waitlist");
  panel.innerHTML = `
    <div class="space-y-5">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 class="text-lg font-semibold text-stone-800">Waitlist &amp; call list</h2>
          <p class="text-sm text-stone-500">Chronological by date added — oldest first is next in line.</p>
          <p class="text-xs text-stone-400">Staff and sibling priority are applied manually, outside this order.</p>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-xs text-stone-400">${state.lastSyncedAt ? `Synced ${new Date(state.lastSyncedAt).toLocaleString()}` : "Not synced yet"}</span>
          <button onclick="openAddModal()" class="flex items-center gap-1.5 px-3.5 py-2 border border-stone-300 text-stone-700 hover:bg-stone-100 rounded-lg text-sm font-medium">
            <i data-lucide="plus" style="width:15px;height:15px;"></i> Add family
          </button>
          <button onclick="syncWaitlistForm()" ${state.syncing ? "disabled" : ""} class="flex items-center gap-1.5 px-3.5 py-2 bg-teal-700 hover:bg-teal-800 disabled:opacity-60 text-white rounded-lg text-sm font-medium">
            <i data-lucide="refresh-cw" style="width:15px;height:15px;" class="${state.syncing ? "animate-spin" : ""}"></i> ${state.syncing ? "Syncing…" : "Sync form responses"}
          </button>
        </div>
      </div>

      <div id="wl-filters" class="flex flex-wrap items-center gap-2"></div>
      <div id="wl-nextup"></div>

      <div class="flex items-center gap-3">
        <div class="relative flex-1 max-w-xs">
          <i data-lucide="search" style="width:15px;height:15px;" class="absolute left-3 top-1/2 -translate-y-1/2 text-stone-400"></i>
          <input id="wl-search-input" value="${esc(state.search)}" oninput="setSearch(this.value)" placeholder="Search child or parent name"
            class="w-full pl-8 pr-3 py-2 border border-stone-300 rounded-lg text-sm" />
        </div>
        <button onclick="toggleSort()" class="flex items-center gap-1 px-3 py-2 border border-stone-300 rounded-lg text-sm text-stone-600">
          <i data-lucide="${state.sortDir === "asc" ? "chevron-up" : "chevron-down"}" style="width:14px;height:14px;"></i>
          ${state.sortDir === "asc" ? "Oldest first" : "Newest first"}
        </button>
      </div>

      <div id="wl-rows"></div>
    </div>
  `;
  updateWaitlistView();
}

function updateWaitlistView() {
  const active = activeEntries();
  const options = classroomOptions();
  const list = filteredWaitlist();
  const nextUp = state.classroomFilter !== "All" ? findNextToCall(state.classroomFilter) : null;

  document.getElementById("wl-filters").innerHTML = `
    <button data-classroom="All" onclick="setClassroomFilter(this.dataset.classroom)"
      class="px-3 py-1.5 rounded-full text-sm font-medium border ${state.classroomFilter === "All" ? "bg-teal-700 border-teal-700 text-white" : "bg-white border-stone-300 text-stone-600"}">
      All (${active.length})
    </button>
    ${options.map((name) => {
      const count = active.filter((e) => (e.currentBracket ? e.currentBracket.name : "Unclassified") === name).length;
      const isActive = state.classroomFilter === name;
      return `<button data-classroom="${esc(name)}" onclick="setClassroomFilter(this.dataset.classroom)"
        class="px-3 py-1.5 rounded-full text-sm font-medium border ${isActive ? "bg-teal-700 border-teal-700 text-white" : "bg-white border-stone-300 text-stone-600"}">
        ${esc(name)} (${count})
      </button>`;
    }).join("")}
  `;

  document.getElementById("wl-nextup").innerHTML = (state.classroomFilter !== "All" && nextUp) ? `
    <div class="flex items-center gap-2 bg-teal-50 border border-teal-200 rounded-xl px-4 py-3">
      <i data-lucide="phone-call" style="width:16px;height:16px;" class="text-teal-700"></i>
      <p class="text-sm text-teal-800">Next to call for <span class="font-semibold">${esc(state.classroomFilter)}</span>: <span class="font-semibold">${esc(nextUp.childName)}</span> (${esc(nextUp.parentName)}, ${esc(nextUp.phone)})</p>
    </div>` : "";

  document.getElementById("wl-rows").innerHTML = list.length === 0
    ? emptyStateHtml("users", "No families on this list", "Sync waitlist form responses or add a family manually to get started.")
    : `<div class="bg-white border border-stone-200 rounded-2xl divide-y divide-stone-100 overflow-hidden">` +
      list.map((e, idx) => `
        <button onclick="openEntryModal(${e.id})" class="w-full text-left px-4 py-3.5 flex flex-wrap items-center justify-between gap-3 hover:bg-stone-50 transition">
          <div class="flex items-center gap-3 min-w-0">
            <span class="w-6 text-center text-xs font-semibold text-stone-400">${idx + 1}</span>
            <div class="min-w-0">
              <p class="font-medium text-stone-800 truncate">${esc(e.childName)} <span class="font-normal text-stone-400">· ${formatAge(e.ageMonths)}</span></p>
              <p class="text-sm text-stone-500 truncate">${esc(e.parentName)} · Added ${e.timestamp ? new Date(e.timestamp).toLocaleDateString() : "—"}</p>
            </div>
          </div>
          <div class="flex items-center gap-3 shrink-0">
            ${e.mismatch ? `<span title="Requested classroom differs from current age-based classroom" class="flex items-center gap-1 text-xs text-amber-600 font-medium">
              <i data-lucide="alert-triangle" style="width:13px;height:13px;"></i> ${esc(e.currentBracket ? e.currentBracket.name : "Unclassified")}
            </span>` : ""}
            ${statusPillHtml(e.status)}
            <i data-lucide="chevron-right" style="width:16px;height:16px;" class="text-stone-300"></i>
          </div>
        </button>`).join("") + `</div>`;

  lucide.createIcons();
}

function setClassroomFilter(name) { state.classroomFilter = name; renderWaitlist(); }
function setSearch(v) { state.search = v; updateWaitlistView(); }
function toggleSort() { state.sortDir = state.sortDir === "asc" ? "desc" : "asc"; renderWaitlist(); }

async function syncWaitlistForm() {
  if (state.syncing) return;
  state.syncing = true;
  renderWaitlist();
  try {
    const data = await api("/entries/sync", { method: "POST" });
    state.lastSyncedAt = data.lastSyncedAt;
    await loadAll();
    const removedNote = data.removed > 0
      ? ` Removed ${data.removed} no longer in the sheet: ${data.removedNames.join(", ")}.`
      : "";
    toast(`Synced: ${data.added} new famil${data.added === 1 ? "y" : "ies"}${data.skipped > 0 ? ` (${data.skipped} already on file, skipped)` : ""}.${removedNote}`);
  } catch (err) {
    toast(`Couldn't sync form responses: ${err.message}`, "error");
  } finally {
    state.syncing = false;
    renderWaitlist();
  }
}

// ---------------------------------------------------------------------------
// Add-family modal
// ---------------------------------------------------------------------------

function labeledInputHtml(id, label, type = "text") {
  return `<label class="block text-sm">
    <span class="text-xs text-stone-500">${label}</span>
    <input id="${id}" type="${type}" class="mt-1 w-full px-3 py-2 border border-stone-300 rounded-lg text-sm" />
  </label>`;
}
function labeledTextareaHtml(id, label) {
  return `<label class="block text-sm">
    <span class="text-xs text-stone-500">${label}</span>
    <textarea id="${id}" rows="2" class="mt-1 w-full px-3 py-2 border border-stone-300 rounded-lg text-sm"></textarea>
  </label>`;
}

function openAddModal() {
  const root = document.getElementById("modal-root");
  root.innerHTML = `
    <div class="fixed inset-0 flex items-start sm:items-center justify-center z-50 p-4 overflow-y-auto" style="background-color: rgba(28,25,23,0.45);">
      <div class="bg-white rounded-2xl shadow-xl max-w-lg w-full my-8">
        <div class="flex items-center justify-between px-5 py-4 border-b border-stone-100">
          <h3 class="font-semibold text-stone-800">Add family to waitlist</h3>
          <button onclick="closeAddModal()" class="p-1.5 text-stone-400 hover:text-stone-700"><i data-lucide="x" style="width:18px;height:18px;"></i></button>
        </div>
        <div class="px-5 py-4 space-y-3 overflow-y-auto" style="max-height: 65vh;">
          ${labeledInputHtml("af-childName", "Child's name *")}
          <div class="grid grid-cols-2 gap-3">
            ${labeledInputHtml("af-dob", "Date of birth", "date")}
            ${labeledInputHtml("af-gender", "Gender")}
          </div>
          ${labeledInputHtml("af-parentName", "Parent's name")}
          <div class="grid grid-cols-2 gap-3">
            ${labeledInputHtml("af-phone", "Phone number")}
            ${labeledInputHtml("af-email", "Email address")}
          </div>
          <div class="grid grid-cols-2 gap-3">
            ${labeledInputHtml("af-classroom", "Requested classroom")}
            ${labeledInputHtml("af-preferredDate", "Preferred enrolment date", "date")}
          </div>
          ${labeledInputHtml("af-subsidy", "Requires subsidy?")}
          ${labeledTextareaHtml("af-questions", "Questions for us")}
          ${labeledTextareaHtml("af-notes", "Notes")}
        </div>
        <div class="flex items-center justify-end gap-3 px-5 py-4 border-t border-stone-100">
          <span id="af-busy-note" class="hidden flex items-center gap-1.5 text-xs text-stone-400 mr-auto">
            <i data-lucide="loader-2" class="animate-spin" style="width:13px;height:13px;"></i> Saving and syncing with Google Sheets…
          </span>
          <button id="af-cancel-btn" onclick="closeAddModal()" class="px-4 py-2 rounded-lg text-sm font-medium text-stone-600 hover:bg-stone-100">Cancel</button>
          <button id="af-save-btn" onclick="submitAddEntry()" class="px-4 py-2 rounded-lg text-sm font-medium text-white bg-teal-700 hover:bg-teal-800 disabled:opacity-60">Add to waitlist</button>
        </div>
      </div>
    </div>
  `;
  lucide.createIcons();
}
function closeAddModal() { document.getElementById("modal-root").innerHTML = ""; }

async function submitAddEntry() {
  const val = (id) => document.getElementById(id).value.trim();
  const childName = val("af-childName");
  if (!childName) { toast("Child's name is required.", "error"); return; }
  const payload = {
    childName, dob: val("af-dob"), gender: val("af-gender"),
    parentName: val("af-parentName"), phone: val("af-phone"), email: val("af-email"),
    requestedClassroom: val("af-classroom"), preferredDate: val("af-preferredDate"),
    subsidy: val("af-subsidy"), questions: val("af-questions"), notes: val("af-notes"),
  };
  const saveBtn = document.getElementById("af-save-btn");
  const cancelBtn = document.getElementById("af-cancel-btn");
  const busyNote = document.getElementById("af-busy-note");
  saveBtn.disabled = true;
  cancelBtn.disabled = true;
  busyNote.classList.remove("hidden");
  lucide.createIcons();
  try {
    const data = await api("/entries", { method: "POST", body: JSON.stringify(payload) });
    closeAddModal();
    await loadAll();
    if (data.syncWarnings && data.syncWarnings.length) {
      toast(`Added ${childName}, but: ${data.syncWarnings.join(" ")}`, "error");
    } else {
      toast(`Added ${childName} to the waitlist.`);
    }
  } catch (err) {
    toast(`Couldn't add family: ${err.message}`, "error");
    saveBtn.disabled = false;
    cancelBtn.disabled = false;
    busyNote.classList.add("hidden");
  }
}

// ---------------------------------------------------------------------------
// Entry detail modal
// ---------------------------------------------------------------------------

function openEntryModal(id) {
  state.selectedEntryId = id;
  renderEntryModal();
}
function closeEntryModal() {
  state.selectedEntryId = null;
  document.getElementById("modal-root").innerHTML = "";
}

function renderEntryModal() {
  const entry = state.entries.find((e) => e.id === state.selectedEntryId);
  if (!entry) return;
  const isBusy = state.busyEntryId === entry.id;
  const root = document.getElementById("modal-root");
  root.innerHTML = `
    <div class="fixed inset-0 flex items-start sm:items-center justify-center z-50 p-4 overflow-y-auto" style="background-color: rgba(28,25,23,0.45);">
      <div class="bg-white rounded-2xl shadow-xl max-w-lg w-full my-8 relative">
        ${isBusy ? `
        <div class="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-white/85 rounded-2xl">
          <i data-lucide="loader-2" class="animate-spin text-teal-700" style="width:26px;height:26px;"></i>
          <p class="text-sm text-stone-600">${esc(state.busyLabel)}</p>
          <p class="text-xs text-stone-400">Talking to Google Sheets — this can take a few seconds.</p>
        </div>` : ""}
        <div class="flex items-center justify-between px-5 py-4 border-b border-stone-100">
          <div>
            <h3 class="font-semibold text-stone-800">${esc(entry.childName)}</h3>
            <p class="text-xs text-stone-500">Added ${entry.timestamp ? new Date(entry.timestamp).toLocaleString() : "—"}</p>
          </div>
          <button onclick="closeEntryModal()" class="p-1.5 text-stone-400 hover:text-stone-700"><i data-lucide="x" style="width:18px;height:18px;"></i></button>
        </div>

        <div class="px-5 py-4 space-y-5 overflow-y-auto" style="max-height: 65vh;">
          <div>
            <p class="text-xs font-semibold text-stone-400 uppercase tracking-wide mb-2">Status</p>
            <div class="flex flex-wrap gap-2">
              ${STATUS_ORDER.map((key) => {
                const s = STATUS[key];
                const active = entry.status === key;
                return `<button data-status="${key}" onclick="setEntryStatus(this.dataset.status)"
                  class="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition ${active ? `${s.classes} ring-2 ring-offset-1 ring-teal-600` : "bg-white border-stone-200 text-stone-500 hover:border-stone-300"}">
                  <span class="w-1.5 h-1.5 rounded-full ${s.dot}"></span>${s.label}
                </button>`;
              }).join("")}
            </div>
          </div>

          <div class="grid grid-cols-2 gap-3 text-sm">
            <div><p class="text-xs text-stone-400">Parent</p><p class="text-stone-700 truncate">${esc(entry.parentName) || "—"}</p></div>
            <div><p class="text-xs text-stone-400">Phone</p><p class="text-stone-700 truncate"><a class="text-teal-700 hover:underline" href="tel:${esc(entry.phone)}">${esc(entry.phone) || "—"}</a></p></div>
            <div><p class="text-xs text-stone-400">Email</p><p class="text-stone-700 truncate"><a class="text-teal-700 hover:underline" href="mailto:${esc(entry.email)}">${esc(entry.email) || "—"}</a></p></div>
            <div><p class="text-xs text-stone-400">Gender</p><p class="text-stone-700 truncate">${esc(entry.gender) || "—"}</p></div>
            <div><p class="text-xs text-stone-400">Date of birth</p><p class="text-stone-700 truncate">${esc(entry.dob) || "—"}</p></div>
            <div><p class="text-xs text-stone-400">Current age</p><p class="text-stone-700 truncate">${formatAge(entry.ageMonths)}</p></div>
            <div><p class="text-xs text-stone-400">Requested classroom</p><p class="text-stone-700 truncate">${esc(entry.requestedClassroom) || "—"}</p></div>
            <div><p class="text-xs text-stone-400">Current age group</p><p class="text-stone-700 truncate ${entry.mismatch ? "text-amber-600 font-medium" : ""}">${esc(entry.currentBracket ? entry.currentBracket.name : "Unclassified")}${entry.mismatch ? " (differs from request)" : ""}</p></div>
            <div><p class="text-xs text-stone-400">Preferred start</p><p class="text-stone-700 truncate">${esc(entry.preferredDate) || "—"}</p></div>
            <div><p class="text-xs text-stone-400">Subsidy needed</p><p class="text-stone-700 truncate">${esc(entry.subsidy) || "—"}</p></div>
          </div>

          ${entry.questions ? `
          <div>
            <p class="text-xs font-semibold text-stone-400 uppercase tracking-wide mb-1">Questions from family</p>
            <p class="text-sm text-stone-700 bg-stone-50 rounded-lg p-3">${esc(entry.questions)}</p>
          </div>` : ""}

          <div>
            <p class="text-xs font-semibold text-stone-400 uppercase tracking-wide mb-2">Notes</p>
            <div class="flex gap-2 mb-3">
              <input id="note-input" placeholder="Add a note…" class="flex-1 px-3 py-2 border border-stone-300 rounded-lg text-sm"
                onkeydown="if(event.key==='Enter'){ submitNote(); }" />
              <button onclick="submitNote()" class="px-3 py-2 bg-teal-700 hover:bg-teal-800 text-white rounded-lg text-sm font-medium">Add</button>
            </div>
            <div class="space-y-2 overflow-y-auto" style="max-height: 10rem;">
              ${(entry.noteLog || []).length === 0 ? `<p class="text-sm text-stone-400">No notes yet.</p>` : ""}
              ${(entry.noteLog || []).map((n) => `
                <div class="text-sm bg-stone-50 rounded-lg px-3 py-2">
                  <p class="text-stone-700">${esc(n.text)}</p>
                  <p class="text-xs text-stone-400 mt-0.5">${new Date(n.ts).toLocaleString()}</p>
                </div>`).join("")}
            </div>
          </div>
        </div>

        <div class="flex items-center justify-between px-5 py-4 border-t border-stone-100">
          <button onclick="requestDeleteEntry(${entry.id})" class="text-sm text-red-600 hover:text-red-700 font-medium">Delete record</button>
          ${entry.archived
            ? `<button onclick="restoreEntry(${entry.id})" class="flex items-center gap-1.5 px-3.5 py-2 border border-stone-300 rounded-lg text-sm font-medium text-stone-700 hover:bg-stone-100">
                 <i data-lucide="rotate-ccw" style="width:15px;height:15px;"></i> Restore to active list
               </button>`
            : `<button onclick="archiveEntryAction(${entry.id})" class="flex items-center gap-1.5 px-3.5 py-2 bg-stone-800 hover:bg-stone-900 text-white rounded-lg text-sm font-medium">
                 <i data-lucide="archive" style="width:15px;height:15px;"></i> Archive family
               </button>`}
        </div>
      </div>
    </div>
  `;
  lucide.createIcons();
}

async function setEntryStatus(status) {
  const id = state.selectedEntryId;
  setEntryBusy(id, "Updating status…");
  renderEntryModal();
  try {
    const data = await api(`/entries/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    await loadAll();
    if (data.syncWarnings && data.syncWarnings.length) {
      toast(`Status updated, but: ${data.syncWarnings.join(" ")}`, "error");
    }
  } catch (err) {
    toast(`Couldn't update status: ${err.message}`, "error");
  } finally {
    clearEntryBusy();
    if (state.selectedEntryId != null) renderEntryModal();
  }
}

async function submitNote() {
  const input = document.getElementById("note-input");
  const text = input.value.trim();
  if (!text) return;
  const id = state.selectedEntryId;
  try {
    await api(`/entries/${id}/notes`, { method: "POST", body: JSON.stringify({ text }) });
    await loadAll();
  } catch (err) {
    toast(`Couldn't add note: ${err.message}`, "error");
  }
}

async function archiveEntryAction(id) {
  setEntryBusy(id, "Archiving…");
  renderEntryModal();
  try {
    const data = await api(`/entries/${id}/archive`, { method: "POST" });
    closeEntryModal();
    await loadAll();
    if (data.syncWarnings && data.syncWarnings.length) {
      toast(`Family archived, but: ${data.syncWarnings.join(" ")}`, "error");
    } else {
      toast("Family archived.");
    }
  } catch (err) {
    toast(`Couldn't archive: ${err.message}`, "error");
  } finally {
    clearEntryBusy();
    if (state.selectedEntryId != null) renderEntryModal();
  }
}

async function restoreEntry(id) {
  setEntryBusy(id, "Restoring…");
  if (state.selectedEntryId === id) renderEntryModal();
  updateArchiveView();
  try {
    const data = await api(`/entries/${id}/restore`, { method: "POST" });
    closeEntryModal();
    await loadAll();
    if (data.syncWarnings && data.syncWarnings.length) {
      toast(`Restored, but: ${data.syncWarnings.join(" ")}`, "error");
    } else {
      toast("Restored to active list.");
    }
  } catch (err) {
    toast(`Couldn't restore: ${err.message}`, "error");
  } finally {
    clearEntryBusy();
    if (state.selectedEntryId != null) renderEntryModal();
    if (state.activeTab === "archive") updateArchiveView();
  }
}

function requestDeleteEntry(id) {
  const entry = state.entries.find((e) => e.id === id);
  if (!entry) return;
  showConfirm({
    title: "Delete this record?",
    message: `This permanently removes ${entry.childName} from the system. This can't be undone.`,
    danger: true,
    confirmLabel: "Delete",
    onConfirm: async () => {
      try {
        await api(`/entries/${id}`, { method: "DELETE" });
        closeConfirm();
        closeEntryModal();
        await loadAll();
        toast("Record deleted.");
      } catch (err) {
        toast(`Couldn't delete: ${err.message}`, "error");
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Archive tab
// ---------------------------------------------------------------------------

function renderArchive() {
  const panel = document.getElementById("panel-archive");
  panel.innerHTML = `
    <div class="space-y-5">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 class="text-lg font-semibold text-stone-800">Archive</h2>
          <p class="text-sm text-stone-500">Families offered a spot (accepted or declined). Kept for licensing's ${RETENTION_YEARS}-year retention rule.</p>
        </div>
        <div class="flex items-center gap-2">
          <button onclick="exportArchive()" class="flex items-center gap-1.5 px-3.5 py-2 border border-stone-300 text-stone-700 hover:bg-stone-100 rounded-lg text-sm font-medium">
            <i data-lucide="download" style="width:15px;height:15px;"></i> Export archive (CSV)
          </button>
          <button onclick="syncArchiveSheet()" ${state.archiveSyncing ? "disabled" : ""} class="flex items-center gap-1.5 px-3.5 py-2 bg-stone-800 hover:bg-stone-900 disabled:opacity-60 text-white rounded-lg text-sm font-medium">
            <i data-lucide="refresh-cw" style="width:15px;height:15px;" class="${state.archiveSyncing ? "animate-spin" : ""}"></i> ${state.archiveSyncing ? "Syncing…" : "Sync archive sheet"}
          </button>
        </div>
      </div>

      <div class="relative max-w-xs">
        <i data-lucide="search" style="width:15px;height:15px;" class="absolute left-3 top-1/2 -translate-y-1/2 text-stone-400"></i>
        <input value="${esc(state.archiveSearch)}" oninput="setArchiveSearch(this.value)" placeholder="Search archive"
          class="w-full pl-8 pr-3 py-2 border border-stone-300 rounded-lg text-sm" />
      </div>

      <div id="ar-rows"></div>
    </div>
  `;
  updateArchiveView();
}

function updateArchiveView() {
  const filtered = archivedEntries()
    .filter((e) => (e.childName + " " + e.parentName).toLowerCase().includes(state.archiveSearch.toLowerCase()))
    .sort((a, b) => new Date(b.archivedAt || 0) - new Date(a.archivedAt || 0));

  document.getElementById("ar-rows").innerHTML = filtered.length === 0
    ? emptyStateHtml("archive", "Archive is empty", "Families appear here once you archive them after an offer has been made.")
    : `<div class="bg-white border border-stone-200 rounded-2xl divide-y divide-stone-100 overflow-hidden">` +
      filtered.map((e) => {
        const archivedDate = e.archivedAt ? new Date(e.archivedAt) : null;
        const eligibleDate = archivedDate ? new Date(archivedDate.getFullYear() + RETENTION_YEARS, archivedDate.getMonth(), archivedDate.getDate()) : null;
        const isBusy = state.busyEntryId === e.id;
        return `
        <div class="px-4 py-3.5 flex flex-wrap items-center justify-between gap-3">
          <div class="min-w-0 cursor-pointer" onclick="openEntryModal(${e.id})">
            <p class="font-medium text-stone-800">${esc(e.childName)}</p>
            <p class="text-sm text-stone-500">${esc(e.parentName)} · Archived ${archivedDate ? archivedDate.toLocaleDateString() : "—"} · Eligible to delete ${eligibleDate ? eligibleDate.toLocaleDateString() : "—"}</p>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            ${isBusy ? `<span class="flex items-center gap-1.5 text-xs text-stone-400 pr-1">
              <i data-lucide="loader-2" class="animate-spin" style="width:13px;height:13px;"></i> Restoring…
            </span>` : statusPillHtml(e.status)}
            <button onclick="restoreEntry(${e.id})" ${isBusy ? "disabled" : ""} class="p-2 text-stone-500 hover:text-teal-700 hover:bg-teal-50 disabled:opacity-40 rounded-lg" title="Restore to active list">
              <i data-lucide="rotate-ccw" style="width:15px;height:15px;"></i>
            </button>
            <button onclick="requestDeleteArchived(${e.id}, ${eligibleDate ? `'${eligibleDate.toISOString()}'` : "null"})" ${isBusy ? "disabled" : ""} class="p-2 text-stone-500 hover:text-red-700 hover:bg-red-50 disabled:opacity-40 rounded-lg" title="Delete permanently">
              <i data-lucide="trash-2" style="width:15px;height:15px;"></i>
            </button>
          </div>
        </div>`;
      }).join("") + `</div>`;
  lucide.createIcons();
}

function setArchiveSearch(v) { state.archiveSearch = v; updateArchiveView(); }

function exportArchive() {
  window.location.href = "/api/archive/export";
}

async function syncArchiveSheet() {
  if (state.archiveSyncing) return;
  state.archiveSyncing = true;
  renderArchive();
  try {
    const data = await api("/archive/sync", { method: "POST" });
    await loadAll();
    const removedNote = data.removed > 0
      ? ` Removed ${data.removed} no longer in the archive sheet: ${data.removedNames.join(", ")}.`
      : "";
    toast(`Archive synced: ${data.added} new, ${data.skipped} already on file.${removedNote}`);
  } catch (err) {
    toast(`Couldn't sync archive sheet: ${err.message}`, "error");
  } finally {
    state.archiveSyncing = false;
    renderArchive();
  }
}

function requestDeleteArchived(id, eligibleIso) {
  const eligibleDate = eligibleIso ? new Date(eligibleIso) : null;
  const beforeEligible = eligibleDate && new Date() < eligibleDate;
  showConfirm({
    title: "Delete this record permanently?",
    message: beforeEligible
      ? `Licensing requires ${RETENTION_YEARS} years of waitlist history — this record isn't eligible until ${eligibleDate.toLocaleDateString()}. Delete anyway?`
      : "This permanently removes the record. This can't be undone.",
    danger: true,
    confirmLabel: "Delete",
    onConfirm: async () => {
      try {
        await api(`/entries/${id}`, { method: "DELETE" });
        closeConfirm();
        await loadAll();
        toast("Record deleted.");
      } catch (err) {
        toast(`Couldn't delete: ${err.message}`, "error");
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Settings tab
// ---------------------------------------------------------------------------

function renderSettings() {
  const panel = document.getElementById("panel-settings");
  panel.innerHTML = `
    <div class="space-y-8 max-w-2xl">
      <div>
        <h2 class="text-lg font-semibold text-stone-800">Age groups</h2>
        <p class="text-sm text-stone-500 mb-4">
          Used to sort each waitlisted child by their <span class="font-medium">current</span> age, not their age at registration.
          These cutoffs are placeholders — confirm the real ones with Erin before relying on this for real decisions.
        </p>
        <div class="bg-white border border-stone-200 rounded-2xl divide-y divide-stone-100">
          <div class="flex items-center gap-3 px-4 py-2.5 text-xs font-semibold text-stone-400 uppercase tracking-wide">
            <span class="flex-1">Classroom name</span><span class="w-20">Min (mo)</span><span class="w-20">Max (mo)</span><span class="w-6"></span>
          </div>
          <div id="bracket-rows"></div>
        </div>
        <button onclick="addBracket()" class="mt-3 flex items-center gap-1.5 text-sm font-medium text-teal-700 hover:text-teal-800">
          <i data-lucide="plus" style="width:15px;height:15px;"></i> Add age group
        </button>
      </div>

      <div>
        <h2 class="text-lg font-semibold text-stone-800">Openings look-ahead</h2>
        <p class="text-sm text-stone-500 mb-3">How many days ahead the Openings tab should scan for graduations by default.</p>
        <input type="number" min="7" max="365" value="${state.lookaheadDays}" onchange="setLookahead(this.value)"
          class="w-28 px-2.5 py-1.5 border border-stone-300 rounded-lg text-sm" />
      </div>

      <div>
        <h2 class="text-lg font-semibold text-stone-800">Waitlist form sheet</h2>
        <p class="text-sm text-stone-500 mb-3">
          Where waitlist form responses sync from. If the Google Form ever moves to a new
          Sheet or tab, paste its link here — no need to touch any code.
          Open the sheet, copy the address bar, and paste the whole thing below.
        </p>
        <div class="flex items-center gap-2">
          <input id="sheet-url-input" value="${esc(state.sheetUrl)}" placeholder="https://docs.google.com/spreadsheets/d/.../edit?gid=..."
            class="flex-1 px-2.5 py-1.5 border border-stone-300 rounded-lg text-sm" />
          <button onclick="saveSheetUrl()" class="px-3.5 py-1.5 bg-teal-700 hover:bg-teal-800 text-white rounded-lg text-sm font-medium">
            Save
          </button>
        </div>
        <p class="text-xs text-stone-400 mt-2">
          Remember to share that sheet with the sync service account (<span class="font-medium">Editor</span> access —
          archiving removes rows from it) or syncing will fail.
        </p>
      </div>

      <div>
        <h2 class="text-lg font-semibold text-stone-800">Archive sheet <span class="font-normal text-stone-400">(optional)</span></h2>
        <p class="text-sm text-stone-500 mb-3">
          When you archive a family here, their row is removed from the waitlist form sheet above.
          If you'd like a permanent copy kept somewhere, paste a Google Sheet link below — each
          archived family gets appended there as a new row.
        </p>
        <div class="flex items-center gap-2">
          <input id="archive-sheet-url-input" value="${esc(state.archiveSheetUrl)}" placeholder="https://docs.google.com/spreadsheets/d/.../edit?gid=..."
            class="flex-1 px-2.5 py-1.5 border border-stone-300 rounded-lg text-sm" />
          <button onclick="saveArchiveSheetUrl()" class="px-3.5 py-1.5 bg-teal-700 hover:bg-teal-800 text-white rounded-lg text-sm font-medium">
            Save
          </button>
        </div>
        <p class="text-xs text-stone-400 mt-2">
          Needs Editor access for the service account too. Leave blank to skip keeping a Sheet copy —
          archived families still stay in this app either way.
        </p>
      </div>

      <div>
        <h2 class="text-lg font-semibold text-stone-800">Record retention</h2>
        <p class="text-sm text-stone-500">
          Archived families are kept for <span class="font-medium">${RETENTION_YEARS} years</span> per licensing requirements before
          they're eligible for deletion. This isn't editable here — it's a compliance rule, not a setting.
        </p>
      </div>

      <div class="pt-4 border-t border-stone-200">
        <h2 class="text-sm font-semibold text-red-700 mb-2">Danger zone</h2>
        <button onclick="requestResetAll()" class="px-3.5 py-2 border border-red-300 text-red-700 hover:bg-red-50 rounded-lg text-sm font-medium">
          Clear all data
        </button>
      </div>
    </div>
  `;
  renderBracketRows();
  lucide.createIcons();
}

function renderBracketRows() {
  const container = document.getElementById("bracket-rows");
  const sorted = [...state.brackets].sort((a, b) => a.minMonths - b.minMonths);
  container.innerHTML = sorted.map((b) => `
    <div class="flex items-center gap-3 px-4 py-2.5">
      <input value="${esc(b.name)}" onchange="updateBracketField(${b.id}, 'name', this.value)" class="flex-1 px-2.5 py-1.5 border border-stone-300 rounded-lg text-sm" />
      <input type="number" value="${b.minMonths}" onchange="updateBracketField(${b.id}, 'minMonths', Number(this.value))" class="w-20 px-2.5 py-1.5 border border-stone-300 rounded-lg text-sm" />
      <input type="number" value="${b.maxMonths}" onchange="updateBracketField(${b.id}, 'maxMonths', Number(this.value))" class="w-20 px-2.5 py-1.5 border border-stone-300 rounded-lg text-sm" />
      <button onclick="removeBracket(${b.id})" class="w-6 text-stone-400 hover:text-red-600"><i data-lucide="x" style="width:15px;height:15px;"></i></button>
    </div>
  `).join("");
  lucide.createIcons();
}

async function updateBracketField(id, field, value) {
  try {
    await api(`/brackets/${id}`, { method: "PATCH", body: JSON.stringify({ [field]: value }) });
    await loadAll();
  } catch (err) {
    toast(`Couldn't update: ${err.message}`, "error");
  }
}

async function addBracket() {
  try {
    await api("/brackets", { method: "POST", body: JSON.stringify({ name: "New group", minMonths: 0, maxMonths: 12 }) });
    await loadAll();
  } catch (err) {
    toast(`Couldn't add group: ${err.message}`, "error");
  }
}

async function removeBracket(id) {
  try {
    await api(`/brackets/${id}`, { method: "DELETE" });
    await loadAll();
  } catch (err) {
    toast(`Couldn't remove group: ${err.message}`, "error");
  }
}

function requestResetAll() {
  showConfirm({
    title: "Clear all data?",
    message: "This removes every waitlist entry, the imported roster, and resets age-group settings to defaults. Meant for demo/testing only.",
    danger: true,
    confirmLabel: "Clear everything",
    onConfirm: async () => {
      try {
        await api("/reset", { method: "POST" });
        closeConfirm();
        await loadAll();
        toast("All data cleared.");
      } catch (err) {
        toast(`Couldn't reset: ${err.message}`, "error");
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Confirm dialog
// ---------------------------------------------------------------------------

function showConfirm({ title, message, confirmLabel = "Confirm", danger, onConfirm }) {
  const root = document.getElementById("confirm-root");
  root.innerHTML = `
    <div class="fixed inset-0 flex items-center justify-center z-50 p-4" style="background-color: rgba(28,25,23,0.45);">
      <div class="bg-white rounded-2xl shadow-xl max-w-sm w-full p-6">
        <h3 class="text-lg font-semibold text-stone-800 mb-2">${esc(title)}</h3>
        <p class="text-sm text-stone-600 mb-6">${esc(message)}</p>
        <div class="flex justify-end gap-3">
          <button id="confirm-cancel-btn" class="px-4 py-2 rounded-lg text-sm font-medium text-stone-600 hover:bg-stone-100">Cancel</button>
          <button id="confirm-ok-btn" class="px-4 py-2 rounded-lg text-sm font-medium text-white ${danger ? "bg-red-600 hover:bg-red-700" : "bg-teal-700 hover:bg-teal-800"}">${esc(confirmLabel)}</button>
        </div>
      </div>
    </div>
  `;
  document.getElementById("confirm-cancel-btn").onclick = closeConfirm;
  document.getElementById("confirm-ok-btn").onclick = onConfirm;
}

function closeConfirm() {
  document.getElementById("confirm-root").innerHTML = "";
}
