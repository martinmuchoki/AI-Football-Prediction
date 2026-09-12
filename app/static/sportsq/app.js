const $ = (id) => document.getElementById(id);

let currentPredictions = [];

function authHeaders() {
  const key = $("apiKey").value.trim();
  const header = $("apiHeader").value.trim();
  const headers = {};
  if (key && header) headers[header] = key;
  return headers;
}

async function api(path, options = {}) {
  const headers = {
    ...authHeaders(),
    ...(options.body ? {"Content-Type": "application/json"} : {}),
    ...(options.headers || {})
  };

  const response = await fetch(path, {...options, headers});
  const text = await response.text();
  let data;

  try {
    data = JSON.parse(text);
  } catch {
    data = {raw: text};
  }

  if (!response.ok) {
    const error = new Error(`HTTP ${response.status}`);
    error.payload = data;
    throw error;
  }

  return data;
}

function setApiState(ok, text) {
  const node = $("apiState");
  node.textContent = text;
  node.className = "pill " + (ok ? "status-ready" : "status-warn");
}

function formatKickoff(value) {
  if (!value) return "Kickoff unavailable";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);

  return date.toLocaleString([], {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function confidenceBand(confidence) {
  const raw = String(confidence?.band || "").toUpperCase();
  if (raw === "HIGH" || raw === "MEDIUM" || raw === "LOW") return raw;

  const value = Number(confidence?.percent);
  if (!Number.isFinite(value)) return "LOW";
  if (value >= 65) return "HIGH";
  if (value >= 55) return "MEDIUM";
  return "LOW";
}

function bandClass(band) {
  if (band === "HIGH") return "band-high";
  if (band === "MEDIUM") return "band-medium";
  return "band-low";
}

function predictionCard(item, index) {
  const fixture = item.fixture || {};
  const predict = item.sportsq_predict || {};
  const confidence = item.sportsq_confidence || {};
  const score = item.sportsq_score_call || {};
  const form = item.sportsq_form_index || {};

  const homeForm = form.home?.index ?? "—";
  const awayForm = form.away?.index ?? "—";
  const band = confidenceBand(confidence);
  const fixtureId = fixture.fixture_id;

  return `
    <article class="prediction-card">
      <div>
        <div class="kicker">LOCKED PRE-MATCH</div>
        <div class="match-name">${fixture.home_team || "Home"} vs ${fixture.away_team || "Away"}</div>
        <div class="kickoff">${formatKickoff(fixture.kickoff_utc)}</div>
      </div>

      <div>
        <div class="kicker">SPORTSQ PREDICT</div>
        <div class="value">${predict.prediction || "—"}</div>
      </div>

      <div>
        <div class="kicker">CONFIDENCE</div>
        <div class="value">${confidence.percent != null ? confidence.percent + "%" : "—"}</div>
        <span class="confidence-badge ${bandClass(band)}">${band}</span>
      </div>

      <div>
        <div class="kicker">SCORECALL</div>
        <div class="value">${score.score || "—"}</div>
        <small class="muted">${score.score ? "Locked score data" : "Not exposed by lock"}</small>
      </div>

      <div>
        <div class="kicker">FORM INDEX</div>
        <div class="value">${homeForm} / ${awayForm}</div>
        <small class="muted">Home / Away</small>
      </div>

      <div class="match-actions">
        <button class="small" onclick="generateFixtureContent(${fixtureId})">Generate</button>
        <button class="secondary small" onclick="showPredictionDetail(${index})">Details</button>
      </div>
    </article>
  `;
}

function renderAccuracy(accuracy) {
  const summary = accuracy?.performance && typeof accuracy.performance === "object"
    ? accuracy.performance
    : (accuracy || {});

  const locked = summary.locked ?? summary.total_locked ?? "—";
  const graded = summary.graded ?? summary.graded_count ?? 0;
  const pending = summary.pending ?? summary.pending_count ??
    (Number.isFinite(Number(locked)) && Number.isFinite(Number(graded))
      ? Math.max(0, Number(locked) - Number(graded))
      : "—");

  let value = summary.accuracy ??
    summary.accuracy_pct ??
    summary.accuracy_percent ??
    summary.hit_rate ??
    summary.correct_rate;

  if (typeof value === "number" && value <= 1) value *= 100;

  $("accuracyLocked").textContent = locked;
  $("accuracyGraded").textContent = graded;
  $("accuracyPending").textContent = pending;
  $("accuracyPercent").textContent =
    typeof value === "number" ? `${value.toFixed(1)}%` : "—";

  $("accuracyMessage").textContent =
    Number(graded) > 0
      ? "Accuracy is calculated only from completed, graded predictions that were locked before kickoff."
      : "Accuracy will appear after locked predictions have completed and been graded.";

  $("accuracyBox").textContent = JSON.stringify(summary, null, 2);
}

async function loadDashboard() {
  try {
    const [status, intelligence, accuracy] = await Promise.all([
      api("/api/v1/sportsq/stage3/status"),
      api("/api/v1/sportsq/intelligence?competition=2&season=2026&limit=8"),
      api("/api/v1/sportsq/accuracy?competition=2&season=2026")
    ]);

    setApiState(true, "API connected");

    currentPredictions = intelligence.items || [];

    $("predictStatus").textContent =
      status.prediction_safety?.existing_prediction_locks_immutable
        ? "ACTIVE"
        : "CHECK";

    const hasScore = currentPredictions.some(
      item => Boolean(item.sportsq_score_call?.score)
    );

    $("scoreStatus").textContent =
      hasScore ? "ACTIVE" : "AWAITING DATA";

    $("confidenceStatus").textContent = "ACTIVE";
    $("formStatus").textContent = "ACTIVE";

    $("predictionTable").innerHTML =
      currentPredictions.map(predictionCard).join("") ||
      '<p class="muted">No locked predictions available.</p>';

    renderAccuracy(accuracy);
    renderConnections(status.publishing?.platforms || {});
  } catch (error) {
    setApiState(false, "API authentication/config required");
    $("predictionTable").innerHTML =
      '<p class="muted">Enter the existing API-key header name and key if your own-api guard is enabled, then click Refresh.</p>';
  }
}

function detailCell(label, value) {
  return `
    <div class="detail-cell">
      <span>${label}</span>
      <strong>${value ?? "—"}</strong>
    </div>
  `;
}

function showPredictionDetail(index) {
  const item = currentPredictions[index];
  if (!item) return;

  const fixture = item.fixture || {};
  const predict = item.sportsq_predict || {};
  const confidence = item.sportsq_confidence || {};
  const score = item.sportsq_score_call || {};
  const form = item.sportsq_form_index || {};
  const news = item.sportsq_news_impact || {};

  $("detailTitle").textContent =
    `${fixture.home_team || "Home"} vs ${fixture.away_team || "Away"}`;

  $("detailBody").innerHTML = `
    <div class="detail-grid">
      ${detailCell("Kickoff", formatKickoff(fixture.kickoff_utc))}
      ${detailCell("SportsQ Predict", predict.prediction)}
      ${detailCell("Confidence", confidence.percent != null ? confidence.percent + "%" : "—")}
      ${detailCell("Confidence Band", confidenceBand(confidence))}
      ${detailCell("ScoreCall", score.score || "Not exposed")}
      ${detailCell("ScoreCall Status", score.status)}
      ${detailCell("Home Form Index", form.home?.index)}
      ${detailCell("Away Form Index", form.away?.index)}
      ${detailCell("SportsQ News Impact", news.status || "UNVERIFIED")}
      ${detailCell("News Verified", news.verified === true ? "YES" : "NO")}
      ${detailCell("Lock Source", item.lock?.immutable_source === true ? "IMMUTABLE" : "PRE-MATCH")}
      ${detailCell("Fixture ID", fixture.fixture_id)}
    </div>
  `;

  const modal = $("detailModal");
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
}

function closeDetailModal() {
  const modal = $("detailModal");
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
}

async function generateFixtureContent(fixtureId) {
  if (!fixtureId) return;

  $("generationStatus").textContent =
    `Generating content for fixture ${fixtureId}…`;

  try {
    const result = await api(
      `/api/v1/sportsq/stage3/generate/${fixtureId}`,
      {
        method: "POST",
        body: JSON.stringify({
          competition: 2,
          season: 2026
        })
      }
    );

    if (result.status !== "success") {
      $("generationStatus").textContent =
        `Fixture content generation did not complete: ${result.reason || result.status}`;
      return;
    }

    $("generationStatus").textContent =
      `Package ${result.package_id} generated for fixture ${fixtureId}.`;

    switchView("content");
    renderAssets(result);
  } catch (error) {
    $("generationStatus").textContent =
      `Fixture content generation failed: ${error.message}`;
  }
}

function renderConnections(platforms) {
  $("connectionsList").innerHTML = Object.entries(platforms).map(([name, state]) => {
    const cls = state.live_posting_verified ? "status-ready" :
      state.config_present ? "status-warn" : "status-bad";

    return `
      <article class="connection-card">
        <strong>${name}</strong>
        <div class="${cls}">${state.status}</div>
        <p class="muted">
          Live posting verified: ${state.live_posting_verified ? "YES" : "NO"}
        </p>
      </article>
    `;
  }).join("");
}

async function generateContent() {
  const payload = {
    competition: Number($("competition").value || 2),
    season: Number($("season").value || 2026),
    limit: Number($("limit").value || 3)
  };

  $("generationStatus").textContent = "Generating…";

  try {
    const result = await api("/api/v1/sportsq/stage3/generate", {
      method: "POST",
      body: JSON.stringify(payload)
    });

    $("generationStatus").textContent =
      `Package ${result.package_id} generated with ${result.assets.length} assets.`;

    renderAssets(result);
    await loadQueue();
  } catch (error) {
    $("generationStatus").textContent =
      `Generation failed: ${error.message}`;
  }
}

function renderAssets(pkg) {
  const imageKinds = new Set([
    "MATCH_PREDICTION",
    "VERTICAL_STORY_REEL",
    "RESULTS_ACCURACY",
    "BREAKING_TEAM_NEWS"
  ]);

  $("assetGallery").innerHTML = (pkg.assets || []).map(asset => {
    const url =
      `/sportsq/content/${pkg.package_id}/${encodeURIComponent(asset.filename)}`;

    if (imageKinds.has(asset.kind)) {
      return `
        <figure>
          <img src="${url}" alt="${asset.kind}">
          <figcaption>${asset.kind}</figcaption>
        </figure>
      `;
    }

    if (asset.kind === "STATIC_STORY_VIDEO") {
      return `
        <figure>
          <video controls src="${url}"></video>
          <figcaption>STATIC STORY VIDEO</figcaption>
        </figure>
      `;
    }

    return "";
  }).join("");
}

async function loadQueue() {
  try {
    const rows = await api("/api/v1/sportsq/stage3/queue");

    $("queueList").innerHTML = rows.map(row => `
      <article class="queue-card">
        <div class="kicker">${row.status}</div>
        <div class="value">${row.package_id}</div>
        <p class="muted">Platforms: ${(row.platforms || []).join(", ")}</p>
        <p class="muted">Scheduled: ${row.scheduled_for || "Not scheduled"}</p>
        <div class="queue-actions">
          ${row.status === "DRAFT" ? `<button onclick="approveItem('${row.queue_id}')">Approve</button>` : ""}
          ${row.status === "APPROVED" ? `<button onclick="processItem('${row.queue_id}')">Process</button>` : ""}
          ${!["CANCELLED","EXPORTED","PUBLISHED"].includes(row.status) ? `<button class="secondary" onclick="cancelItem('${row.queue_id}')">Cancel</button>` : ""}
        </div>
        <pre>${JSON.stringify(row.platform_results || {}, null, 2)}</pre>
      </article>
    `).join("") || '<p class="muted">Queue is empty.</p>';
  } catch (error) {
    $("queueList").innerHTML =
      '<p class="muted">Queue unavailable until API authentication succeeds.</p>';
  }
}

async function approveItem(id) {
  await api(`/api/v1/sportsq/stage3/queue/${id}/approve`, {method: "POST"});
  await loadQueue();
}

async function cancelItem(id) {
  await api(`/api/v1/sportsq/stage3/queue/${id}/cancel`, {method: "POST"});
  await loadQueue();
}

async function processItem(id) {
  await api(`/api/v1/sportsq/stage3/queue/${id}/process`, {method: "POST"});
  await loadQueue();
}

function switchView(target) {
  document.querySelectorAll(".nav").forEach(x => {
    x.classList.toggle("active", x.dataset.target === target);
  });

  document.querySelectorAll(".view").forEach(x => {
    x.classList.toggle("active", x.id === target);
  });

  if (target === "publishing") loadQueue();
}

document.querySelectorAll(".nav").forEach(button => {
  button.addEventListener("click", () => {
    switchView(button.dataset.target);
  });
});

document.querySelectorAll("[data-close-modal]").forEach(node => {
  node.addEventListener("click", closeDetailModal);
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape") closeDetailModal();
});

$("saveAuth").addEventListener("click", () => {
  sessionStorage.setItem("sportsq_api_header", $("apiHeader").value);
  sessionStorage.setItem("sportsq_api_key", $("apiKey").value);
  loadDashboard();
});

$("refreshDashboard").addEventListener("click", loadDashboard);
$("generateContent").addEventListener("click", generateContent);
$("refreshQueue").addEventListener("click", loadQueue);

$("apiHeader").value =
  sessionStorage.getItem("sportsq_api_header") || "X-API-Key";

$("apiKey").value =
  sessionStorage.getItem("sportsq_api_key") || "";

window.approveItem = approveItem;
window.cancelItem = cancelItem;
window.processItem = processItem;
window.showPredictionDetail = showPredictionDetail;
window.generateFixtureContent = generateFixtureContent;

loadDashboard();
loadQueue();

