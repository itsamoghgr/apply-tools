const BACKEND = "http://127.0.0.1:8001";
const LINKEDIN_INVITE_LIMIT = 300;
const LINKEDIN_INVITE_WARN = 280;

// Default timeouts (ms). The backend caps a single LLM call at ~45s, so
// 60s leaves a small buffer for network + cold-start before we abort.
// /generate is longer because tectonic can take 30s+ on first compile.
// /extract-jd runs on fast Groq Llama (~0.3-3s) but can walk a multi-hop
// fallback chain (Groq quota-out -> Bedrock -> Anthropic) when the primary is
// down. The backend caps each hop at EXTRACT_TIMEOUT_SECS (~15s), so allow room
// for a couple of hops + buffer before the client aborts.
const TIMEOUT_LLM_MS = 60_000;
const TIMEOUT_GENERATE_MS = 180_000;
const TIMEOUT_EXTRACT_MS = 50_000;
// Reading the page across all frames (chrome.scripting.executeScript) is
// normally instant but can hang on pages with many/cross-origin frames. Bound
// it so "Detecting..." never freezes.
const TIMEOUT_PAGE_READ_MS = 8_000;

// Populated from GET / at startup. Fallback label is generic so it never
// reads as a lie if the server is offline. `_providerLabel` is the generation
// provider (shown in the health badge); `_extractProviderLabel` is the JD
// auto-detect provider, which is often different (e.g. generation on Bedrock,
// auto-detect on Groq). Defaults to the generation label until health resolves.
let _providerLabel = "AI";
let _extractProviderLabel = "AI";

// Storage keys. The shared company + JD live in one place; only the truly
// tab-local fields (intent, outreach profile, question text, tracker
// extras) are persisted per-tab.
const STORAGE_KEYS = {
  activeTab: "activeTab",
  resumeId: "resume.id",
  shared: { company: "shared.company", jd: "shared.jd" },
  email: { intent: "email.intent" },
  outreach: {
    channel: "outreach.channel",
    profile: "outreach.profile",
    context: "outreach.context",
  },
  question: { text: "question.text" },
  track: {
    role: "track.role",
    location: "track.location",
    jobUrl: "track.jobUrl",
    status: "track.status",
    interview: "track.interview",
    notes: "track.notes",
  },
  lead: {
    name: "lead.name",
    email: "lead.email",
    linkedinUrl: "lead.linkedinUrl",
    role: "lead.role",
    company: "lead.company",
    profile: "lead.profile",
    notes: "lead.notes",
  },
  chat: { messages: "chat.messages" },
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
      try {
        const data = await res.json();
        if (data && typeof data.provider_label === "string" && data.provider_label) {
          _providerLabel = data.provider_label;
          // Default extract label to the generation label, then override if the
          // backend reports a distinct auto-detect provider.
          _extractProviderLabel = data.provider_label;
        }
        if (
          data &&
          typeof data.extract_provider_label === "string" &&
          data.extract_provider_label
        ) {
          _extractProviderLabel = data.extract_provider_label;
        }
      } catch (_e) {
        // older backend without provider_label - keep the fallback.
      }
      healthLabel.textContent = `online · ${_providerLabel}`;
      return true;
    }
    throw new Error(`HTTP ${res.status}`);
  } catch (_e) {
    healthDot.className = "dot bad";
    healthLabel.textContent = "offline";
    return false;
  }
}

