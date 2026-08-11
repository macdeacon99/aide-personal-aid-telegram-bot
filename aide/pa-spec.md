# "Aide" — Personal Assistant Agent
## Full Technical & Behavioural Specification v1.0

**Author:** Gordon (with Claude)
**Date:** August 2026
**Status:** Draft for build

---

## 1. Purpose

A proactive, always-on personal assistant that communicates exclusively via Telegram. It manages the user's schedule, captures and prioritises tasks, delivers structured daily briefs, nudges throughout the day, and acts as a long-horizon coach that aligns daily activity with the user's life goals. It initiates conversation — it does not wait to be asked.

**Design principle:** The assistant behaves like a competent human PA with mentor instincts — organised, direct, respectful of attention, and relentless about follow-through.

---

## 2. Core Capabilities (Functional Requirements)

### FR-1: Telegram as the sole interface
- All interaction happens through a private Telegram bot (Bot API, long-polling or webhook).
- Supports: text messages, inline keyboards (for quick replies: ✅ Done / ⏭ Defer / ❌ Drop), voice notes (transcribed via Whisper or similar before processing).
- The bot must respond within ~5 seconds for conversational turns; scheduled jobs may take longer.
- Single-user. The bot must hard-reject any Telegram user ID other than the owner's.

### FR-2: Morning Brief (daily, configurable time, default 07:00 weekdays / 08:30 weekends)
The brief must include:
1. **Today's calendar** — pulled live from the user's calendar(s), with travel-time warnings for events with locations.
2. **Carry-over tasks** — anything incomplete from yesterday, flagged explicitly ("this is the 3rd day this has rolled over").
3. **Top 3 suggested priorities** — proposed by the assistant based on deadlines, goals alignment, and carry-over age.
4. **A direct question:** "What are your priorities today? Confirm mine, or tell me yours."
5. **One goal-alignment line** — a single sentence connecting today to a life goal (see FR-6).
- After the user replies, the assistant builds a **time-blocked plan** for the day around fixed calendar events and sends it back for confirmation.

### FR-3: Intraday check-ins
- Default cadence: 3 check-ins (mid-morning ~10:30, post-lunch ~13:30, late afternoon ~16:30). All configurable.
- Each check-in asks, in rotation/context: "Anything new landed on your plate?", progress on the current time block, and whether the plan needs re-shuffling.
- **Adaptive silence:** if the user's calendar shows a meeting or a focus block, the check-in is deferred until it ends. Never interrupt a marked focus block.
- Check-ins that go unanswered for 2+ hours are dropped, not repeated (no double-nagging). The missed items roll into the next check-in.

### FR-4: Task capture & management
- Any message at any time can create a task: "add X", "remind me to Y", or free-form ("need to sort the MOT next week") — the assistant parses intent, due date, and priority.
- Tasks stored with: title, notes, due date, priority (P1–P4), effort estimate, goal linkage (see FR-6), source, created/completed timestamps, defer count.
- Tasks sync **bidirectionally** with the system of record (Apple Reminders via calendar/reminders bridge, or a local SQLite DB as the primary store with Reminders as a mirror — decide at build time; SQLite-primary is recommended for queryability).
- Voice note → task must work ("remind me to order the press plates" spoken while driving).

### FR-5: Schedule management & optimisation
- Read/write access to the user's calendar.
- On request or when the plan breaks (meeting overruns, new urgent task), the assistant re-plans the remainder of the day and sends the delta, not the whole plan again.
- **Optimisation rules:**
  - Deep/creative work scheduled in the user's stated peak hours (captured during onboarding, refined over time from completion data).
  - Similar shallow tasks batched (admin, emails, calls).
  - Hard 25% buffer — never schedule a day past 75% capacity.
  - Respect protected personal time (evenings with partner, workshop time, range days) as immovable unless the user explicitly overrides.
- Weekly capacity view: warns on any day already >75% booked at week start.

### FR-6: Goals engine (the differentiator)
- A `goals.md` file (or DB table) holds the user's life goals in three tiers:
  - **Horizon goals** (3–10 yr): e.g. financial independence, business ownership, industry positioning.
  - **Yearly objectives**: concrete, measurable.
  - **Quarterly key results**: what "on track" means this quarter.
