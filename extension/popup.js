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
  question: {
    company: "question.company",
    jd: "question.jd",
    text: "question.text",
  },
  track: {
    company: "track.company",
    role: "track.role",
    location: "track.location",
    jd: "track.jd",
    status: "track.status",
    interview: "track.interview",
    appliedDate: "track.appliedDate",
    notes: "track.notes",
    jobUrl: "track.jobUrl",
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
  syncAutodetectBarVisibility();
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => activateTab(tab.dataset.tab));
});

// ---------- auto-detect from page ----------

const AUTODETECT_TARGETS = {
  cover: {
    company: "cover-company",
    jd: "cover-jd",
    storage: { company: STORAGE_KEYS.cover.company, jd: STORAGE_KEYS.cover.jd },
  },
  email: {
    company: "email-company",
    jd: "email-jd",
    storage: { company: STORAGE_KEYS.email.company, jd: STORAGE_KEYS.email.jd },
  },
  score: {
    company: "score-company",
    jd: "score-jd",
    storage: { company: STORAGE_KEYS.score.company, jd: STORAGE_KEYS.score.jd },
  },
  question: {
    company: "question-company",
    jd: "question-jd",
    storage: { company: STORAGE_KEYS.question.company, jd: STORAGE_KEYS.question.jd },
  },
  // Track tab uses extra fields beyond company+jd; runAutoDetect handles
  // these by name when the active tab is "track".
  track: {
    company: "track-company",
    jd: "track-jd",
    role: "track-role",
    location: "track-location",
    storage: {
      company: STORAGE_KEYS.track.company,
      jd: STORAGE_KEYS.track.jd,
      role: STORAGE_KEYS.track.role,
      location: STORAGE_KEYS.track.location,
      jobUrl: STORAGE_KEYS.track.jobUrl,
    },
  },
};

const autodetectBar = $("autodetectBar");
const autoDetectBtn = $("autoDetectBtn");
const autoDetectStatus = $("autoDetectStatus");

function setAutodetectStatus(text, kind = "") {
  autoDetectStatus.textContent = text;
  autoDetectStatus.className = "autodetect-status" + (kind ? " " + kind : "");
}

function getActiveTabName() {
  const t = document.querySelector(".tab.active");
  return t ? t.dataset.tab : "cover";
}

function syncAutodetectBarVisibility() {
  const supported = Boolean(AUTODETECT_TARGETS[getActiveTabName()]);
  autodetectBar.classList.toggle("hidden", !supported);
}

