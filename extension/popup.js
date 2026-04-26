const BACKEND = "http://127.0.0.1:8000";
const LINKEDIN_INVITE_LIMIT = 300;
const LINKEDIN_INVITE_WARN = 280;

const STORAGE_KEYS = {
  activeTab: "activeTab",
  resumeId: "resume.id",
  cover: { company: "cover.company", jd: "cover.jd" },
  email: {
    company: "email.company",
    jd: "email.jd",
    intent: "email.intent",
  },
  outreach: {
    channel: "outreach.channel",
    profile: "outreach.profile",
    context: "outreach.context",
  },
  score: {
    company: "score.company",
    jd: "score.jd",
  },
};

const DEFAULT_RESUME_ID = "default";

const $ = (id) => document.getElementById(id);

const healthDot = $("healthDot");
const healthLabel = $("healthLabel");

// ---------- helpers ----------

function setStatus(el, text, kind = "") {
  el.textContent = text;
  el.className = "status" + (kind ? " " + kind : "");
}

function safeFilenamePart(s) {
  return (s || "Company").replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^[._-]+|[._-]+$/g, "") || "Company";
}

async function readErrorDetail(res) {
  try {
    const data = await res.json();
    if (data && typeof data.detail === "string") return data.detail;
    return JSON.stringify(data);
  } catch (_e) {
    try {
      return await res.text();
    } catch (_e2) {
      return `HTTP ${res.status}`;
    }
  }
}

async function checkHealth() {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 2500);
    const res = await fetch(`${BACKEND}/`, { signal: ctrl.signal });
    clearTimeout(t);
    if (res.ok) {
      healthDot.className = "dot ok";
      healthLabel.textContent = "online";
      return true;
    }
    throw new Error(`HTTP ${res.status}`);
  } catch (_e) {
    healthDot.className = "dot bad";
    healthLabel.textContent = "offline";
    return false;
  }
}

async function storageGet(keys) {
  try {
    return await chrome.storage.local.get(keys);
  } catch (_e) {
    return {};
  }
}

async function storageSet(items) {
  try {
    await chrome.storage.local.set(items);
  } catch (_e) {
    // ignore
  }
}

// ---------- resume picker ----------

const resumeSelect = $("resumeSelect");
const resumeBar = document.querySelector(".resume-bar");

let _resumeId = DEFAULT_RESUME_ID;

function getResumeId() {
  return _resumeId;
}

async function loadResumes() {
  // Restore previously saved id while we wait on the network.
  const stored = await storageGet([STORAGE_KEYS.resumeId]);
  const savedId = stored[STORAGE_KEYS.resumeId];
  if (savedId) _resumeId = savedId;

  let resumes = null;
  try {
    const res = await fetch(`${BACKEND}/resumes`);
    if (res.ok) {
      const data = await res.json();
      if (data && Array.isArray(data.resumes)) resumes = data.resumes;
    }
  } catch (_e) {
    resumes = null;
  }

  if (!resumes || resumes.length === 0) {
    if (resumes && resumes.length === 0) {
      // Backend reachable but no resumes on disk.
      resumeSelect.innerHTML = "";
      resumeSelect.disabled = true;
      resumeBar.classList.add("empty");
      _resumeId = "";
      return;
    }
    resumeSelect.innerHTML = `<option value="${DEFAULT_RESUME_ID}">Default</option>`;
    _resumeId = DEFAULT_RESUME_ID;
    return;
  }

  const ids = new Set(resumes.map((r) => r.id));
  if (!ids.has(_resumeId)) {
    _resumeId = resumes[0].id;
    storageSet({ [STORAGE_KEYS.resumeId]: _resumeId });
  }

  resumeSelect.innerHTML = resumes
    .map(
      (r) =>
        `<option value="${r.id}"${r.id === _resumeId ? " selected" : ""}>${r.label}</option>`
    )
    .join("");
  resumeSelect.disabled = false;
  resumeBar.classList.remove("empty");
}