- Every task can be tagged to a goal. Untagged tasks are fine, but the assistant tracks the **goal-aligned ratio** of completed work.
- The morning brief and weekly review reference goals explicitly: "You've spent 0 hours on [objective] this week — want me to block Thursday evening for it?"
- Goals are reviewed quarterly in a dedicated guided session the assistant initiates.

### FR-7: Coach / mentor mode
- **Tone:** direct, honest, no sycophancy. Challenges avoidance ("You've deferred this 4 times — is it actually a priority, or should we kill it?"). Celebrates real wins briefly, without gushing.
- **Weekly review (Sunday evening, ~15 min guided conversation):**
  - Wins, misses, deferred-task autopsy, goal progress, next week's top 3.
  - One reflective question per week (rotating: energy, habits, confidence, relationships, health).
- **Monthly deep review:** trends over 4 weeks — completion rate, goal-aligned hours, defer patterns, check-in responsiveness. Delivered as a short written report.
- **Confidence & habit support:** tracks 2–3 user-chosen habits (e.g. gym, reading, no-phone mornings) with light streak accountability. Frames setbacks as data, not failure. Never moralises.
- **Hard boundary:** the coach is a productivity/accountability mentor, not a therapist. If conversations turn to serious mental health territory, it says so plainly and suggests proper support.

### FR-8: Evening shutdown (default 21:00, optional)
- Two-line summary: what got done, what rolls over.
- One question: "Anything on your mind for tomorrow?" — captured, not discussed (protects the evening).

### FR-9: Device telemetry (phone OS data feeding the goals engine)
- The assistant consumes health and usage data from the user's phone and ties it to goals/habits: **step count, sleep, workout minutes, screen time (total + per-app for flagged apps)**.
- **Architecture: push, not pull.** iOS exposes no server-side API for HealthKit or Screen Time, so the phone sends the data:
  - **Health data:** iOS Shortcuts personal automation (or the Health Auto Export app) runs daily at ~06:30 and POSTs a JSON payload to the bot's `/ingest` endpoint over HTTPS with a shared-secret header.
  - **Screen time:** Shortcuts automation reading Screen Time isn't first-class; options are (a) a nightly manual 1-tap shortcut that grabs the Screen Time summary, (b) a small companion app using the DeviceActivity framework (proper solution, later phase), or (c) self-reported via a 1-tap check in the evening shutdown as fallback.
- **Usage in briefs and reviews:**
  - Morning brief: yesterday's numbers vs. target in one line ("Steps 4.2k vs 8k target. Screen time 3h41 — 1h12 of it on Instagram.").
  - Goal-linked: if a goal is "use the phone less," screen time becomes a tracked measure with weekly trend in the Sunday review; same for steps/gym against health goals.
  - Coach mode uses trends, not single days — no scolding over one bad Tuesday.
- **Privacy:** telemetry lands in the user's own SQLite, nowhere else; the ingest endpoint requires the shared secret and rejects everything else; payloads capped at 64KB.
- **Degradation:** if no payload arrives by brief time, the brief simply omits the line — never blocks, never nags about missing data more than once a week.

---

## 3. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | **Availability:** Scheduled messages must fire even when the user's laptop is off → the agent runs server-side (VPS/cloud), not on a desktop app. |
| NFR-2 | **Latency:** Conversational replies < 5s p95. |
| NFR-3 | **Privacy:** All state (tasks, goals, transcripts) stored on infrastructure the user controls. No third-party analytics. Telegram bot token, API keys, and calendar credentials in a secrets manager or encrypted env, never in the repo. |
| NFR-4 | **Cost control:** Daily LLM spend cap with a circuit breaker; use a cheap/fast model for parsing and routing, a frontier model only for planning, reviews, and coaching turns. Target < £30/month. |
| NFR-5 | **Reliability:** Missed cron runs (host down) execute once on recovery, never stacked. Idempotent job design. |
| NFR-6 | **Auditability:** Every message and state change logged locally; 90-day retention. |
| NFR-7 | **Fail-safe:** If the LLM call fails, the bot still sends the deterministic parts (calendar list, task list) and notes the failure. The assistant degrades, never disappears. |