// fetch() with a timeout. AbortError ⇒ throw a clearer "timed out" so the
// caller can show a non-cryptic message. Pass through other errors as-is.
async function fetchWithTimeout(url, opts = {}, timeoutMs = TIMEOUT_LLM_MS) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...opts, signal: ctrl.signal });
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw new Error(`request timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// Race a promise against a timeout. Used to bound chrome.scripting.executeScript
// with allFrames:true — injecting into many (cross-origin / sandboxed / ad)
// frames can occasionally never resolve, which would otherwise freeze the
// "Detecting..." status forever. On timeout we reject so the caller can recover.
function withTimeout(promise, timeoutMs, label = "operation") {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`${label} timed out after ${Math.round(timeoutMs / 1000)}s`));
    }, timeoutMs);
    promise.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e) => {
        clearTimeout(timer);
        reject(e);
      },
    );
  });
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

// ---------- textarea autosize ----------
// Popup has no scroll: textareas grow with content instead of using
// internal scrollbars. Call autosize() any time a textarea's value
// changes (typing, programmatic set, restore from storage).

function autosize(el) {
  if (!el) return;
  // Reset height so shrinking works after deletes.
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
}

function wireAutosize() {
  document.querySelectorAll("textarea").forEach((ta) => {
    // The shared JD paste area is intentionally fixed-height + scrollable;
    // skip it so it doesn't grow with content.
    if (ta.classList.contains("jd-textarea")) return;
    // Lead profile: capped height with internal scroll (see popup.css).
    if (ta.classList.contains("lead-scroll-textarea")) return;
    ta.addEventListener("input", () => autosize(ta));
    autosize(ta);
  });
}

// ---------- resume picker ----------

const resumeSelect = $("resumeSelect");
const resumeBar = document.querySelector(".resume-bar");

let _resumeId = DEFAULT_RESUME_ID;

function getResumeId() {
  return _resumeId;
}

async function loadResumes() {
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
  // Re-score with the new resume if a JD is present.
  scheduleSharedScore();
});

// ---------- shared JD context ----------

const sharedCompany = $("shared-company");
const sharedJd = $("shared-jd");
const sharedScore = $("sharedScore");
const sharedScoreNum = sharedScore.querySelector(".score-pill-num");
const sharedScoreVerdict = sharedScore.querySelector(".score-pill-verdict");

function getSharedCompany() {
  return sharedCompany.value.trim();
}

function getSharedJd() {
  return sharedJd.value.trim();
}

function persistShared() {
  storageSet({
    [STORAGE_KEYS.shared.company]: sharedCompany.value,
    [STORAGE_KEYS.shared.jd]: sharedJd.value,
  });
}

sharedCompany.addEventListener("input", () => {
  persistShared();
});
sharedJd.addEventListener("input", () => {
  persistShared();
  scheduleSharedScore();
});

// ---------- tabs ----------

const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");
// Tabs that don't use the shared JD context (just hide the JD block when active).
const TABS_WITHOUT_JD = new Set(["outreach", "lead", "chat"]);
const jdContext = $("jdContext");

function activateTab(name) {
  tabs.forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  panels.forEach((p) => {
    const isActive = p.dataset.panel === name;
    p.classList.toggle("active", isActive);
    if (isActive) {
      // Hidden textareas can't compute scrollHeight; rerun on reveal.
      p.querySelectorAll("textarea").forEach((ta) => {
        if (ta.classList.contains("jd-textarea")) return;
        if (ta.classList.contains("lead-scroll-textarea")) return;
        autosize(ta);
      });
    }
  });
  storageSet({ [STORAGE_KEYS.activeTab]: name });
  jdContext.classList.toggle("hidden", TABS_WITHOUT_JD.has(name));
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => activateTab(tab.dataset.tab));
});

// ---------- score helpers ----------

function scoreBand(score) {
  if (score <= 3) return "band-poor";
  if (score <= 5) return "band-marginal";
  if (score <= 7) return "band-solid";
  return "band-strong";
}

function setSharedScoreLoading() {
  sharedScore.classList.remove("hidden");
  sharedScore.className = "score-pill loading";
  sharedScoreNum.textContent = "-";
  sharedScoreVerdict.textContent = "scoring...";
}

function setSharedScoreResult(score, verdict) {
  sharedScore.classList.remove("hidden");
  sharedScore.className = "score-pill " + scoreBand(score);
  sharedScoreNum.textContent = String(score);
  sharedScoreVerdict.textContent = verdict || "";
}

function hideSharedScore() {
  sharedScore.classList.add("hidden");
}

let _scoreReqId = 0;
let _scoreTimer = null;

async function fetchScoreSilently(jd, company) {
  try {
    const res = await fetchWithTimeout(`${BACKEND}/score`, {
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

// Auto-scoring is intentionally disabled: scoring only runs when the user
// explicitly clicks "Score against all resumes" (the /score-all path below).
// This stays a no-op (rather than deleting the ~5 call sites) so every caller
// remains valid; it just hides the shared score pill instead of firing /score.
function scheduleSharedScore() {
  if (_scoreTimer) clearTimeout(_scoreTimer);
  _scoreReqId += 1; // invalidate any in-flight score response
  hideSharedScore();
}

async function runSharedScore() {
  const jd = getSharedJd();
  const company = getSharedCompany();
  if (jd.length < 200) {
    hideSharedScore();
    return;
  }
  const myReq = ++_scoreReqId;
  setSharedScoreLoading();
  let data = null;
  try {
    data = await fetchScoreSilently(jd, company);
  } catch (_e) {
    data = null;
  }
  if (myReq !== _scoreReqId) return; // a newer request superseded us
  if (data && typeof data.score === "number") {
    setSharedScoreResult(data.score, data.verdict || "");
  } else {
    // Null can mean: validation error (422), timeout, network blip. Either
    // way the pill should not remain in its loading state forever.
    hideSharedScore();
  }
}

// ---------- auto-detect from page ----------

const autoDetectBtn = $("autoDetectBtn");
const autoDetectStatus = $("autoDetectStatus");

function setAutodetectStatus(text, kind = "") {
  autoDetectStatus.textContent = text;
  autoDetectStatus.className = "autodetect-status" + (kind ? " " + kind : "");
}

// Self-contained extractor; runs in the active tab's page context.
function extractJdFromPage() {
  const MIN_JD_LEN = 200;
  const MAX_TEXT = 40000;

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const looksLikeHtml = (s) =>
    typeof s === "string" && /<\/?[a-z][\s\S]*?>/i.test(s);
  const stripHtml = (s) => {
    if (!s) return "";
    const tmp = document.createElement("div");
    tmp.innerHTML = s;
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
      } catch (_e) {}
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
        } catch (_e) {}
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
  } catch (_e) {}

  if (!jd) {
    try {
      const ld = fromJsonLd();
      if (ld.jd) {
        if (!company) company = ld.company;
        jd = ld.jd;
      }
      if (!role && ld.role) role = ld.role;
      if (!job_location && ld.location) job_location = ld.location;
    } catch (_e) {}
  }

  if (!role || !job_location) {
    try {
      const ld = fromJsonLd();
      if (!role && ld.role) role = ld.role;
      if (!job_location && ld.location) job_location = ld.location;
    } catch (_e) {}
  }

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
    // Bounded so a hung frame can't freeze "Detecting..." forever. Try all
    // frames first (covers iframe-embedded JDs); if that times out, fall back
    // to just the top frame, which is enough for most postings.
    let out;
    try {
      out = await withTimeout(
        chrome.scripting.executeScript({
          target: { tabId: tab.id, allFrames: true },
          func: extractJdFromPage,
        }),
        TIMEOUT_PAGE_READ_MS,
        "Reading page",
      );
    } catch (frameErr) {
      console.warn("[Apply Tools] all-frames read failed, retrying top frame:", frameErr);
      out = await withTimeout(
        chrome.scripting.executeScript({
          target: { tabId: tab.id }, // top frame only
          func: extractJdFromPage,
        }),
        TIMEOUT_PAGE_READ_MS,
        "Reading page",
      );
    }
    result = pickBestFrameResult(out);
  } catch (err) {
    setAutodetectStatus(`Failed to read page: ${err.message || err}`, "err");
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
    // Tick an elapsed-seconds counter so a slow provider never looks frozen.
    const startedAt = Date.now();
    setAutodetectStatus(`Detecting via ${_extractProviderLabel}...`, "working");
    const ticker = setInterval(() => {
      const secs = Math.round((Date.now() - startedAt) / 1000);
      setAutodetectStatus(
        `Detecting via ${_extractProviderLabel}... ${secs}s`,
        "working",
      );
    }, 1000);
    try {
      const res = await fetchWithTimeout(
        `${BACKEND}/extract-jd`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: result.url || "",
            page_title: result.page_title || "",
            page_text: result.page_text,
          }),
        },
        TIMEOUT_EXTRACT_MS,
      );
      if (!res.ok) {
        const detail = await readErrorDetail(res);
        throw new Error(detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      company = data.company || "";
      jd = data.job_description || "";
      if (data.job_role) role = data.job_role;
      if (data.location) job_location = data.location;
      source = _extractProviderLabel;
    } catch (err) {
      setAutodetectStatus(`Failed: ${err.message || err}`, "err");
      autoDetectBtn.disabled = false;
      return;
    } finally {
      clearInterval(ticker);
    }
  }

  if (!company && !jd) {
    const msg = source === _extractProviderLabel
      ? "Page doesn't look like a single job posting. Open the specific posting and try again."
      : "This site is supported but the posting markup looks different - try copy/paste.";
    setAutodetectStatus(msg, "err");
    autoDetectBtn.disabled = false;
    return;
  }

  // Always populate the shared fields. JD is fixed-height + scrollable,
  // so no autosize call here.
  sharedCompany.value = company;
  sharedJd.value = jd;
  persistShared();

  // Track tab also gets role / location / jobUrl populated.
  trackRole.value = role || trackRole.value;
  trackLocation.value = job_location || trackLocation.value;
  if (tab && tab.url) trackJobUrl.value = tab.url;
  storageSet({
    [STORAGE_KEYS.track.role]: trackRole.value,
    [STORAGE_KEYS.track.location]: trackLocation.value,
    [STORAGE_KEYS.track.jobUrl]: trackJobUrl.value,
  });

  if (company && !jd) {
    setAutodetectStatus("Detected company but no job description on this page.", "err");
  } else if (!company && jd) {
    setAutodetectStatus("Detected a description but no company name. Fill it in manually.", "err");
  } else {
    setAutodetectStatus(`Filled from ${source}.`, "ok");
  }
  autoDetectBtn.disabled = false;

  // Kick off a fresh score now that the JD changed.
  scheduleSharedScore();
}

autoDetectBtn.addEventListener("click", runAutoDetect);

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
      target.focus();
      target.select();
    }
  });
});

// ============================================================================
// Cover Letter tab
// ============================================================================

const coverSubmit = $("coverSubmit");
const coverTextBtn = $("coverTextBtn");
const coverStatus = $("coverStatus");
const coverTextResult = $("coverTextResult");
const coverBodyOut = $("cover-body-out");

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

coverSubmit.addEventListener("click", async () => {
  const company = getSharedCompany();
  const jd = getSharedJd();
  if (!company || !jd) {
    setStatus(coverStatus, "Fill in company and job description above.", "err");
    return;
  }

  coverSubmit.disabled = true;
  coverTextBtn.disabled = true;
  setStatus(
    coverStatus,
    "Generating PDF... (first compile can take 30s+ while tectonic fetches packages)",
    "working"
  );

  try {
    const res = await fetchWithTimeout(
      `${BACKEND}/generate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          company,
          job_description: jd,
          resume_id: getResumeId(),
        }),
      },
      TIMEOUT_GENERATE_MS,
    );
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
    coverTextBtn.disabled = false;
  }
});