// Self-contained extractor; runs in the active tab's page context.
// Must not reference any popup-scope variables.
function extractJdFromPage() {
  const MIN_JD_LEN = 200;
  const MAX_TEXT = 40000;

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  // Some sites (CBRE, certain Workday tenants) emit JD content as already-
  // serialised HTML inside JSON-LD or text nodes - so what looks like rendered
  // text actually contains literal "<div>..." characters. Detect and strip.
  const looksLikeHtml = (s) =>
    typeof s === "string" && /<\/?[a-z][\s\S]*?>/i.test(s);
  const stripHtml = (s) => {
    if (!s) return "";
    const tmp = document.createElement("div");
    tmp.innerHTML = s;
    // Two passes handle the double-encoded case (e.g. "&lt;div&gt;").
    let out = tmp.innerText || tmp.textContent || "";
    if (looksLikeHtml(out)) {
      tmp.innerHTML = out;
      out = tmp.innerText || tmp.textContent || "";
    }
    return out;
  };
  const blockText = (el) => {
    if (!el) return "";
    const clone = el.cloneNode(true);
    clone.querySelectorAll("script,style,noscript").forEach((n) => n.remove());
    let text = (clone.innerText || clone.textContent || "")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
    if (looksLikeHtml(text)) text = stripHtml(text);
    return text;
  };
  const pickText = (selectors) => {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      const t = blockText(el);
      if (t) return t;
    }
    return "";
  };
  const pickCompany = (selectors) => {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      const t = norm(el && (el.textContent || el.getAttribute("content")));
      if (t) return t;
    }
    return "";
  };
  const ogSiteName = () => {
    const m = document.querySelector('meta[property="og:site_name"]');
    return norm(m && m.getAttribute("content"));
  };
  // schema.org JobPosting JSON-LD: emitted by Ashby (and many other ATS)
  // at server-render time, so it works before any client-side React renders.
  const jsonLdJobPosting = () => {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const s of scripts) {
      try {
        const parsed = JSON.parse(s.textContent || "");
        const items = Array.isArray(parsed) ? parsed : [parsed];
        for (const item of items) {
          if (!item || typeof item !== "object") continue;
          const t = item["@type"];
          const isJob = t === "JobPosting" || (Array.isArray(t) && t.includes("JobPosting"));
          if (isJob) return item;
        }
      } catch (_e) {
        // skip malformed JSON-LD blocks
      }
    }
    return null;
  };
  const fromJsonLd = () => {
    const job = jsonLdJobPosting();
    if (!job) return { company: "", jd: "", role: "", location: "" };
    const c =
      norm(job.hiringOrganization?.name) ||
      norm(job.hiringOrganization) ||
      "";
    const title = norm(job.title);
    const descHtml = job.description || "";
    const tmp = document.createElement("div");
    tmp.innerHTML = typeof descHtml === "string" ? descHtml : "";
    const desc = blockText(tmp);
    const jdText = title ? `${title}\n\n${desc}`.trim() : desc;

    // jobLocation is sometimes an object, sometimes an array. Take the first.
    let loc = "";
    const locField = job.jobLocation;
    const locItem = Array.isArray(locField) ? locField[0] : locField;
    if (locItem && typeof locItem === "object") {
      const addr = locItem.address || {};
      const parts = [
        norm(addr.addressLocality),
        norm(addr.addressRegion),
        norm(addr.addressCountry?.name) || norm(addr.addressCountry),
      ].filter(Boolean);
      loc = parts.join(", ");
    }
    if (!loc && job.jobLocationType) {
      // e.g. "TELECOMMUTE" -> "Remote"
      const t = String(job.jobLocationType).toLowerCase();
      if (t.includes("telecommute")) loc = "Remote";
    }
    return { company: c, jd: jdText, role: title, location: loc };
  };

  const host = location.hostname.toLowerCase();
  const url = location.href;
  const page_title = document.title || "";

  let company = "";
  let jd = "";
  let role = "";
  let job_location = "";

  try {
    if (host.includes("linkedin.com")) {
      company = pickCompany([
        ".job-details-jobs-unified-top-card__company-name a",
        ".job-details-jobs-unified-top-card__company-name",
        ".topcard__org-name-link",
        ".jobs-unified-top-card__company-name a",
        ".jobs-unified-top-card__company-name",
      ]);
      role = pickCompany([
        ".job-details-jobs-unified-top-card__job-title h1",
        ".job-details-jobs-unified-top-card__job-title",
        ".jobs-unified-top-card__job-title",
        ".topcard__title",
        "h1.t-24",
      ]);
      job_location = pickCompany([
        ".job-details-jobs-unified-top-card__primary-description-container .tvm__text:first-child",
        ".job-details-jobs-unified-top-card__bullet",
        ".jobs-unified-top-card__bullet",
        ".topcard__flavor--bullet",
      ]);
      jd = pickText([
        ".jobs-description__content .jobs-box__html-content",
        ".jobs-description-content__text",
        ".jobs-box__html-content",
        ".show-more-less-html__markup",
        "#job-details",
      ]);
    } else if (host.endsWith("greenhouse.io")) {
      company =
        pickCompany([".company-name", "header .company-name", "h1.company-name"]) ||
        ogSiteName();
      jd = pickText(["#content", ".section-wrapper.body", "#main #content"]);
    } else if (host === "jobs.lever.co" || host.endsWith(".lever.co")) {
      company =
        ogSiteName() ||
        pickCompany([".main-header-logo img[alt]", ".main-header-text-logo"]);
      const headlineEl = document.querySelector(".posting-headline h2");
      const headline = norm(headlineEl && headlineEl.textContent);
      const desc = pickText([
        ".section-wrapper.page-full-width .section.page-centered",
        ".posting-page .content",
        ".posting-page",
      ]);
      jd = headline ? `${headline}\n\n${desc}`.trim() : desc;
    } else if (host === "jobs.ashbyhq.com" || host.endsWith(".ashbyhq.com")) {
      const ld = fromJsonLd();
      if (ld.jd) {
        company = ld.company;
        jd = ld.jd;
      }
      if (!jd) {
        try {
          const next = document.getElementById("__NEXT_DATA__");
          if (next && next.textContent) {
            const data = JSON.parse(next.textContent);
            const job =
              data?.props?.pageProps?.posting ||
              data?.props?.pageProps?.jobPosting ||
              data?.props?.pageProps?.job ||
              null;
            if (job) {
              company =
                job.organizationName ||
                job.organization?.name ||
                job.company ||
                "";
              const title = job.title || job.role || "";
              const descHtml =
                job.descriptionHtml ||
                job.description ||
                job.jobDescriptionHtml ||
                "";
              const tmp = document.createElement("div");
              tmp.innerHTML = descHtml;
              const desc = blockText(tmp);
              jd = title ? `${title}\n\n${desc}`.trim() : desc;
            }
          }
        } catch (_e) {
          // fall through to selector-based attempt
        }
      }
      if (!jd) {
        jd = pickText([
          '[class*="_descriptionText"]',
          '[class*="_jobPostingDescription"]',
          "main",
        ]);
      }
      if (!company) company = ogSiteName();
    } else if (host.endsWith("myworkdayjobs.com")) {
      jd = pickText([
        '[data-automation-id="jobPostingDescription"]',
        '[data-automation-id="jobPostingPage"]',
      ]);
      company =
        ogSiteName() ||
        (host.split(".")[0] || "").replace(/[-_]+/g, " ").trim();
      if (company) company = company.charAt(0).toUpperCase() + company.slice(1);
    } else if (host.includes("indeed.com")) {
      company = pickCompany([
        '[data-testid="inlineHeader-companyName"] a',
        '[data-testid="inlineHeader-companyName"]',
        '[data-company-name="true"]',
      ]);
      jd = pickText(["#jobDescriptionText"]);
    }
  } catch (_e) {
    // best-effort; selector errors fall through to fallback
  }

  if (!jd) {
    try {
      const ld = fromJsonLd();
      if (ld.jd) {
        if (!company) company = ld.company;
        jd = ld.jd;
      }
      if (!role && ld.role) role = ld.role;
      if (!job_location && ld.location) job_location = ld.location;
    } catch (_e) {
      // ignore
    }
  }

  // Fill role/location from JSON-LD even when site selectors gave us the JD,
  // since the site-specific branches above don't always pick these up.
  if (!role || !job_location) {
    try {
      const ld = fromJsonLd();
      if (!role && ld.role) role = ld.role;
      if (!job_location && ld.location) job_location = ld.location;
    } catch (_e) {
      // ignore
    }
  }

  // Final safety net: if any extraction path leaked literal HTML into jd
  // (e.g. double-encoded JSON-LD descriptions), strip it before returning.
  if (looksLikeHtml(jd)) jd = stripHtml(jd);

  const matched = Boolean(company && jd && jd.length >= MIN_JD_LEN);

  let page_text = "";
  if (!matched) {
    page_text = blockText(document.body).slice(0, MAX_TEXT);
    if (looksLikeHtml(page_text)) page_text = stripHtml(page_text);
  }

  return {
    matched,
    host,
    url,
    page_title,
    company,
    jd,
    role,
    location: job_location,
    page_text,
  };
}

