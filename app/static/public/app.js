"use strict";

const $ = (selector) => document.querySelector(selector);

const grid = $("#match-grid");
const statusBox = $("#status");
const competitionInput = $("#competition");
const seasonInput = $("#season");
const refreshButton = $("#refresh");


function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


function display(value, fallback = "Pending") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }

  return String(value);
}


function formatKickoff(value) {
  if (!value) {
    return "Kickoff pending";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return display(value);
  }

  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}


function confidenceText(confidence) {
  if (!confidence) {
    return "Pending";
  }

  const percent =
    confidence.percent === null ||
    confidence.percent === undefined
      ? null
      : Number(confidence.percent);

  const band = display(confidence.band, "");

  if (percent === null || Number.isNaN(percent)) {
    return band || "Pending";
  }

  return `${percent.toFixed(1)}%${band ? ` · ${band}` : ""}`;
}


function formMarkup(form) {
  if (!form) {
    return `<p class="news-note">Form data pending.</p>`;
  }

  const home = form.home || {};
  const away = form.away || {};

  return `
    <div class="form-row">
      <span>${escapeHtml(display(home.team, "Home"))}</span>
      <strong>${escapeHtml(display(home.index, "—"))}</strong>
    </div>

    <div class="form-row">
      <span>${escapeHtml(display(away.team, "Away"))}</span>
      <strong>${escapeHtml(display(away.index, "—"))}</strong>
    </div>

    <div class="news-meta">
      Recent ${escapeHtml(display(form.window, "—"))}-match window
    </div>
  `;
}


function newsMarkup(news) {
  if (!news) {
    return `<p class="news-note">News impact pending.</p>`;
  }

  const note = display(news.note, "No verified news-impact note available.");
  const direction = display(news.direction, "neutral");

  return `
    <p class="news-note">${escapeHtml(note)}</p>

    <div class="news-meta">
      Direction: ${escapeHtml(direction)}
      ${news.verified === true ? " · Verified" : ""}
    </div>
  `;
}


function cardMarkup(item) {
  const fixture = item.fixture || {};
  const predict = item.sportsq_predict || {};
  const scoreCall = item.sportsq_score_call || {};
  const confidence = item.sportsq_confidence || {};

  return `
    <article class="match-card">

      <div class="match-top">

        <div class="kickoff">
          ${escapeHtml(formatKickoff(fixture.kickoff_utc))}
        </div>

        <div class="teams">
          <span>${escapeHtml(display(fixture.home_team, "Home"))}</span>
          <span class="versus">VS</span>
          <span>${escapeHtml(display(fixture.away_team, "Away"))}</span>
        </div>

      </div>


      <div class="prediction-strip">

        <div class="metric">
          <span>SportsQ Predict</span>
          <strong class="lime">
            ${escapeHtml(display(predict.prediction))}
          </strong>
        </div>

        <div class="metric">
          <span>ScoreCall</span>
          <strong>
            ${escapeHtml(display(scoreCall.score))}
          </strong>
        </div>

        <div class="metric">
          <span>Confidence</span>
          <strong>
            ${escapeHtml(confidenceText(confidence))}
          </strong>
        </div>

      </div>


      <div class="detail-grid">

        <div class="detail">
          <h4>SportsQ Form Index</h4>
          ${formMarkup(item.sportsq_form_index)}
        </div>

        <div class="detail">
          <h4>SportsQ News Impact</h4>
          ${newsMarkup(item.sportsq_news_impact)}
        </div>

      </div>

    </article>
  `;
}


async function loadMatches() {
  const competition = Number(competitionInput.value);
  const season = Number(seasonInput.value);

  if (!Number.isInteger(competition) || competition < 1) {
    statusBox.textContent = "Enter a valid competition.";
    statusBox.classList.add("error");
    return;
  }

  if (!Number.isInteger(season) || season < 2000 || season > 2100) {
    statusBox.textContent = "Enter a valid season.";
    statusBox.classList.add("error");
    return;
  }

  statusBox.classList.remove("error");
  statusBox.textContent = "Loading SportsQ intelligence...";
  grid.innerHTML = "";
  refreshButton.disabled = true;

  try {
    const params = new URLSearchParams({
      competition: String(competition),
      season: String(season),
      limit: "40",
    });

    const response = await fetch(
      `/api/v1/public/sportsq?${params.toString()}`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    );

    if (!response.ok) {
      throw new Error(`Request failed (${response.status})`);
    }

    const payload = await response.json();
    const items = Array.isArray(payload.items) ? payload.items : [];

    if (!items.length) {
      statusBox.textContent = "No published SportsQ intelligence is currently available.";

      grid.innerHTML = `
        <div class="empty">
          No match intelligence is available for this competition and season yet.
        </div>
      `;

      return;
    }

    statusBox.textContent =
      `${items.length} match${items.length === 1 ? "" : "es"} available`;

    grid.innerHTML = items.map(cardMarkup).join("");

  } catch (error) {
    console.error(error);

    statusBox.textContent =
      "SportsQ intelligence is temporarily unavailable.";

    statusBox.classList.add("error");

    grid.innerHTML = `
      <div class="empty">
        Please try again shortly.
      </div>
    `;

  } finally {
    refreshButton.disabled = false;
  }
}


refreshButton.addEventListener("click", loadMatches);

loadMatches();