---

## 4. Architecture

```
┌─────────────┐   webhook/poll   ┌──────────────────────────────┐
│  Telegram    │◄────────────────►│  Bot Gateway (Go or Python)  │
│  (user)      │                  │  - auth (owner ID only)      │
└─────────────┘                  │  - message router            │
                                  └──────┬───────────────────────┘
                                         │
                    ┌────────────────────┼─────────────────────┐
                    ▼                    ▼                     ▼
          ┌───────────────┐   ┌──────────────────┐   ┌───────────────┐
          │ Scheduler      │   │ Agent Core        │   │ State Store    │
          │ (cron/temporal)│   │ (Claude API)      │   │ (SQLite +      │
          │ - brief        │   │ - intent parse    │   │  goals.md +    │
          │ - check-ins    │   │ - planning        │   │  logs)         │
          │ - reviews      │   │ - coaching        │   └───────────────┘
          └───────────────┘   │ - tool calls      │
                               └───────┬──────────┘
                                       │ tools
                        ┌──────────────┼──────────────┐
                        ▼              ▼              ▼
                 ┌────────────┐ ┌────────────┐ ┌────────────┐
                 │ Calendar    │ │ Tasks CRUD  │ │ Transcribe  │
                 │ (CalDAV /   │ │ (SQLite)    │ │ (voice →    │
                 │  Google API)│ │             │ │  text)      │
                 └────────────┘ └────────────┘ └────────────┘
```