resumeSelect.addEventListener("change", () => {
  _resumeId = resumeSelect.value;
  storageSet({ [STORAGE_KEYS.resumeId]: _resumeId });
});

// ---------- tabs ----------

const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");

function activateTab(name) {
  tabs.forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  panels.forEach((p) => {
    p.classList.toggle("active", p.dataset.panel === name);
  });
  storageSet({ [STORAGE_KEYS.activeTab]: name });
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => activateTab(tab.dataset.tab));
});

// ---------- score display helper ----------

function scoreBand(score) {
  if (score <= 3) return "band-poor";
  if (score <= 5) return "band-marginal";
  if (score <= 7) return "band-solid";
  return "band-strong";
}

function renderScore(displayEl, data, { compact = false } = {}) {
  if (!displayEl) return;
  const numEl = displayEl.querySelector(".score-number");
  const verdictEl = displayEl.querySelector(".score-verdict");

  const score = typeof data.score === "number" ? data.score : 0;
  const verdict = (data.verdict || "").trim();

  numEl.textContent = String(score);
  verdictEl.textContent = verdict;

  let cls = "score-display";
  if (compact) cls += " compact";
  cls += " " + scoreBand(score);
  displayEl.className = cls;
}

function showScoreLoading(displayEl, { compact = false } = {}) {
  if (!displayEl) return;
  const numEl = displayEl.querySelector(".score-number");
  const verdictEl = displayEl.querySelector(".score-verdict");
  numEl.textContent = "-";
  verdictEl.textContent = "Scoring...";
  let cls = "score-display loading";
  if (compact) cls += " compact";
  displayEl.className = cls;
}

function hideScore(displayEl) {
  if (!displayEl) return;
  displayEl.classList.add("hidden");
}

// ---------- copy buttons ----------

document.querySelectorAll(".copy-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const targetId = btn.dataset.copy;
    const target = $(targetId);
    if (!target) return;
    const text = target.value || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      btn.classList.add("copied");
      const orig = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => {
        btn.classList.remove("copied");
        btn.textContent = orig;
      }, 1500);
    } catch (_e) {
      // Fallback: select text so user can hit Cmd+C.
      target.focus();
      target.select();
    }
  });
});

// ============================================================================
// Cover Letter tab
// ============================================================================

const coverForm = $("coverForm");
const coverCompany = $("cover-company");
const coverJd = $("cover-jd");
const coverSubmit = $("coverSubmit");
const coverStatus = $("coverStatus");
const coverScore = $("coverScore");

async function fetchScoreSilently(jd, company) {
  try {
    const res = await fetch(`${BACKEND}/score`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_description: jd,
        company: company || null,
        resume_id: getResumeId(),
      }),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (_e) {
    return null;
  }
}

async function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  try {
    await new Promise((resolve, reject) => {
      chrome.downloads.download({ url, filename, saveAs: false }, (downloadId) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (typeof downloadId === "undefined") {
          reject(new Error("download did not start"));
          return;
        }
        resolve(downloadId);
      });
    });
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
}

coverForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const company = coverCompany.value.trim();
  const jd = coverJd.value.trim();
  if (!company || !jd) {
    setStatus(coverStatus, "Fill in both fields.", "err");
    return;
  }

  await storageSet({
    [STORAGE_KEYS.cover.company]: company,
    [STORAGE_KEYS.cover.jd]: jd,
  });

  coverSubmit.disabled = true;
  setStatus(
    coverStatus,
    "Generating... (first compile can take 30s+ while tectonic fetches packages)",
    "working"
  );

  // Fire score in parallel; populate the inline score block when it returns.
  coverScore.classList.remove("hidden");
  showScoreLoading(coverScore, { compact: true });
  fetchScoreSilently(jd, company).then((data) => {
    if (data && typeof data.score === "number") {
      renderScore(coverScore, data, { compact: true });
    } else {
      hideScore(coverScore);
    }
  });

  try {
    const res = await fetch(`${BACKEND}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company,
        job_description: jd,
        resume_id: getResumeId(),
      }),
    });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      throw new Error(detail || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    if (blob.type && !blob.type.includes("pdf")) {
      throw new Error(`Unexpected response type: ${blob.type}`);
    }
    const filename = `CoverLetter_${safeFilenamePart(company)}.pdf`;
    await downloadBlob(blob, filename);
    setStatus(coverStatus, `Done. Saved as ${filename}.`, "ok");
  } catch (err) {
    setStatus(coverStatus, `Failed: ${err.message || err}`, "err");
  } finally {
    coverSubmit.disabled = false;
  }
});

// ============================================================================
// Email tab
// ============================================================================

const emailForm = $("emailForm");
const emailCompany = $("email-company");
const emailJd = $("email-jd");
const emailIntent = $("email-intent");
const emailSubmit = $("emailSubmit");
const emailStatus = $("emailStatus");
const emailResult = $("emailResult");
const emailSubjectOut = $("email-subject-out");
const emailBodyOut = $("email-body-out");

emailForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const company = emailCompany.value.trim();
  const jd = emailJd.value.trim();
  const intent = emailIntent.value.trim();
  if (!company || !jd) {
    setStatus(emailStatus, "Fill in company and job description.", "err");
    return;
  }

  await storageSet({
    [STORAGE_KEYS.email.company]: company,
    [STORAGE_KEYS.email.jd]: jd,
    [STORAGE_KEYS.email.intent]: intent,
  });

  emailSubmit.disabled = true;
  setStatus(emailStatus, "Generating email...", "working");
  emailResult.classList.add("hidden");

  try {
    const res = await fetch(`${BACKEND}/email`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company,
        job_description: jd,
        intent: intent || null,
        resume_id: getResumeId(),
      }),
    });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      throw new Error(detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    emailSubjectOut.value = data.subject || "";
    emailBodyOut.value = data.body || "";
    emailResult.classList.remove("hidden");
    setStatus(emailStatus, "Done. Edit if you want, then copy.", "ok");
  } catch (err) {
    setStatus(emailStatus, `Failed: ${err.message || err}`, "err");
  } finally {
    emailSubmit.disabled = false;
  }
});

// ============================================================================
// Outreach tab
// ============================================================================

const outreachForm = $("outreachForm");
const outreachChannel = $("outreach-channel");
const outreachProfile = $("outreach-profile");
const outreachContext = $("outreach-context");
const outreachSubmit = $("outreachSubmit");
const outreachStatus = $("outreachStatus");
const outreachResult = $("outreachResult");
const outreachSubjectRow = $("outreachSubjectRow");
const outreachSubjectOut = $("outreach-subject-out");
const outreachMessageOut = $("outreach-message-out");
const outreachCharCount = $("outreachCharCount");

function updateCharCount() {
  if (outreachChannel.value !== "linkedin_invitation") {
    outreachCharCount.textContent = "";
    outreachCharCount.className = "char-count";
    return;
  }
  const len = outreachMessageOut.value.length;
  outreachCharCount.textContent = `${len}/${LINKEDIN_INVITE_LIMIT}`;
  let cls = "char-count";
  if (len > LINKEDIN_INVITE_LIMIT) cls += " over";
  else if (len > LINKEDIN_INVITE_WARN) cls += " warn";
  outreachCharCount.className = cls;
}

outreachMessageOut.addEventListener("input", updateCharCount);
outreachChannel.addEventListener("change", () => {
  storageSet({ [STORAGE_KEYS.outreach.channel]: outreachChannel.value });
  updateCharCount();
});

outreachForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const channel = outreachChannel.value;
  const profile = outreachProfile.value.trim();
  const context = outreachContext.value.trim();
  if (!profile) {
    setStatus(outreachStatus, "Paste the target person's profile.", "err");
    return;
  }

  await storageSet({
    [STORAGE_KEYS.outreach.channel]: channel,
    [STORAGE_KEYS.outreach.profile]: profile,
    [STORAGE_KEYS.outreach.context]: context,
  });

  outreachSubmit.disabled = true;
  setStatus(outreachStatus, "Generating message...", "working");
  outreachResult.classList.add("hidden");

  try {
    const res = await fetch(`${BACKEND}/outreach`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile_text: profile,
        channel,
        context: context || null,
        resume_id: getResumeId(),
      }),
    });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      throw new Error(detail || `HTTP ${res.status}`);
    }
    const data = await res.json();

    const isEmail = channel === "email";
    outreachSubjectRow.style.display = isEmail ? "" : "none";
    outreachSubjectOut.style.display = isEmail ? "" : "none";
    if (isEmail) outreachSubjectOut.value = data.subject || "";

    outreachMessageOut.value = data.message || "";
    outreachResult.classList.remove("hidden");
    updateCharCount();

    const cc = typeof data.char_count === "number" ? ` (${data.char_count} chars)` : "";
    setStatus(outreachStatus, `Done${cc}. Edit if you want, then copy.`, "ok");
  } catch (err) {
    setStatus(outreachStatus, `Failed: ${err.message || err}`, "err");
  } finally {
    outreachSubmit.disabled = false;
  }
});

// ============================================================================
// Score tab
// ============================================================================

const scoreForm = $("scoreForm");
const scoreCompany = $("score-company");
const scoreJd = $("score-jd");
const scoreSubmit = $("scoreSubmit");
const scoreStatus = $("scoreStatus");
const scoreList = $("scoreList");

function selectResumeGlobally(resumeId, label) {
  if (!resumeId) return;
  _resumeId = resumeId;
  if (resumeSelect) {
    resumeSelect.value = resumeId;
    // Updating .value silently doesn't fire 'change'; persist manually.
  }
  storageSet({ [STORAGE_KEYS.resumeId]: resumeId });
  highlightActiveScoreRow(resumeId);
  setStatus(
    scoreStatus,
    `Set "${label || resumeId}" as the active resume for the other tabs.`,
    "ok"
  );
}

function highlightActiveScoreRow(resumeId) {
  scoreList.querySelectorAll(".score-row").forEach((row) => {
    row.classList.toggle("active", row.dataset.resumeId === resumeId);
  });
}

function renderScoreList(results) {
  scoreList.innerHTML = "";
  if (!results || results.length === 0) {
    scoreList.classList.add("hidden");
    return;
  }
  scoreList.classList.remove("hidden");

  const successes = results.filter((r) => typeof r.score === "number");
  const topId = successes.length > 0 ? successes[0].resume_id : null;
  const activeId = getResumeId();

  results.forEach((r) => {
    const row = document.createElement("div");
    row.className = "score-row";
    row.dataset.resumeId = r.resume_id;

    const isError = !!r.error;
    if (!isError) {
      row.classList.add("clickable");
      row.classList.add(scoreBand(r.score));
    } else {
      row.classList.add("error");
    }
    if (!isError && r.resume_id === topId && successes.length > 1) {
      row.classList.add("top");
    }
    if (r.resume_id === activeId) row.classList.add("active");

    const labelEl = document.createElement("div");
    labelEl.className = "score-row-label";
    labelEl.textContent = r.label || r.resume_id;

    const numWrap = document.createElement("div");
    numWrap.className = "score-number-wrap";
    const numEl = document.createElement("span");
    numEl.className = "score-number";
    numEl.textContent = isError ? "-" : String(r.score);
    const denomEl = document.createElement("span");
    denomEl.className = "score-denom";
    denomEl.textContent = "/10";
    numWrap.append(numEl, denomEl);

    const verdictEl = document.createElement("div");
    verdictEl.className = "score-verdict";
    verdictEl.textContent = isError ? r.error : r.verdict || "";

    row.append(numWrap, labelEl, verdictEl);

    if (!isError) {
      row.addEventListener("click", () => {
        selectResumeGlobally(r.resume_id, r.label);
      });
    }

    scoreList.appendChild(row);
  });
}

scoreForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const company = scoreCompany.value.trim();
  const jd = scoreJd.value.trim();
  if (!jd) {
    setStatus(scoreStatus, "Paste the job description.", "err");
    return;
  }

  await storageSet({
    [STORAGE_KEYS.score.company]: company,
    [STORAGE_KEYS.score.jd]: jd,
  });

  scoreSubmit.disabled = true;
  setStatus(scoreStatus, "Scoring against all resumes...", "working");
  scoreList.classList.add("hidden");
  scoreList.innerHTML = "";

  try {
    const res = await fetch(`${BACKEND}/score-all`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_description: jd,
        company: company || null,
      }),
    });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      throw new Error(detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    const results = (data && data.results) || [];
    if (results.length === 0) {
      setStatus(
        scoreStatus,
        "No resumes found. Drop a .txt into backend/resumes/.",
        "err"
      );
      return;
    }
    renderScoreList(results);
    const topSuccess = results.find((r) => typeof r.score === "number");
    if (topSuccess) {
      setStatus(
        scoreStatus,
        `Top: "${topSuccess.label}" - click any row to set as active resume.`,
        ""
      );
    } else {
      setStatus(scoreStatus, "All resumes errored. See rows for details.", "err");
    }
  } catch (err) {
    setStatus(scoreStatus, `Failed: ${err.message || err}`, "err");
  } finally {
    scoreSubmit.disabled = false;
  }
});

// ============================================================================
// Restore persisted fields & active tab
// ============================================================================

async function restoreAll() {
  const data = await storageGet([
    STORAGE_KEYS.activeTab,
    STORAGE_KEYS.cover.company,
    STORAGE_KEYS.cover.jd,
    STORAGE_KEYS.email.company,
    STORAGE_KEYS.email.jd,
    STORAGE_KEYS.email.intent,
    STORAGE_KEYS.outreach.channel,
    STORAGE_KEYS.outreach.profile,
    STORAGE_KEYS.outreach.context,
    STORAGE_KEYS.score.company,
    STORAGE_KEYS.score.jd,
  ]);

  if (data[STORAGE_KEYS.cover.company]) coverCompany.value = data[STORAGE_KEYS.cover.company];
  if (data[STORAGE_KEYS.cover.jd]) coverJd.value = data[STORAGE_KEYS.cover.jd];

  if (data[STORAGE_KEYS.email.company]) emailCompany.value = data[STORAGE_KEYS.email.company];
  if (data[STORAGE_KEYS.email.jd]) emailJd.value = data[STORAGE_KEYS.email.jd];
  if (data[STORAGE_KEYS.email.intent]) emailIntent.value = data[STORAGE_KEYS.email.intent];

  if (data[STORAGE_KEYS.outreach.channel]) outreachChannel.value = data[STORAGE_KEYS.outreach.channel];
  if (data[STORAGE_KEYS.outreach.profile]) outreachProfile.value = data[STORAGE_KEYS.outreach.profile];
  if (data[STORAGE_KEYS.outreach.context]) outreachContext.value = data[STORAGE_KEYS.outreach.context];

  if (data[STORAGE_KEYS.score.company]) scoreCompany.value = data[STORAGE_KEYS.score.company];
  if (data[STORAGE_KEYS.score.jd]) scoreJd.value = data[STORAGE_KEYS.score.jd];

  const activeTab = data[STORAGE_KEYS.activeTab];
  if (activeTab && ["cover", "email", "outreach", "score"].includes(activeTab)) {
    activateTab(activeTab);
  }

  updateCharCount();
}

document.addEventListener("DOMContentLoaded", () => {
  restoreAll();
  loadResumes();
  checkHealth();
});