// When a company embeds an ATS in an iframe (e.g. voleon.com → Ashby),
// the top frame is just marketing chrome. Prefer frames that look like
// known ATS hosts so the extractor's site-specific selectors fire.
function isAtsHost(host) {
  if (!host) return false;
  return (
    host.endsWith(".ashbyhq.com") ||
    host === "jobs.ashbyhq.com" ||
    host.endsWith("greenhouse.io") ||
    host === "jobs.lever.co" ||
    host.endsWith(".lever.co") ||
    host.endsWith("myworkdayjobs.com")
  );
}

function pickBestFrameResult(executeScriptOut) {
  if (!Array.isArray(executeScriptOut) || executeScriptOut.length === 0) {
    return null;
  }
  const results = executeScriptOut
    .map((entry) => entry && entry.result)
    .filter((r) => r && typeof r === "object");
  if (results.length === 0) return null;

  const matched = results.find((r) => r.matched);
  if (matched) return matched;

  const ats = results.find((r) => isAtsHost(r.host));
  if (ats) return ats;

  const withText = results.find((r) => r.page_text && r.page_text.length > 0);
  if (withText) return withText;

  return results[0];
}

async function runAutoDetect() {
  const active = getActiveTabName();
  const targets = AUTODETECT_TARGETS[active];
  if (!targets) return;

  let tab;
  try {
    [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  } catch (_e) {
    setAutodetectStatus("Could not read active tab.", "err");
    return;
  }
  if (!tab || !tab.id) {
    setAutodetectStatus("Could not read active tab.", "err");
    return;
  }
  if (/^(chrome|edge|brave|about|chrome-extension):/.test(tab.url || "")) {
    setAutodetectStatus("Open a job posting page first.", "err");
    return;
  }

  autoDetectBtn.disabled = true;
  setAutodetectStatus("Detecting...", "working");

  let result;
  try {
    const out = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: true },
      func: extractJdFromPage,
    });
    result = pickBestFrameResult(out);
  } catch (err) {
    setAutodetectStatus(
      `Failed to read page: ${err.message || err}`,
      "err"
    );
    autoDetectBtn.disabled = false;
    return;
  }

  if (!result) {
    setAutodetectStatus("No data extracted from page.", "err");
    autoDetectBtn.disabled = false;
    return;
  }

  let company = result.company || "";
  let jd = result.jd || "";
  let role = result.role || "";
  let job_location = result.location || "";
  let source = result.host ? `selectors: ${result.host}` : "selectors";

  if (!result.matched) {
    if (!result.page_text) {
      setAutodetectStatus("Page has no readable text.", "err");
      autoDetectBtn.disabled = false;
      return;
    }
    setAutodetectStatus("Detecting via Groq...", "working");
    try {
      const res = await fetch(`${BACKEND}/extract-jd`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: result.url || "",
          page_title: result.page_title || "",
          page_text: result.page_text,
        }),
      });
      if (!res.ok) {
        const detail = await readErrorDetail(res);
        throw new Error(detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      company = data.company || "";
      jd = data.job_description || "";
      // Groq returns these on newer backend; older deploys won't.
      if (data.job_role) role = data.job_role;
      if (data.location) job_location = data.location;
      source = "groq";
    } catch (err) {
      setAutodetectStatus(`Failed: ${err.message || err}`, "err");
      autoDetectBtn.disabled = false;
      return;
    }
  }

  if (!company && !jd) {
    const msg = source === "groq"
      ? "Page doesn't look like a single job posting. Open the specific posting and try again."
      : "This site is supported but the posting markup looks different - try copy/paste.";
    setAutodetectStatus(msg, "err");
    autoDetectBtn.disabled = false;
    return;
  }

  const companyEl = $(targets.company);
  const jdEl = $(targets.jd);
  if (companyEl) companyEl.value = company;
  if (jdEl) jdEl.value = jd;

  const storageItems = {
    [targets.storage.company]: company,
    [targets.storage.jd]: jd,
  };

  // Track tab also gets role / location populated, plus the page URL shown
  // in the visible jobUrl field and stashed in storage for save time.
  if (active === "track") {
    const roleEl = $(targets.role);
    const locEl = $(targets.location);
    const jobUrlEl = $("track-job-url");
    if (roleEl) roleEl.value = role;
    if (locEl) locEl.value = job_location;
    if (jobUrlEl && tab && tab.url) jobUrlEl.value = tab.url;
    storageItems[targets.storage.role] = role;
    storageItems[targets.storage.location] = job_location;
    if (tab && tab.url) {
      storageItems[targets.storage.jobUrl] = tab.url;
    }
  }

  await storageSet(storageItems);

  if (company && !jd) {
    setAutodetectStatus("Detected company but no job description on this page.", "err");
  } else if (!company && jd) {
    setAutodetectStatus("Detected a description but no company name. Fill it in manually.", "err");
  } else {
    setAutodetectStatus(`Filled from ${source}.`, "ok");
  }
  autoDetectBtn.disabled = false;
}