coverTextBtn.addEventListener("click", async () => {
  const company = getSharedCompany();
  const jd = getSharedJd();
  if (!company || !jd) {
    setStatus(coverStatus, "Fill in company and job description above.", "err");
    return;
  }

  coverSubmit.disabled = true;
  coverTextBtn.disabled = true;
  setStatus(coverStatus, "Generating cover letter text...", "working");
  coverTextResult.classList.add("hidden");

  try {
    const res = await fetchWithTimeout(`${BACKEND}/cover-text`, {
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
    autosize(coverBodyOut);
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

const emailIntent = $("email-intent");
const emailSubmit = $("emailSubmit");
const emailStatus = $("emailStatus");
const emailResult = $("emailResult");
const emailSubjectOut = $("email-subject-out");
const emailBodyOut = $("email-body-out");

emailIntent.addEventListener("input", () => {
  storageSet({ [STORAGE_KEYS.email.intent]: emailIntent.value });
});

emailSubmit.addEventListener("click", async () => {
  const company = getSharedCompany();
  const jd = getSharedJd();
  const intent = emailIntent.value.trim();
  if (!company || !jd) {
    setStatus(emailStatus, "Fill in company and job description above.", "err");
    return;
  }

  emailSubmit.disabled = true;
  setStatus(emailStatus, "Generating email...", "working");
  emailResult.classList.add("hidden");

  try {
    const res = await fetchWithTimeout(`${BACKEND}/email`, {
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
    autosize(emailBodyOut);
    setStatus(emailStatus, "Done. Edit if you want, then copy.", "ok");
  } catch (err) {
    setStatus(emailStatus, `Failed: ${err.message || err}`, "err");
  } finally {
    emailSubmit.disabled = false;
  }
});

// ============================================================================
// Outreach tab (uses its own profile + context, not the shared JD)
// ============================================================================

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
outreachProfile.addEventListener("input", () => {
  storageSet({ [STORAGE_KEYS.outreach.profile]: outreachProfile.value });
});
outreachContext.addEventListener("input", () => {
  storageSet({ [STORAGE_KEYS.outreach.context]: outreachContext.value });
});

outreachSubmit.addEventListener("click", async () => {
  const channel = outreachChannel.value;
  const profile = outreachProfile.value.trim();
  const context = outreachContext.value.trim();
  if (!profile) {
    setStatus(outreachStatus, "Paste the target person's profile.", "err");
    return;
  }

  outreachSubmit.disabled = true;
  setStatus(outreachStatus, "Generating message...", "working");
  outreachResult.classList.add("hidden");

  try {
    const res = await fetchWithTimeout(`${BACKEND}/outreach`, {
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
    autosize(outreachMessageOut);
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
// Score tab (scores all resumes against shared JD)
// ============================================================================

const scoreSubmit = $("scoreSubmit");
const scoreStatus = $("scoreStatus");
const scoreList = $("scoreList");

function selectResumeGlobally(resumeId, label) {
  if (!resumeId) return;
  _resumeId = resumeId;
  if (resumeSelect) resumeSelect.value = resumeId;
  storageSet({ [STORAGE_KEYS.resumeId]: resumeId });
  highlightActiveScoreRow(resumeId);
  setStatus(
    scoreStatus,
    `Set "${label || resumeId}" as the active resume for the other tabs.`,
    "ok"
  );
  // Re-run the shared score with the new resume.
  scheduleSharedScore();
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

scoreSubmit.addEventListener("click", async () => {
  const company = getSharedCompany();
  const jd = getSharedJd();
  if (!jd) {
    setStatus(scoreStatus, "Paste a job description above.", "err");
    return;
  }

  scoreSubmit.disabled = true;
  setStatus(scoreStatus, "Scoring against all resumes...", "working");
  scoreList.classList.add("hidden");
  scoreList.innerHTML = "";

  try {
    const res = await fetchWithTimeout(
      `${BACKEND}/score-all`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_description: jd,
          company: company || null,
        }),
      },
      // /score-all fans out over N resumes server-side, so give it more headroom.
      TIMEOUT_GENERATE_MS,
    );
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

const questionText = $("question-text");
const questionSubmit = $("questionSubmit");
const questionStatus = $("questionStatus");
const questionResult = $("questionResult");
const questionAnswerOut = $("question-answer-out");

questionText.addEventListener("input", () => {
  storageSet({ [STORAGE_KEYS.question.text]: questionText.value });
});

questionSubmit.addEventListener("click", async () => {
  const company = getSharedCompany();
  const jd = getSharedJd();
  const question = questionText.value.trim();
  if (!company || !jd || !question) {
    setStatus(
      questionStatus,
      "Fill in company, job description, and the question.",
      "err"
    );
    return;
  }

  questionSubmit.disabled = true;
  setStatus(questionStatus, "Generating answer...", "working");
  questionResult.classList.add("hidden");

  try {
    const res = await fetchWithTimeout(`${BACKEND}/answer-question`, {
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
    autosize(questionAnswerOut);
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

const trackRole = $("track-role");
const trackLocation = $("track-location");
const trackJobUrl = $("track-job-url");
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

// Persist tracker-specific fields on input.
trackRole.addEventListener("input", () => {
  storageSet({ [STORAGE_KEYS.track.role]: trackRole.value });
});
trackLocation.addEventListener("input", () => {
  storageSet({ [STORAGE_KEYS.track.location]: trackLocation.value });
});
trackJobUrl.addEventListener("input", () => {
  storageSet({ [STORAGE_KEYS.track.jobUrl]: trackJobUrl.value });
});
trackStatusSel.addEventListener("change", () => {
  storageSet({ [STORAGE_KEYS.track.status]: trackStatusSel.value });
});
trackInterview.addEventListener("change", () => {
  storageSet({ [STORAGE_KEYS.track.interview]: trackInterview.value });
});
trackNotes.addEventListener("input", () => {
  storageSet({ [STORAGE_KEYS.track.notes]: trackNotes.value });
});

trackSubmit.addEventListener("click", async () => {
  const company = getSharedCompany();
  if (!company) {
    setStatus(trackStatusEl, "Company is required (above).", "err");
    return;
  }

  let jobUrl = trackJobUrl.value.trim();
  if (!jobUrl) {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab && tab.url && !/^(chrome|edge|brave|about|chrome-extension):/.test(tab.url)) {
        jobUrl = tab.url;
      }
    } catch (_e) {}
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
    jobDescription: getSharedJd() || null,
  };

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
// Lead tab
// ============================================================================

const leadName = $("lead-name");
const leadEmail = $("lead-email");
const leadLinkedinUrl = $("lead-linkedin-url");
const leadRole = $("lead-role");
const leadCompany = $("lead-company");
const leadProfile = $("lead-profile");
const leadNotes = $("lead-notes");
const leadAutoDetectBtn = $("leadAutoDetectBtn");
const leadAutoDetectStatus = $("leadAutoDetectStatus");
const leadSubmit = $("leadSubmit");
const leadStatus = $("leadStatus");

// Persist each lead field on input so a half-filled form survives the
// popup closing (which Chrome does the moment focus leaves it). Cleared
// from storage on successful save in the submit handler below.
const LEAD_FIELDS = [
  [leadName, STORAGE_KEYS.lead.name],
  [leadEmail, STORAGE_KEYS.lead.email],
  [leadLinkedinUrl, STORAGE_KEYS.lead.linkedinUrl],
  [leadRole, STORAGE_KEYS.lead.role],
  [leadCompany, STORAGE_KEYS.lead.company],
  [leadProfile, STORAGE_KEYS.lead.profile],
  [leadNotes, STORAGE_KEYS.lead.notes],
];
for (const [el, key] of LEAD_FIELDS) {
  el.addEventListener("input", () => {
    storageSet({ [key]: el.value });
  });
}

function clearLeadStorage() {
  return storageSet(
    Object.fromEntries(LEAD_FIELDS.map(([, key]) => [key, ""])),
  );
}

// Injected into the active tab. Self-contained — anything referenced here
// must be defined inline because it executes in the page's JS context.
function extractLeadFromPage() {
  const url = window.location.href || "";
  const host = window.location.hostname || "";
  const path = window.location.pathname || "";
  // Only LinkedIn profile pages are in scope. /in/<slug>/ is the canonical
  // profile path; /pub/, /sales/, /talent/ are different surfaces and the
  // selectors below don't apply.
  const isLinkedInProfile =
    host.includes("linkedin.com") && /^\/in\//.test(path);
  if (!isLinkedInProfile) {
    return { matched: false, reason: "not-linkedin-profile", url, host };
  }

  // The reliable signal on every LinkedIn profile is `document.title`.
  // It always contains the person's name and follows a small set of
  // formats that we can parse without scraping the DOM. DOM scraping is
  // unreliable here because LinkedIn class-hashes everything per deploy
  // and the page is full of sidebar cards (recommended profiles, promo
  // copy) whose headings can collide with the actual subject's content.
  //
  // Title formats we handle:
  //   "Name | LinkedIn"
  //   "(7) Name | LinkedIn"           ← N unread notifications prefix
  //   "Name - Role - Company | LinkedIn"
  //   "Name | Professional Profile | LinkedIn"
  //   "Name's Profile | LinkedIn"
  function parseTitle(rawTitle) {
    let t = (rawTitle || "").trim();
    if (!t) return { name: "", role: "", company: "" };
    t = t.replace(/^\(\d+\)\s*/, "");
    t = t.replace(/\s*\|\s*LinkedIn\s*$/i, "");
    t = t.replace(/\s*\|\s*Professional Profile\s*$/i, "");
    t = t.replace(/['\u2019]s\s+Profile\b/i, "");

    // " - " separator: "Name - Role" or "Name - Role - Company".
    if (t.includes(" - ")) {
      const parts = t
        .split(" - ")
        .map((s) => s.trim())
        .filter(Boolean);
      return {
        name: parts[0] || "",
        role: parts[1] || "",
        company: parts[2] || "",
      };
    }
    // No role/company encoded in title — just the name.
    return { name: t, role: "", company: "" };
  }

  const parsed = parseTitle(document.title);

  // ---- About + Experience (best-effort) ----
  // LinkedIn keeps semantic anchor divs (#about, #experience) for
  // permalink scrolling. When present, the surrounding <section> holds
  // the visible content. Skipped silently when missing — better to leave
  // the textarea empty than to dump random page text into it.
  function sectionText(anchorId, stripHeading) {
    const anchor = document.getElementById(anchorId);
    if (!anchor) return "";
    const section = anchor.closest("section");
    if (!section) return "";
    let text = (section.innerText || section.textContent || "").trim();
    if (stripHeading && text) {
      text = text
        .replace(new RegExp("^" + stripHeading + "\\s*", "i"), "")
        .trim();
    }
    return text;
  }

  const aboutText = sectionText("about", "About");
  const experienceText = sectionText("experience", "Experience");
  const profileText = [aboutText, experienceText]
    .filter(Boolean)
    .join("\n\n")
    .slice(0, 30000);

  // Canonical profile URL: drop trailing slashes, query strings, and
  // fragment so a URL pasted from a hover card matches one pasted from
  // the address bar.
  const linkedinUrl = `${window.location.origin}${path.replace(/\/+$/, "")}`;

  return {
    matched: !!parsed.name,
    name: parsed.name,
    role: parsed.role,
    currentCompany: parsed.company,
    linkedinUrl,
    profileText,
    host,
    url,
    // Diagnostic counts the popup can surface if extraction fails. We
    // include the parsed title so a paste-back debug session can show
    // exactly what `document.title` looked like at scrape time.
    debug: {
      title: (document.title || "").slice(0, 160),
      hasAboutAnchor: !!document.getElementById("about"),
      hasExperienceAnchor: !!document.getElementById("experience"),
      bodyTextLen: (document.body && document.body.innerText
        ? document.body.innerText.length
        : 0),
    },
  };
}

function setLeadAutoDetectStatus(text, kind = "") {
  setStatus(leadAutoDetectStatus, text, kind);
}

async function runLeadAutoDetect() {
  let tab;
  try {
    [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  } catch (_e) {
    setLeadAutoDetectStatus("Could not read active tab.", "err");
    return;
  }
  if (!tab || !tab.id) {
    setLeadAutoDetectStatus("Could not read active tab.", "err");
    return;
  }
  if (/^(chrome|edge|brave|about|chrome-extension):/.test(tab.url || "")) {
    setLeadAutoDetectStatus(
      "Open a LinkedIn profile (linkedin.com/in/...) and try again.",
      "err",
    );
    return;
  }

  leadAutoDetectBtn.disabled = true;
  setLeadAutoDetectStatus("Detecting...", "working");

  let result;
  try {
    // allFrames: true so embedded LinkedIn surfaces (rare but seen in
    // some profile experiments) don't slip through. We then pick the
    // best result: a `matched` frame wins; otherwise the frame with the
    // longest body text (which typically is the actual profile frame).
    const out = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: true },
      func: extractLeadFromPage,
    });
    const frames = (out || [])
      .map((entry) => entry && entry.result)
      .filter((r) => r && typeof r === "object");
    result =
      frames.find((r) => r.matched) ||
      frames.sort(
        (a, b) =>
          ((b.debug && b.debug.bodyTextLen) || 0) -
          ((a.debug && a.debug.bodyTextLen) || 0),
      )[0];
  } catch (err) {
    setLeadAutoDetectStatus(`Failed to read page: ${err.message || err}`, "err");
    leadAutoDetectBtn.disabled = false;
    return;
  }

  if (!result) {
    setLeadAutoDetectStatus("No data extracted from page.", "err");
    leadAutoDetectBtn.disabled = false;
    return;
  }

  if (!result.matched) {
    if (result.reason === "not-linkedin-profile") {
      setLeadAutoDetectStatus(
        "Open a LinkedIn profile (linkedin.com/in/...) and try again.",
        "err",
      );
    } else {
      const dbg = result.debug;
      // The title is the source of truth for the name; if it didn't
      // include one, the page is probably mid-load or a different
      // LinkedIn surface (login wall, search results overlay).
      console.warn("[lead auto-detect] no match", result);
      const detail = dbg && dbg.title ? ` (title="${dbg.title}")` : "";
      setLeadAutoDetectStatus(
        `Could not detect a name. Make sure the profile finished loading.${detail}`,
        "err",
      );
    }
    leadAutoDetectBtn.disabled = false;
    return;
  }

  // Non-destructive fill: never clobber a value the user already typed.
  // Setting `.value` programmatically doesn't fire an `input` event, so
  // we persist to chrome.storage.local explicitly to keep the draft alive
  // across popup close/reopen.
  function fillIfEmpty(input, value, storageKey) {
    if (!value) return;
    if ((input.value || "").trim() !== "") return;
    input.value = value;
    if (storageKey) storageSet({ [storageKey]: value });
  }

  fillIfEmpty(leadName, result.name, STORAGE_KEYS.lead.name);
  fillIfEmpty(leadRole, result.role, STORAGE_KEYS.lead.role);
  fillIfEmpty(leadCompany, result.currentCompany, STORAGE_KEYS.lead.company);
  fillIfEmpty(leadLinkedinUrl, result.linkedinUrl, STORAGE_KEYS.lead.linkedinUrl);
  fillIfEmpty(leadProfile, result.profileText, STORAGE_KEYS.lead.profile);

  setLeadAutoDetectStatus(`Filled from ${result.host}.`, "ok");
  leadAutoDetectBtn.disabled = false;
}

leadAutoDetectBtn.addEventListener("click", runLeadAutoDetect);

leadSubmit.addEventListener("click", async () => {
  const name = leadName.value.trim();
  if (!name) {
    setStatus(leadStatus, "Name is required.", "err");
    leadName.focus();
    return;
  }

  const payload = {
    name,
    email: leadEmail.value.trim() || null,
    linkedinUrl: leadLinkedinUrl.value.trim() || null,
    linkedinProfile: leadProfile.value.trim() || null,
    currentCompany: leadCompany.value.trim() || null,
    role: leadRole.value.trim() || null,
    notes: leadNotes.value.trim() || null,
  };

  leadSubmit.disabled = true;
  setStatus(leadStatus, "Saving...", "working");

  try {
    const res = await fetch(`${BACKEND}/leads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === 409) {
      const detail = await readErrorDetail(res);
      setStatus(
        leadStatus,
        detail || "A lead with this email already exists.",
        "err",
      );
      leadEmail.focus();
      return;
    }
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      throw new Error(detail || `HTTP ${res.status}`);
    }

    // Success: clear the form so the popup is ready for another lead.
    leadName.value = "";
    leadEmail.value = "";
    leadLinkedinUrl.value = "";
    leadRole.value = "";
    leadCompany.value = "";
    leadProfile.value = "";
    leadNotes.value = "";
    if (typeof autosize === "function") autosize(leadNotes);
    // Wipe the persisted draft so reopening the popup shows an empty form.
    await clearLeadStorage();
    setLeadAutoDetectStatus("", "");
    setStatus(leadStatus, `Saved ${name}.`, "ok");
  } catch (err) {
    setStatus(leadStatus, `Failed: ${err.message || err}`, "err");
  } finally {
    leadSubmit.disabled = false;
  }
});

// ============================================================================
// Restore persisted fields & active tab
// ============================================================================

async function restoreAll() {
  const data = await storageGet([
    STORAGE_KEYS.activeTab,
    STORAGE_KEYS.shared.company,
    STORAGE_KEYS.shared.jd,
    STORAGE_KEYS.email.intent,
    STORAGE_KEYS.outreach.channel,
    STORAGE_KEYS.outreach.profile,
    STORAGE_KEYS.outreach.context,
    STORAGE_KEYS.question.text,
    STORAGE_KEYS.track.role,
    STORAGE_KEYS.track.location,
    STORAGE_KEYS.track.jobUrl,
    STORAGE_KEYS.track.status,
    STORAGE_KEYS.track.interview,
    STORAGE_KEYS.track.notes,
    STORAGE_KEYS.lead.name,
    STORAGE_KEYS.lead.email,
    STORAGE_KEYS.lead.linkedinUrl,
    STORAGE_KEYS.lead.role,
    STORAGE_KEYS.lead.company,
    STORAGE_KEYS.lead.profile,
    STORAGE_KEYS.lead.notes,
    STORAGE_KEYS.chat.messages,
  ]);

  if (data[STORAGE_KEYS.shared.company]) sharedCompany.value = data[STORAGE_KEYS.shared.company];
  if (data[STORAGE_KEYS.shared.jd]) sharedJd.value = data[STORAGE_KEYS.shared.jd];

  if (data[STORAGE_KEYS.email.intent]) emailIntent.value = data[STORAGE_KEYS.email.intent];

  if (data[STORAGE_KEYS.outreach.channel]) outreachChannel.value = data[STORAGE_KEYS.outreach.channel];
  if (data[STORAGE_KEYS.outreach.profile]) outreachProfile.value = data[STORAGE_KEYS.outreach.profile];
  if (data[STORAGE_KEYS.outreach.context]) outreachContext.value = data[STORAGE_KEYS.outreach.context];

  if (data[STORAGE_KEYS.question.text]) questionText.value = data[STORAGE_KEYS.question.text];

  if (data[STORAGE_KEYS.track.role]) trackRole.value = data[STORAGE_KEYS.track.role];
  if (data[STORAGE_KEYS.track.location]) trackLocation.value = data[STORAGE_KEYS.track.location];
  if (data[STORAGE_KEYS.track.jobUrl]) trackJobUrl.value = data[STORAGE_KEYS.track.jobUrl];
  if (data[STORAGE_KEYS.track.status]) trackStatusSel.value = data[STORAGE_KEYS.track.status];
  if (data[STORAGE_KEYS.track.interview]) trackInterview.value = data[STORAGE_KEYS.track.interview];
  if (data[STORAGE_KEYS.track.notes]) trackNotes.value = data[STORAGE_KEYS.track.notes];
  // Always default Applied date to today on popup load.
  trackAppliedDate.value = todayIsoDate();

  if (data[STORAGE_KEYS.lead.name]) leadName.value = data[STORAGE_KEYS.lead.name];
  if (data[STORAGE_KEYS.lead.email]) leadEmail.value = data[STORAGE_KEYS.lead.email];
  if (data[STORAGE_KEYS.lead.linkedinUrl])
    leadLinkedinUrl.value = data[STORAGE_KEYS.lead.linkedinUrl];
  if (data[STORAGE_KEYS.lead.role]) leadRole.value = data[STORAGE_KEYS.lead.role];
  if (data[STORAGE_KEYS.lead.company]) leadCompany.value = data[STORAGE_KEYS.lead.company];
  if (data[STORAGE_KEYS.lead.profile]) leadProfile.value = data[STORAGE_KEYS.lead.profile];
  if (data[STORAGE_KEYS.lead.notes]) leadNotes.value = data[STORAGE_KEYS.lead.notes];

  restoreChat(data[STORAGE_KEYS.chat.messages]);

  const activeTab = data[STORAGE_KEYS.activeTab];
  if (
    activeTab &&
    ["cover", "email", "outreach", "score", "question", "track", "lead", "chat"].includes(activeTab)
  ) {
    activateTab(activeTab);
  } else {
    // Make sure the default active tab also syncs JD-context visibility.
    activateTab("cover");
  }

  updateCharCount();

  // If there's already a JD in storage, kick off a score on open.
  if (getSharedJd().length >= 200) scheduleSharedScore();
}

// ============================================================================
// Chat tab — free-form assistant backed by Bedrock (POST /chat).
// ============================================================================

const chatThread = $("chatThread");
const chatEmpty = $("chatEmpty");
const chatStatus = $("chatStatus");
const chatForm = $("chatForm");
const chatInput = $("chat-input");
const chatSend = $("chatSend");
const chatClear = $("chatClear");

// In-memory transcript: [{ role: "user"|"assistant", content }]. Persisted to
// extension storage so the conversation survives popup close/reopen.
let chatMessages = [];
let chatBusy = false;

function persistChat() {
  storageSet({ [STORAGE_KEYS.chat.messages]: JSON.stringify(chatMessages) });
}

// Render one message bubble. `pending` marks the in-flight assistant turn so
// we can replace it in place once the reply (or an error) arrives.
function appendBubble(role, text, { pending = false } = {}) {
  if (chatEmpty) chatEmpty.style.display = "none";
  const el = document.createElement("div");
  el.className = `chat-msg ${role}` + (pending ? " pending" : "");
  el.textContent = text;
  chatThread.appendChild(el);
  chatThread.scrollTop = chatThread.scrollHeight;
  return el;
}

function restoreChat(raw) {
  if (!raw) return;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (_e) {
    return;
  }
  if (!Array.isArray(parsed) || parsed.length === 0) return;
  chatMessages = parsed.filter(
    (m) => m && (m.role === "user" || m.role === "assistant") && m.content
  );
  for (const m of chatMessages) appendBubble(m.role, m.content);
}

function autosizeChatInput() {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 140) + "px";
}

async function sendChat() {
  if (chatBusy) return;
  const text = chatInput.value.trim();
  if (!text) return;

  chatBusy = true;
  chatSend.disabled = true;
  setStatus(chatStatus, "", "");

  chatMessages.push({ role: "user", content: text });
  appendBubble("user", text);
  persistChat();

  chatInput.value = "";
  autosizeChatInput();

  const pending = appendBubble("assistant", "Thinking…", { pending: true });

  try {
    const res = await fetchWithTimeout(`${BACKEND}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chatMessages }),
    });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      throw new Error(detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    const reply = (data.reply || "").trim();
    if (!reply) throw new Error("Empty response");
    pending.classList.remove("pending");
    pending.textContent = reply;
    chatMessages.push({ role: "assistant", content: reply });
    persistChat();
  } catch (err) {
    // Drop the failed turn's placeholder and let the user retry. Keep the
    // user message in the transcript so they don't have to retype it.
    pending.remove();
    setStatus(chatStatus, `Failed: ${err.message || err}`, "err");
  } finally {
    chatThread.scrollTop = chatThread.scrollHeight;
    chatBusy = false;
    chatSend.disabled = false;
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  sendChat();
});

// Enter sends; Shift+Enter inserts a newline.
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});

chatInput.addEventListener("input", autosizeChatInput);

chatClear.addEventListener("click", () => {
  chatMessages = [];
  persistChat();
  chatThread.querySelectorAll(".chat-msg").forEach((el) => el.remove());
  if (chatEmpty) chatEmpty.style.display = "";
  setStatus(chatStatus, "", "");
  chatInput.focus();
});

document.addEventListener("DOMContentLoaded", () => {
  wireAutosize();
  restoreAll();
  loadResumes();
  checkHealth();
});
