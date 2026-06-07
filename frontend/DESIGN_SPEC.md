# Apply Tools — Warm Minimal Design Spec

A full visual overhaul moving Apply Tools from a purple-gradient / glassmorphism
aesthetic to a **warm minimal**, editorial system: cream paper grounds, warm
near-black ink, soft neutral borders, and a single muted accent. Calm, restrained,
Notion-ish — the data is the hero, the chrome recedes.

This document is the design system. It does **not** rewrite page components; it
defines tokens and utility behavior so existing markup degrades gracefully into
the new look. See `globals.new.css` for the concrete implementation.

---

## 1. Accent choice — **Sage**

**Chosen accent: muted sage green.**

Rationale:

- **It reads "calm + productive," not "salesy."** Apply Tools is a daily-driver
  utility for a stressful task (job hunting). Sage is restful and low-arousal —
  it supports long working sessions where terracotta (warm/urgent) or ink-blue
  (corporate/cold) would either nag or chill the warm paper palette.
- **It harmonizes with cream.** Sage and warm off-white sit adjacent on a warm
  neutral palette; the accent feels like it belongs to the paper rather than
  being stamped on top (which is exactly the glassy-purple problem we're leaving).
- **Semantic colors stay legible.** Because the accent is desaturated green, we
  shift *success* to a deeper forest/teal-green so "sent / success" never collides
  with the brand accent. Info (a muted slate-blue), warning (warm amber), and
  error (warm clay-red) all remain distinct against sage.

The accent is used **sparingly**: active nav, primary buttons, focused inputs,
links, the single highlighted KPI. Everything else is ink-on-paper with hairline
borders.

---

## 2. Color tokens

All tokens map onto the **existing DaisyUI variable names** so no theme plumbing
changes. Two themes preserved: `applylight` (default) and `applydark`.

Philosophy: backgrounds are layered warm neutrals (paper → raised → sunk), text
is warm near-black, borders are a single warm hairline. The accent (`primary`)
and `secondary`/`accent` all resolve to the **same sage family** so legacy markup
referencing `secondary`/`accent` no longer produces a second/third hue — the app
reads as monochrome-plus-sage.

### 2.1 Light — `applylight`

| DaisyUI var | Hex | Role |
|---|---|---|
| `--color-base-100` | `#faf8f4` | App background (warm cream paper) |
| `--color-base-200` | `#f2efe8` | Raised surface / cards / inputs |
| `--color-base-300` | `#e3ded3` | Borders, dividers, sunk wells |
| `--color-base-content` | `#26231d` | Primary text (warm near-black) |
| `--color-primary` | `#5f7355` | Sage accent |
| `--color-primary-content` | `#faf8f4` | Text on accent |
| `--color-secondary` | `#5f7355` | = primary (collapse to one hue) |
| `--color-secondary-content` | `#faf8f4` | |
| `--color-accent` | `#5f7355` | = primary (collapse to one hue) |
| `--color-accent-content` | `#faf8f4` | |
| `--color-neutral` | `#2f2b24` | Dark warm chip / inverse surface |
| `--color-neutral-content` | `#f2efe8` | |
| `--color-info` | `#4f6d80` | Muted slate-blue |
| `--color-info-content` | `#faf8f4` | |
| `--color-success` | `#3f7a5e` | Forest green (distinct from sage accent) |
| `--color-success-content` | `#faf8f4` | |
| `--color-warning` | `#b87f3c` | Warm amber |
| `--color-warning-content` | `#2a1d08` | |
| `--color-error` | `#b4563f` | Warm clay-red |
| `--color-error-content` | `#faf8f4` | |

### 2.2 Dark — `applydark`

Warm charcoal, **not** blue-black (the old dark theme was navy `#0b0f1a`). Paper
becomes warm graphite; ink becomes warm bone. Accent lifts to a lighter sage so it
holds contrast on dark.

| DaisyUI var | Hex | Role |
|---|---|---|
| `--color-base-100` | `#1a1815` | App background (warm graphite) |
| `--color-base-200` | `#23211d` | Raised surface / cards / inputs |
| `--color-base-300` | `#322f29` | Borders, dividers, sunk wells |
| `--color-base-content` | `#ece8e0` | Primary text (warm bone) |
| `--color-primary` | `#9db389` | Sage accent (lifted for dark) |
| `--color-primary-content` | `#17190f` | Text on accent |
| `--color-secondary` | `#9db389` | = primary |
| `--color-secondary-content` | `#17190f` | |
| `--color-accent` | `#9db389` | = primary |
| `--color-accent-content` | `#17190f` | |
| `--color-neutral` | `#322f29` | Warm chip surface |
| `--color-neutral-content` | `#ece8e0` | |
| `--color-info` | `#8aa8bd` | Muted slate-blue |
| `--color-info-content` | `#15191c` | |
| `--color-success` | `#6fae8c` | Forest green |
| `--color-success-content` | `#0d1812` | |
| `--color-warning` | `#d8a45f` | Warm amber |
| `--color-warning-content` | `#241806` | |
| `--color-error` | `#d8826b` | Warm clay-red |
| `--color-error-content` | `#1f0c07` | |

### 2.3 Radii (per theme, unchanged variable names)

| Var | Value | Note |
|---|---|---|
| `--radius-selector` | `9999px` | Pills / circle buttons / toggles |
| `--radius-field` | `0.375rem` | Inputs, buttons, tabs (was `0.5rem` — tightened) |
| `--radius-box` | `0.625rem` | Cards (was `1rem` — flatter, editorial) |

---

## 3. Typography

Move off Inter for display. Keep a refined sans for body but give headings a
distinctive editorial serif to match the "paper" concept. Mono stays for data.

- **Display / headings:** a transitional serif — **Fraunces** (variable, optical
  sizing) or **Newsreader** as fallback. Used for `h1`/`h2` page titles only.
  Warm, literary, pairs with cream paper. *(Requires a one-line font import in
  `layout.tsx` — see §8 follow-ups. The CSS ships a `--font-display` hook and
  falls back to Georgia/serif so nothing breaks if the dev defers the font swap.)*
- **Body / UI:** **Inter** retained but de-emphasized (it's a neutral workhorse
  here, not a brand statement). `font-feature-settings: "ss01","cv11"` kept.
- **Mono:** **Geist Mono** retained for counts, dates, codes, badges.

### Type scale (rem)

| Token | Size / line | Usage |
|---|---|---|
| Display | `1.875rem` / 1.15, serif, weight 500, tracking `-0.02em` | Page `h1` |
| Title | `1.125rem` / 1.3, weight 600 | Card / section `h2` |
| Eyebrow | `0.6875rem` / 1, weight 600, uppercase, tracking `0.12em`, opacity ~55% | Section labels, KPI captions |
| Body | `0.875rem` / 1.55 | Default UI text |
| Small | `0.8125rem` / 1.5 | Secondary text |
| Micro | `0.6875rem` / 1.4 | Meta, timestamps |

Heading weights drop from the old `font-bold` (700) toward **500–600** — minimal
systems carry hierarchy with size + color + space, not weight. Pages still using
`font-bold` on titles will look fine (just a touch heavier than ideal); optional
follow-up is to relax to `font-semibold`.

---

## 4. Spacing, rhythm, borders, shadows

- **Spacing rhythm:** 4px base; primary section gap stays Tailwind `space-y-6`
  (24px) and dashboard `space-y-10` (40px). Card padding settles to **20px**
  (`p-5`) for content cards, **16px** (`p-4`) for compact stat cards.
- **Borders:** one warm hairline, `1px solid var(--color-base-300)`. This is the
  primary separator in the whole system — replaces shadows as the depth cue.
  Full-strength borders (no more `/40` opacity washes) since the neutral is soft.
- **Shadow philosophy:** **flat by default.** No drop shadows on resting cards.
  A single, barely-there shadow (`--shadow-soft`) is available for genuinely
  floating elements (dropdowns, modals, mobile drawer). No glow, no colored
  shadows, no hover lift on cards. Depth = border + background step, not blur.
- **Focus:** a 2px accent ring via `box-shadow` (offset, non-glowing). Functional,
  visible, calm.

---

## 5. Replacing the legacy custom utilities

Every legacy utility is **redefined** rather than deleted, so existing markup keeps
working with zero page edits. Behavior in the new system:

| Legacy utility | Usage | New behavior |
|---|---|---|
| `.glass-card` | 36× | Becomes **`.surface`**: flat `base-200` fill, 1px `base-300` border, `--radius-box`, **no blur, no shadow**. `.glass-card` is aliased to `.surface`. |
| `.glass-card-lift` | 2× | Hover lift removed. Now a no-op (kept so class still parses); hover only shifts border to a slightly stronger neutral. |
| `.gradient-text` | 2× | Gradient removed. Renders as solid `base-content` (the highlighted KPI uses the accent via a new `.accent-text` if desired). No rainbow. |
| `.btn-gradient` | 13× | Becomes a **solid sage button**: `primary` fill, `primary-content` text, flat, 1px border (`primary`), subtle darken on hover, no glow/translate. |
| `.glow-ring` | 5× | Glow removed. `:focus-within` now shows the calm 2px accent ring + accent border. |
| `.nav-link` | 5× | Sliding gradient underline removed. Hover = color shift to `base-content`; `.active` = accent color + a thin static underline (no animation). |
| `.mode-dot-*` | activity feed | Kept (functional category coding) but **glow box-shadows removed** and colors **remapped to the warm palette** (sage / slate / amber / forest / clay) instead of neon indigo/cyan/violet. |
| `.animate-fade-in` | 10× | Kept — a gentle opacity fade on mount is functional (perceived load). Duration trimmed to `0.35s`. |
| `.animate-slide-up` | 16× | Kept but softened: travel reduced `16px → 6px`, duration `0.35s`. Subtle, not bouncy. |
| `.stagger-1..5` | dashboard | Kept (sequence the fade/slide). Delays unchanged. |
| `.animate-pulse-glow` | 1× | **Removed** (decorative). Redefined to no animation so markup doesn't break. Dev should drop the class. |
| `.animate-float` | 1× | **Removed** (decorative). Redefined to no animation. Dev should drop the class. |

`prefers-reduced-motion: reduce` disables all mount animations.

---

## 6. Component guidance

**Cards (`.surface` / `.glass-card`)** — Flat `base-200` panel, 1px `base-300`
border, `--radius-box`. Group related rows with internal `divide-y` using
`base-300`. No shadow at rest. A header row inside the card uses the Eyebrow style.

**Tables (`.table`)** — Hairline horizontal rules only (no vertical lines, no zebra
by default). Header row: Eyebrow style, `base-200` background, bottom border.
Row hover: fill to `base-200` (a quiet wash), 0.15s. Numerics right-aligned,
tabular. Generous row height (≈44px).

**Badges (DaisyUI `.badge`)** — Treated as quiet **tags**, not loud chips.
`badge-ghost` = `base-200` fill + `base-300` border + muted text (the default for
mode/category labels). Status badges use the **soft tonal** pattern: tinted
background at ~12–15% of the semantic color with the solid semantic text color
(e.g. success = forest text on forest-12% — not a saturated solid). `badge-primary`
becomes a soft sage tag. Mono uppercase micro-text for codes.

**Buttons** — Three weights only:
- *Primary* (`.btn-gradient` / `.btn-primary`): solid sage, sage border.
- *Default* (`.btn` / outline): `base-200` fill, `base-300` border, ink text.
- *Ghost* (`.btn-ghost`): transparent, hover to `base-200`.
All flat, `--radius-field`, no shadow, no translate. Hover = small bg/border
darken. Active = none/instant.

**Inputs** — `base-200` fill (or `base-100` for inset feel), 1px `base-300`
border, `--radius-field`. Focus = accent border + 2px accent ring (`.glow-ring`
container or native `:focus`). Placeholder ~45% opacity. Comfortable padding
(`h-9`/`h-10`).

**Sidebar** — Stays structurally identical (machinery in §7). Visual: `base-100`
background (same as app — it's a quiet rail, not a colored panel), 1px `base-300`
right border, **no backdrop blur**. Active link: soft sage fill (`primary` at
~12%) + sage text + medium weight. Inactive: muted ink, hover to `base-200`.
Mobile top bar / drawer: solid `base-100`, hairline border, no blur.

---

## 7. Load-bearing machinery preserved verbatim

These must not change — layout + SSR/hydration depend on them. Carried into
`globals.new.css` unchanged:

- `:root { --sidebar-w: 224px; }` and `[data-sidebar="collapsed"] { --sidebar-w: 68px; }`
- `.sidebar-label` / `.sidebar-icon-only` visibility rules keyed to
  `[data-sidebar="collapsed"] .sidebar-aside ...`
- `.sidebar-brand`, `.sidebar-link` collapse padding/justify rules
- `.sidebar-aside { width: var(--sidebar-w); }`
- `.rb-bullet` contentEditable placeholder + `strong` rules (resume builder)
- DaisyUI plugin block structure (`@plugin "daisyui"`, two `@plugin
  "daisyui/theme"` blocks named `applylight` / `applydark`)
- The custom scrollbar + `.table tbody tr` transition (kept, retuned to neutrals)

---

## 8. Follow-ups for the dev (page-level, optional)

These are *graceful-degradation* gaps — the app works without them, but doing them
realizes the aesthetic fully:

1. **Display font.** Add `Fraunces` (or `Newsreader`) in `layout.tsx` next to
   Inter and expose it as `--font-display`; the CSS already consumes that hook and
   falls back to a system serif. Then optionally set page `h1`s to
   `font-[family-name:var(--font-display)] font-medium` and relax `font-bold`.
2. **Drop dead decorative classes.** Remove the single `.animate-pulse-glow` and
   `.animate-float` usages (they're now no-ops, but cleaner to delete).
3. **Background blur classes.** Sidebar/mobile bar markup still has
   `bg-base-100/80 backdrop-blur`. The new look wants solid `bg-base-100` and no
   blur — change those Tailwind classes in `Sidebar.tsx` (CSS can't override
   Tailwind utilities for the `/80` + `backdrop-blur`). Footer/border `/40`
   opacity washes can stay or be bumped to solid `border-base-300`.
4. **`.gradient-text` on the highlighted KPI.** Now renders solid ink. If you want
   that one number to pop in sage, swap it to `text-primary`.
5. **Tint-heavy stat icons** on the dashboard (`bg-*/10 text-*`) still work but now
   draw from the warm semantic palette; verify the accent KPI reads as intended.

---

## 9. Summary of intent

Cream paper, warm ink, one sage accent, hairline borders, flat surfaces, serif
titles, restrained motion. Hierarchy comes from **space and a single accent**, not
from gradients, glow, or shadow. The result should feel like a well-set document
you can work in all day.
