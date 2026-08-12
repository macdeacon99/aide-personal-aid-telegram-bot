"""Aide v1 — the agent.

v0's flaw: chat and function were separate programs. The model could talk but
not act, so it said "I don't have access to your task system" while reading the
same database that produced the morning brief.

v1 gives the model tools and a loop. It queries current state every turn rather
than relying on a short message window, so "memory" becomes a matter of looking
things up rather than remembering them.
"""
from __future__ import annotations

from datetime import datetime

import anthropic

from . import tools as tools_mod
from .config import CFG
from .db import DB

MAX_TOOL_ROUNDS = 6


def build_system(db: DB) -> str:
    """Assembled fresh each turn so the model always sees current state."""
    now = datetime.now(CFG.tz)
    facts = db.all_facts()
    open_count = len(db.open_tasks())

    fact_block = ""
    if facts:
        fact_block = "\n\nWhat you know about him:\n" + "\n".join(
            f"- {k}: {v}" for k, v in facts.items())

    return f"""You are Aide, Gordon's personal assistant and mentor.

Right now it is {now.strftime('%A %d %B %Y, %H:%M')} ({CFG.tz}).
He has {open_count} open tasks.{fact_block}

CHARACTER
Organised, direct, dry. No flattery, no filler, no corporate warmth. This is
Telegram — short lines, one question per message where you can. You propose, he
decides. You challenge avoidance honestly but don't moralise, and don't lecture
him about balance he didn't ask about.

Scottish register is fine in casual conversation. Clean and plain for anything
technical.

TOOLS — THIS IS THE IMPORTANT BIT
You have real access to his tasks, calendar, reminders and health data. You are
not a chatbot describing what he could do; you are the thing that does it.

- NEVER say you lack access to his tasks, calendar or data. You have tools. Use them.
- NEVER guess at what's on his list. Call list_tasks and read it.
- When he asks you to do something, DO IT, then report what you did.
- When he mentions something he needs to do, add it as a task without being asked.
- When he tells you something durable about himself — preferences, goals, working
  patterns, commitments — call remember so it survives the conversation.
- If he references something you don't have in front of you, search_history.

DESTRUCTIVE ACTIONS
Completing or dropping ALL tasks at once needs explicit confirmation first.
Single or named tasks: just do it, don't be precious.

STYLE
Report actions plainly: "Closed #3, #7 and #9." not "I have gone ahead and
successfully marked those tasks as complete for you!"
If a tool fails, say what failed and what he can do about it. Don't pretend.

You are not a therapist. If something is beyond accountability coaching, say so
plainly and point him at real support."""


class LLM:
    def __init__(self, db: DB):
        self.db = db
        self.client = (anthropic.Anthropic(api_key=CFG.anthropic_api_key)
                       if CFG.anthropic_api_key else None)

    def available(self) -> bool:
        return self.client is not None and self.db.tokens_today() < CFG.daily_token_budget

    def _log_usage(self, resp):
        self.db.log_msg("meta", "[llm]", "llm_call",
                        resp.usage.input_tokens, resp.usage.output_tokens)

    def agent_turn(self, user_text: str) -> str | None:
        """Run a full tool-use loop and return the final text for the user."""
        if not self.available():
            return None

        messages = self._history()
        messages.append({"role": "user", "content": user_text})
        system = build_system(self.db)

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                resp = self.client.messages.create(
                    model=CFG.model_smart,
                    max_tokens=1200,
                    system=system,
                    tools=tools_mod.TOOLS,
                    messages=messages,
                )
            except Exception as e:                            # noqa: BLE001
                return f"Something went wrong talking to my brain: {e}"

            self._log_usage(resp)

            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if b.type == "text").strip()
                return text or None

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                out = tools_mod.execute(block.name, block.input, self.db)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": out,
                })
            messages.append({"role": "user", "content": results})

        return "I got stuck working that out — try asking a different way."

    def _history(self, n: int = 10) -> list[dict]:
        rows = self.db.recent_dialogue(n * 2)
        msgs: list[dict] = []
        for r in rows:
            role = ("user" if r["direction"] == "in"
                    else "assistant" if r["direction"] == "out" else None)
            if role is None or not r["text"].strip():
                continue
            if msgs and msgs[-1]["role"] == role:
                msgs[-1]["content"] += "\n" + r["text"]
            else:
                msgs.append({"role": role, "content": r["text"]})
        while msgs and msgs[0]["role"] == "assistant":
            msgs.pop(0)
        while msgs and msgs[-1]["role"] == "user":
            msgs.pop()
        return msgs[-n:]

    def polish_brief(self, raw_brief: str, context: str = "") -> str | None:
        if not self.available():
            return None
        system = build_system(self.db) + (
            "\n\nRewrite the draft brief below in your voice. Keep every fact, "
            "date and number exactly as given — invent nothing. One phone screen. "
            "End by asking for today's priorities.")
        try:
            resp = self.client.messages.create(
                model=CFG.model_smart, max_tokens=800, system=system,
                messages=[{"role": "user", "content": f"{context}\n\nDRAFT:\n{raw_brief}"}])
            self._log_usage(resp)
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception:
            return None

    def chat(self, text: str) -> str | None:
        return self.agent_turn(text)

    def parse_intent(self, text: str) -> dict:
        """v0 shim — the agent handles intent now."""
        return {"intent": "chat"}