**Recommended stack** (given the user's existing skills):
- **Language:** Go (gateway + scheduler) or Python (faster to prototype; `python-telegram-bot` + `APScheduler`).
- **LLM:** Anthropic API — Haiku for routing/parsing, Sonnet/Opus for planning and coaching turns. System prompt assembled per-turn from: persona file + goals.md + today's state + last N conversation turns.
- **Memory:** Rolling conversation window (last ~20 turns) + daily summary compaction into a `journal` table. Weekly/monthly reviews read the journal, not raw transcripts.
- **Host:** Small VPS (Hetzner/DO, ~£5/mo) or a Pi at home behind Tailscale. Deployed via the user's normal Terraform/GitOps flow — the whole assistant should be infrastructure-as-code, config in a repo, secrets external.
- **Alternative low-code path:** Claude Code remote routines or Cowork scheduled tasks + a thin Telegram relay — faster to stand up, less control over memory and cadence logic. Fine as a v0 to validate the workflow before building the real thing.

---

## 5. Data Model (minimum)

```sql
tasks(id, title, notes, due, priority, effort_min, goal_id,
      status, defer_count, created_at, completed_at, source)
goals(id, tier, title, measure, target_date, status)
habits(id, name, cadence, current_streak, best_streak)
journal(date, summary, wins, misses, mood_note)
plans(date, blocks_json, confirmed_at, replan_count)
messages(id, ts, direction, text, intent, tokens_in, tokens_out)
config(key, value)  -- brief time, check-in times, quiet hours, caps
```

---

## 6. Persona File (system prompt skeleton)

```
You are [NAME], Gordon's personal assistant and mentor.

Character: organised, direct, dry-humoured, zero flattery. You speak
plainly and briefly — this is Telegram, not email. You are proactive:
you ask, you chase, you propose. You respect attention: one question
per message where possible, never repeat an unanswered nudge.

You know his goals (attached) and you quietly steer every day toward
them. You challenge deferral and avoidance honestly but without
moralising. You protect his personal time as hard as his work time.

You are not a therapist. If something is beyond accountability
coaching, say so and point to real support.

Formatting: short lines, minimal emoji (✅ ⏭ ❌ for buttons only),
no markdown walls. A brief should fit on one phone screen.
```

---

## 7. Message Schedule (defaults, all user-configurable)

| Time | Job | Skippable? |
|------|-----|-----------|
| 07:00 Mon–Fri | Morning brief + priorities question | No |
| 08:30 Sat–Sun | Light weekend brief | Yes |
| 10:30 / 13:30 / 16:30 | Check-ins (deferred around meetings/focus blocks) | Auto-skip if in DND |
| 21:00 daily | Evening shutdown | Yes |
| Sun 19:00 | Weekly review (guided, ~15 min) | Prompted, can defer once |
| 1st Sun of month | Monthly report | No |
| Quarterly | Goals review session | Prompted |
| Ad hoc | Re-plan on calendar change or urgent task | — |

**Quiet hours:** 22:00–06:30 hard silence. Configurable DND command (`/dnd 2h`).

---

## 8. Commands (explicit escape hatches alongside natural language)

```
/brief        force the morning brief now
/plan         show today's current time-blocked plan
/add …        quick task add
/done …       complete a task
/defer …      push a task (increments defer_count)
/goals        show goals + this quarter's progress
/review       start weekly review early
/dnd [dur]    silence check-ins
/pause [days] full vacation mode (briefs off, capture still on)
/config       adjust times, cadence, tone intensity
```

---

## 9. Extended Features — ALL IN SCOPE (confirmed by user, treat as core requirements)

1. **Defer-count autopsy** — the single highest-value coaching mechanic. Tasks deferred 3+ times trigger a forced decision: do it now, schedule it immovably, delegate it, or delete it.
2. **Energy-aware planning** — a 1-tap energy check at the morning brief (🔥/😐/🪫); low-energy days get shallow-task-heavy plans instead of a guilt trip.
3. **Meeting prep nudges** — 30 min before any calendar event with attendees: one-line context + "anything you need prepped?"
4. **Someday/maybe inbox** — a parking lot for ideas (business angles, projects) reviewed monthly, so ideas are captured without cluttering the active list.
5. **Vacation mode** — full pause with a re-entry brief ("here's what accumulated") on return. Non-negotiable for actually switching off on trips.
6. **Weekly time audit** — planned vs. actual hours per goal category; this is where "optimise my life" becomes measurable rather than vibes.
7. **Tone dial** — `gentle | standard | drill-sergeant` setting for the coach voice, adjustable per week.
8. **Onboarding interview** — a one-off 20-minute structured conversation on first run: goals, peak hours, protected time, habits to track, pet peeves. Everything downstream is seeded from this.
9. **Escalation ladder** — P1 tasks approaching deadline get exactly one extra nudge outside normal cadence; nothing else ever does.
10. **Data export** — `/export` dumps everything to JSON/CSV. Your data, always.

---

## 10. Build Phases

| Phase | Scope | Effort |
|-------|-------|--------|
| **v0** | Telegram bot + morning brief (calendar read-only) + task capture (/add + natural language) + SQLite + telemetry ingest endpoint (steps/sleep/screen time in the brief) + onboarding interview + quiet hours. Validate the daily rhythm. | 1–2 weekends |
| **v1** | Check-ins with adaptive silence, time-block planning, calendar write, evening shutdown + energy check, defer autopsy, escalation ladder. | +2 weekends |
| **v2** | Goals engine + goal-aligned ratio, weekly review + time audit, habits/streaks, someday-maybe inbox, meeting prep nudges. | +2 weekends |
| **v3** | Monthly reports, quarterly goals sessions, voice notes, tone dial, vacation mode, /export, Screen Time companion app (DeviceActivity). | Ongoing |

**Success criteria for v0:** after two weeks, the morning brief is something the user reads every day and the task list has become the single source of truth. If not, fix the rhythm before adding features.

---

## 11. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Nag fatigue → user mutes the bot | One-question messages, adaptive silence, no repeat nudges, tone dial, hard quiet hours |
| Assistant becomes a novelty that gets abandoned | v0 success gate; weekly review asks "is this still useful?" monthly |
| LLM cost creep | Model tiering, daily spend cap, deterministic fallbacks |
| Calendar API auth expiry breaks briefs | Token refresh monitoring + failure alert via the bot itself |
| Over-reliance / outsourced judgement | Coach persona explicitly asks the user to decide; assistant proposes, never dictates |
| Secrets leakage | Secrets manager, repo scanning, owner-ID-only gateway |
