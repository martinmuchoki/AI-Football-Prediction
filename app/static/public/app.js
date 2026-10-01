"use strict";

(() => {
  const DEFAULT_COMPETITION = 39;
  const DEFAULT_SEASON = 2026;
  const DEFAULT_LIMIT = 40;

  const competitionControl = document.getElementById("competition");
  const seasonControl = document.getElementById("season");
  const refreshButton =
    document.getElementById("refresh") ||
    document.getElementById("refresh-button") ||
    document.querySelector("[data-action='refresh']");

  const feedStatus = document.getElementById("feed-status");

  const matchContainer =
    document.getElementById("match-grid") ||
    document.getElementById("match-list") ||
    document.querySelector(".match-grid") ||
    document.querySelector(".matches-grid") ||
    document.querySelector("[data-match-list]");

  function numberOrDefault(value, fallback) {
    const parsed = Number.parseInt(String(value ?? ""), 10);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function clearSuccessfulLoadingState() {
    const statusElement =
      document.getElementById("status");

    if (!statusElement) {
      return;
    }

    statusElement.textContent = "";
    statusElement.hidden = true;
    statusElement.setAttribute("aria-hidden", "true");
  }


  function selectedCompetition() {
    if (!competitionControl) {
      return DEFAULT_COMPETITION;
    }

    return numberOrDefault(
      competitionControl.value,
      DEFAULT_COMPETITION
    );
  }

  function selectedSeason() {
    if (!seasonControl) {
      return DEFAULT_SEASON;
    }

    return numberOrDefault(
      seasonControl.value,
      DEFAULT_SEASON
    );
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatKickoff(raw) {
    if (!raw) {
      return "Kickoff time unavailable";
    }

    let normalized = String(raw);

    // SQLite-style UTC timestamps have no timezone suffix. The public
    // API's kickoff_utc field is UTC, so make that explicit for browsers.
    if (
      /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(normalized) &&
      !/[zZ]|[+-]\d{2}:\d{2}$/.test(normalized)
    ) {
      normalized = normalized.replace(" ", "T") + "Z";
    }

    const date = new Date(normalized);

    if (Number.isNaN(date.getTime())) {
      return escapeHtml(raw);
    }

    return new Intl.DateTimeFormat(undefined, {
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(date);
  }

  function predictionLabel(value) {
    const normalized = String(value ?? "").toUpperCase();

    if (normalized === "H" || normalized === "HOME") {
      return "Home win";
    }

    if (normalized === "A" || normalized === "AWAY") {
      return "Away win";
    }

    if (normalized === "D" || normalized === "DRAW") {
      return "Draw";
    }

    return value || "Pending";
  }

  function confidenceText(confidence) {
    if (!confidence) {
      return "Pending";
    }

    const percent = confidence.percent;
    const band = confidence.band;

    if (percent === null || percent === undefined) {
      return band || "Pending";
    }

    return `${escapeHtml(percent)}%${
      band ? ` • ${escapeHtml(band)}` : ""
    }`;
  }

  function formSide(side) {
    if (!side) {
      return "Pending";
    }

    const team = escapeHtml(side.team || "Team");
    const index =
      side.index === null || side.index === undefined
        ? "—"
        : escapeHtml(side.index);
    const band = side.band ? ` • ${escapeHtml(side.band)}` : "";

    return `${team}: ${index}${band}`;
  }

  function matchCard(item) {
    const fixture = item.fixture || {};
    const predict = item.sportsq_predict || {};
    const scoreCall = item.sportsq_score_call || {};
    const confidence = item.sportsq_confidence || {};
    const form = item.sportsq_form_index || {};
    const news = item.sportsq_news_impact || {};

    const home = escapeHtml(fixture.home_team || "Home");
    const away = escapeHtml(fixture.away_team || "Away");

    const newsDirection =
      news.direction && news.direction !== "UNVERIFIED"
        ? escapeHtml(news.direction)
        : "No verified impact";

    return `
      <article class="match-card upcoming-match-card stage3-match-card">

      <!-- MDRN SPORTSQ PUBLIC UI STAGE 3 MATCH INTELLIGENCE DETAIL -->

      <div class="match-card__topline">
        <span class="stage3-upcoming-pill">
          UPCOMING
        </span>

        <span class="match-kickoff">
          ${formatKickoff(fixture.kickoff_utc)}
        </span>
      </div>

      <div class="match-teams stage3-team-row">
        <strong>
          ${escapeHtml(fixture.home_team || "Home")}
        </strong>

        <span class="match-vs">
          vs
        </span>

        <strong>
          ${escapeHtml(fixture.away_team || "Away")}
        </strong>
      </div>

      <div class="stage3-headline-grid">

        <div class="stage3-headline-stat">
          <span>
            SportsQ Predict
          </span>

          <strong>
            ${escapeHtml(predict.prediction || "?")}
          </strong>
        </div>

        <div class="stage3-headline-stat">
          <span>
            ScoreCall
          </span>

          <strong>
            ${escapeHtml(scoreCall.score || "?")}
          </strong>
        </div>

        <div class="stage3-headline-stat">
          <span>
            Confidence
          </span>

          <strong>
            ${
              confidence.percent != null
                ? escapeHtml(confidence.percent) + "%"
                : "?"
            }
          </strong>

          <small>
            ${escapeHtml(confidence.band || "?")}
          </small>
        </div>

      </div>

      <details class="stage3-intelligence-detail stage3-deeper-detail">

        <summary>
          <span>
            View deeper intelligence
          </span>
        
          
          <span class="stage3-disclosure-icon" aria-hidden="true"></span>
        </summary>

        <!-- MDRN SPORTSQ STAGE 3 FINAL CLEANUP - DEEPER INTELLIGENCE ONLY -->

        <div class="stage3-deeper-grid">

          <section class="stage3-intelligence-panel stage3-form-panel">
            <span class="stage3-label">
              SPORTSQ FORM INDEX
            </span>

            <div class="stage3-form-grid">

              <div>
                <span>
                  ${escapeHtml(fixture.home_team || "Home")}
                </span>

                <strong>
                  ${
                    form.home?.index != null
                      ? escapeHtml(form.home.index)
                      : "?"
                  }
                </strong>

                <small>
                  ${escapeHtml(form.home?.band || "?")}
                </small>
              </div>

              <div>
                <span>
                  ${escapeHtml(fixture.away_team || "Away")}
                </span>

                <strong>
                  ${
                    form.away?.index != null
                      ? escapeHtml(form.away.index)
                      : "?"
                  }
                </strong>

                <small>
                  ${escapeHtml(form.away?.band || "?")}
                </small>
              </div>

            </div>
          </section>

          <section class="stage3-intelligence-panel stage3-news-panel">
            <span class="stage3-label">
              SPORTSQ NEWS IMPACT
            </span>

            <strong class="stage3-news-title">
              ${
                news.verified === true
                  ? escapeHtml(
                      news.direction ||
                      "Verified team-news impact"
                    )
                  : "No verified team-news impact"
              }
            </strong>

            <small class="stage3-news-status">
              ${escapeHtml(news.status || "UNVERIFIED")}
            </small>

            <p>
              ${
                news.verified === true
                  ? escapeHtml(
                      news.note ||
                      "Verified structured team-news information is available."
                    )
                  : "No verified structured team-news input is available. No injury or news impact is inferred."
              }
            </p>
          </section>

        </div>

      </details>

    </article>
    `;
  }

  function setStatus(message, state = "") {
    if (!feedStatus) {
      return;
    }

    feedStatus.className = `feed-status${
      state ? ` feed-status--${state}` : ""
    }`;

    feedStatus.textContent = message;
  }

  function renderLoading() {
    setStatus(
      "Loading upcoming Premier League intelligence…",
      "loading"
    );

    if (matchContainer) {
      matchContainer.innerHTML = `
        <div class="feed-message feed-message--loading">
          <strong>Loading match intelligence</strong>
          <span>Checking publication-approved upcoming fixtures.</span>
        </div>
      `;

    }
  }

  function renderEmpty() {
    setStatus(
      "No publication-approved upcoming intelligence is available yet.",
      "empty"
    );

    if (!matchContainer) {
      return;
    }

    matchContainer.innerHTML = `
      <div class="feed-message feed-message--empty">
        <strong>No upcoming intelligence published yet</strong>
        <p>
          MDRN SportsQ only displays future fixtures that have passed
          the publication safety gate. Upcoming predictions will appear
          here automatically when approved.
        </p>
      </div>
    `;
  }

  function renderError() {
    setStatus(
      "Match intelligence is temporarily unavailable.",
      "error"
    );

    if (!matchContainer) {
      return;
    }

    matchContainer.innerHTML = `
      <div class="feed-message feed-message--error">
        <strong>Unable to load match intelligence</strong>
        <p>
          Please try again shortly. No unverified prediction data will
          be substituted.
        </p>
      </div>
    `;
  }

  function renderItems(items) {
    if (!matchContainer) {
      return;
    }

    if (!Array.isArray(items) || items.length === 0) {
      renderEmpty();
      return;
    }

    matchContainer.innerHTML = items.map(matchCard).join("");

    setStatus(
      `${items.length} publication-approved upcoming ${
        items.length === 1 ? "fixture" : "fixtures"
      }.`,
      "ready"
    );

    clearSuccessfulLoadingState();
  }

  async function loadPublicIntelligence() {
    renderLoading();

    const competition = selectedCompetition();
    const season = selectedSeason();

    const params = new URLSearchParams({
      competition: String(competition),
      season: String(season),
      limit: String(DEFAULT_LIMIT),
    });

    try {
      const response = await fetch(
        `/api/v1/public/sportsq?${params.toString()}`,
        {
          method: "GET",
          headers: {
            Accept: "application/json",
          },
        }
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const payload = await response.json();

      if (
        payload.status !== "success" ||
        !Array.isArray(payload.items)
      ) {
        throw new Error("Invalid public feed response");
      }

      renderItems(payload.items);
    } catch (error) {
      console.error("MDRN SportsQ public feed error:", error);
      renderError();
    }
  }

  if (competitionControl) {
    competitionControl.value = String(DEFAULT_COMPETITION);
    competitionControl.addEventListener(
      "change",
      loadPublicIntelligence
    );
  }

  if (seasonControl) {
    seasonControl.value = String(DEFAULT_SEASON);
    seasonControl.addEventListener(
      "change",
      loadPublicIntelligence
    );
  }

  if (refreshButton) {
    refreshButton.addEventListener(
      "click",
      loadPublicIntelligence
    );
  }

  loadPublicIntelligence();
})();