autoDetectBtn.addEventListener("click", runAutoDetect);

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
const coverTextBtn = $("coverTextBtn");
const coverStatus = $("coverStatus");
const coverScore = $("coverScore");
const coverTextResult = $("coverTextResult");
const coverBodyOut = $("cover-body-out");

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

coverTextBtn.addEventListener("click", async () => {
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
  coverTextBtn.disabled = true;
  setStatus(coverStatus, "Generating cover letter text...", "working");
  coverTextResult.classList.add("hidden");

  // Fire score in parallel; same UX as the PDF flow.
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
    const res = await fetch(`${BACKEND}/cover-text`, {
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
    const data = await res.json();
    const greeting = `Dear ${data.hiring_manager || "Hiring Team"},`;
    const signoff = "Best regards,\nAmogh Ramagiri";
    coverBodyOut.value = `${greeting}\n\n${(data.body || "").trim()}\n\n${signoff}`;
    coverTextResult.classList.remove("hidden");
    setStatus(coverStatus, "Done. Edit if you want, then copy.", "ok");
  } catch (err) {
    setStatus(coverStatus, `Failed: ${err.message || err}`, "err");
  } finally {
    coverSubmit.disabled = false;
    coverTextBtn.disabled = false;
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
// Question tab
// ============================================================================

const questionForm = $("questionForm");
const questionCompany = $("question-company");
const questionJd = $("question-jd");
const questionText = $("question-text");
const questionSubmit = $("questionSubmit");
const questionStatus = $("questionStatus");
const questionResult = $("questionResult");
const questionAnswerOut = $("question-answer-out");

questionForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const company = questionCompany.value.trim();
  const jd = questionJd.value.trim();
  const question = questionText.value.trim();
  if (!company || !jd || !question) {
    setStatus(
      questionStatus,
      "Fill in company, job description, and the question.",
      "err"
    );
    return;
  }

  await storageSet({
    [STORAGE_KEYS.question.company]: company,
    [STORAGE_KEYS.question.jd]: jd,
    [STORAGE_KEYS.question.text]: question,
  });

  questionSubmit.disabled = true;
  setStatus(questionStatus, "Generating answer...", "working");
  questionResult.classList.add("hidden");

  try {
    const res = await fetch(`${BACKEND}/answer-question`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company,
        job_description: jd,
        question,
        resume_id: getResumeId(),
      }),
    });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      throw new Error(detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    questionAnswerOut.value = data.answer || "";
    questionResult.classList.remove("hidden");
    setStatus(questionStatus, "Done. Edit if you want, then copy.", "ok");
  } catch (err) {
    setStatus(questionStatus, `Failed: ${err.message || err}`, "err");
  } finally {
    questionSubmit.disabled = false;
  }
});

