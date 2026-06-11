# The Lead-Finder, Explained Simply

This is a plain-English walkthrough of the lead-generation service — what it does,
why it's built the way it is, and how it came together phase by phase. No jargon
where it can be avoided. For the precise technical reference, see `docs/dev.md`
and `agent_server/CONTRACTS.md`.

---

## What does it actually do?

You press a button. The service goes out onto the open web, finds startups that
recently raised money, figures out who founded each one and how to reach them,
double-checks the details, and drops the clean results into your main app's
database. It keeps going until it has **50 good leads**, then stops.

You don't wait around while it works. The moment you press the button it says
"got it, here's your job number" and does everything in the background. You can
ask "how's it going?" any time with that job number.

---

## The one big idea: smart at the edges, dumb in the middle

The system has two kinds of work:

- **Thinking work** — reading messy web articles and pulling out "this is a
  company, here's its founder." That's genuinely hard, so we use an AI for it.
- **Bookkeeping work** — counting to 50, removing duplicates, checking a box,
  saving a row. That's simple and must be 100% reliable, so it's plain code with
  no AI anywhere near it.

We deliberately keep the AI **only at the two edges** (finding companies, and
researching each one). The whole middle — the loop that ties it together, the
duplicate-removal, the fact-checking, the saving — is ordinary, predictable code.
This is the rule we kept coming back to: *let the AI be creative where creativity
helps, and keep everything else boringly reliable.*

---

## The two separate programs

There are **two programs**, and they stay out of each other's way:

1. **Your existing app** ("the platform") — already running. It owns the real list
   of leads. We only added two small doors to it: one to ask "do you already know
   this company?" and one to "save this company."
2. **The new lead-finder** — a brand-new program on its own port. It does all the
   hunting, then knocks on those two doors to save results.

They each have their **own database**. The platform's database is the real,
permanent home for leads. The lead-finder's database is just a scratchpad — job
status, a memory of what it's already seen, an outbox for safe delivery, and a
diary of what the AI did. The scratchpad never holds the "official" copy of a lead.

---

## The journey of a single hunt

1. **Discover.** The AI reads the open web — tech-news, funding announcements,
   accelerator posts — and writes down companies it finds. To make sure it always
   has *something* solid, it also pulls from a few free, reliable lists
   (Y Combinator's public company list, Product Hunt, funding news feeds). The
   open-web reading is the special sauce (fresh, unusual finds); the free lists are
   the dependable floor.
2. **De-duplicate.** Plain code cleans the messy pile: it tidies up each web
   address, throws out repeats, and skips anything the platform already knows or
   that we've already rejected before. (The AI is *not* allowed to do this — it has
   to be exact.)
3. **Research — for each company, one at a time.** The AI digs up the funding
   round, the founder's name, and the founder's LinkedIn link. Two firm rules:
   if we already got the funding from one of the reliable lists, don't waste effort
   re-finding it; and find the LinkedIn link only from public search results —
   never open the actual LinkedIn page.
4. **Verify.** Plain code scores how trustworthy the lead is, mostly by trying to
   confirm a real email address (using free email-checking services first, with a
   weak email "knock on the door" only as a last resort). The result is a
   **confidence score from 0 to 1**, not a yes/no — so a human can judge borderline
   ones.
5. **Deliver — safely.** The lead is first written to an "outbox" in the scratchpad
   (so it can never be lost), then handed to the platform. If the platform is down,
   it stays in the outbox and gets retried later. Saving the same company twice just
   updates it — never creates a duplicate.
6. **Loop.** Add one to the counter, pause briefly, move to the next company. Stop
   the instant we hit 50 — or when we simply run out of companies.

---

## How it was built — the phases

We built it in deliberate layers, proving each one worked before stacking the next.
A small team of focused builders worked in parallel where it was safe, all coding
against one **frozen agreement** (`CONTRACTS.md`) so their pieces fit together.

### Phase 0 — Agree on the shapes first
Before anyone wrote real logic, we froze the "shapes": exactly what a *company*
record looks like, what a *finished lead* looks like, what the web-search tool
returns, what the two database doors expect. This is the single most important step
— it's why parallel work didn't collide. We also built the scratchpad database
(tables for jobs, memory, outbox, diary) and set up the platform's two new doors.
*Result: the contracts and database, proven with tests.*

### Phase 1 — Make the whole loop run with fakes
We built the front door (press button → get job number) and the central loop, but
with **fake** stand-ins for the hard parts. This proved the plumbing end-to-end:
press the button, watch it loop 80 fake companies, stop exactly at 50, save to the
scratchpad. No AI, no internet — just proof the skeleton works.
*Result: the full loop runs and stops correctly.*

### Phase 2 — Build the reliable middle
Now the real bookkeeping: the shared web tools (a free search, and a page-reader
that politely refuses to ever open LinkedIn), the duplicate-remover, the
fact-checker with its confidence score, and the safe outbox delivery.
*Result: every "dumb but reliable" piece, fully tested.*

### Phase 3 — Build the smart edges
The two AI agents: the discoverer (reads the web + pulls the reliable lists) and
the researcher (funding + founder + LinkedIn-from-search-only). Each is given a
strict budget of steps so it can't run away or run up a bill.
*Result: the creative parts, tested with a fake AI so we don't need a real one to
run the tests.*

### Phase 4 — Plug it all in and run it for real
We connected the real edges to the reliable middle and **ran an actual hunt against
the live internet and a real AI** (AWS Bedrock, Claude Sonnet). The first real run
surfaced two genuine bugs in how the AI conversation was being recorded — fixed
them, added tests so they can't come back, and ran again. The second run found
real, fresh startups (e.g. *Tessl* — Series A, founder Guy Podjarny; *Greptile* —
Seed, founder Daksh Gupta), researched them, and saved them to the platform.
*Result: the whole thing works for real, plus these two guides.*

---

## A note on honesty and resilience

Two things we made sure of, because they matter more than features:

- **It tells the truth about itself.** Every step is written to a diary you can
  read. If the AI's key is invalid, it says so and quietly falls back to the
  reliable lists rather than pretending. If the platform is down, the lead waits
  safely instead of vanishing.
- **One bad apple never spoils the run.** If a single company trips up the AI or a
  web page won't load, that one is logged and skipped — the hunt keeps going.

---

## Trying it yourself

See `docs/dev.md` section 8 for exact commands. The short version:

```
# start the finder
python -m agent_server.api.main

# tell it to hunt for 3 leads
curl -XPOST localhost:8001/api/v1/hunt -d '{"target_count":3,"query_hint":"AI dev tools startups"}'

# check progress with the job id it gives back
curl localhost:8001/api/v1/hunt/<job_id>
```

Add `"dry_run": true` to do a full practice run that finds and checks everything
but doesn't actually save to the platform — handy for a safe try.