// ============================================================================
// Track tab
// ============================================================================

const trackForm = $("trackForm");
const trackCompany = $("track-company");
const trackRole = $("track-role");
const trackLocation = $("track-location");
const trackJobUrl = $("track-job-url");
const trackJd = $("track-jd");
const trackStatusSel = $("track-status");
const trackInterview = $("track-interview");
const trackAppliedDate = $("track-applied-date");
const trackNotes = $("track-notes");
const trackSubmit = $("trackSubmit");
const trackStatusEl = $("trackStatus");

function todayIsoDate() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

trackForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const company = trackCompany.value.trim();
  if (!company) {
    setStatus(trackStatusEl, "Company is required.", "err");
    return;
  }

  // Read jobUrl from the visible input; fall back to the active tab's URL.
  let jobUrl = trackJobUrl.value.trim();
  if (!jobUrl) {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab && tab.url && !/^(chrome|edge|brave|about|chrome-extension):/.test(tab.url)) {
        jobUrl = tab.url;
      }
    } catch (_e) {
      // ignore
    }
  }

  const payload = {
    companyName: company,
    jobRole: trackRole.value.trim() || null,
    location: trackLocation.value.trim() || null,
    interviewStatus: trackInterview.value.trim() || null,
    status: trackStatusSel.value || "Applied",
    appliedDate: trackAppliedDate.value || todayIsoDate(),
    resumeId: getResumeId() || null,
    jobUrl: jobUrl || null,
    notes: trackNotes.value.trim() || null,
    jobDescription: trackJd.value.trim() || null,
  };

  await storageSet({
    [STORAGE_KEYS.track.company]: payload.companyName,
    [STORAGE_KEYS.track.role]: payload.jobRole || "",
    [STORAGE_KEYS.track.location]: payload.location || "",
    [STORAGE_KEYS.track.jobUrl]: payload.jobUrl || "",
    [STORAGE_KEYS.track.status]: payload.status,
    [STORAGE_KEYS.track.interview]: payload.interviewStatus || "",
    // Don't persist appliedDate — it should always default to "today" when
    // the popup opens, not whatever was last saved.
    [STORAGE_KEYS.track.notes]: payload.notes || "",
    [STORAGE_KEYS.track.jd]: payload.jobDescription || "",
  });

  trackSubmit.disabled = true;
  setStatus(trackStatusEl, "Saving...", "working");

  try {
    const res = await fetch(`${BACKEND}/track`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      throw new Error(detail || `HTTP ${res.status}`);
    }
    setStatus(trackStatusEl, `Saved. ${payload.companyName} - ${payload.status}.`, "ok");
  } catch (err) {
    setStatus(trackStatusEl, `Failed: ${err.message || err}`, "err");
  } finally {
    trackSubmit.disabled = false;
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
    STORAGE_KEYS.question.company,
    STORAGE_KEYS.question.jd,
    STORAGE_KEYS.question.text,
    STORAGE_KEYS.track.company,
    STORAGE_KEYS.track.role,
    STORAGE_KEYS.track.location,
    STORAGE_KEYS.track.jobUrl,
    STORAGE_KEYS.track.jd,
    STORAGE_KEYS.track.status,
    STORAGE_KEYS.track.interview,
    STORAGE_KEYS.track.appliedDate,
    STORAGE_KEYS.track.notes,
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

  if (data[STORAGE_KEYS.question.company]) questionCompany.value = data[STORAGE_KEYS.question.company];
  if (data[STORAGE_KEYS.question.jd]) questionJd.value = data[STORAGE_KEYS.question.jd];
  if (data[STORAGE_KEYS.question.text]) questionText.value = data[STORAGE_KEYS.question.text];

  if (data[STORAGE_KEYS.track.company]) trackCompany.value = data[STORAGE_KEYS.track.company];
  if (data[STORAGE_KEYS.track.role]) trackRole.value = data[STORAGE_KEYS.track.role];
  if (data[STORAGE_KEYS.track.location]) trackLocation.value = data[STORAGE_KEYS.track.location];
  if (data[STORAGE_KEYS.track.jobUrl]) trackJobUrl.value = data[STORAGE_KEYS.track.jobUrl];
  if (data[STORAGE_KEYS.track.jd]) trackJd.value = data[STORAGE_KEYS.track.jd];
  if (data[STORAGE_KEYS.track.status]) trackStatusSel.value = data[STORAGE_KEYS.track.status];
  if (data[STORAGE_KEYS.track.interview]) trackInterview.value = data[STORAGE_KEYS.track.interview];
  // Always default Applied date to today on popup load. The stored value is
  // ignored on purpose: if you tracked a job yesterday, opening the popup
  // today should show today, not "yesterday".
  trackAppliedDate.value = todayIsoDate();
  if (data[STORAGE_KEYS.track.notes]) trackNotes.value = data[STORAGE_KEYS.track.notes];

  const activeTab = data[STORAGE_KEYS.activeTab];
  if (
    activeTab &&
    ["cover", "email", "outreach", "score", "question", "track"].includes(activeTab)
  ) {
    activateTab(activeTab);
  }

  updateCharCount();
}

document.addEventListener("DOMContentLoaded", () => {
  restoreAll();
  loadResumes();
  checkHealth();
});
