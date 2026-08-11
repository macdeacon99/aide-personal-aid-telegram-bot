from __future__ import annotations
"""Aide v0 — Anthropic wrapper with model tiering, a daily token budget circuit
breaker, and deterministic fallbacks. The bot must keep working if this fails."""
import json
import re
from datetime import date, timedelta

import anthropic

from .config import CFG
from .db import DB

PERSONA = """You are Aide, Gordon's personal assistant and mentor.
Character: organised, direct, dry-humoured, zero flattery. Telegram, not email —
short lines, one question per message where possible, no markdown walls.
You propose, he decides. You are not a therapist; if something is beyond
accountability coaching, say so plainly and point to real support."""


class LLM:
    def __init__(self, db: DB):
        self.db = db
        self.client = (anthropic.Anthropic(api_key=CFG.anthropic_api_key)
                       if CFG.anthropic_api_key else None)

    def _budget_ok(self) -> bool:
        return self.db.tokens_today() < CFG.daily_token_budget

    def _call(self, model: str, system: str, messages: list[dict],
              max_tokens: int = 800) -> str | None:
        if not self.client or not self._budget_ok():
            return None
        try:
            resp = self.client.messages.create(
                model=model, max_tokens=max_tokens, system=system, messages=messages)
            text = "".join(b.text for b in resp.content if b.type == "text")
            self.db.log_msg("meta", f"[llm:{model}]", "llm_call",
                            resp.usage.input_tokens, resp.usage.output_tokens)
            return text
        except Exception:
            return None

    # ---------- intent parsing (fast model) ----------
    def parse_intent(self, text: str) -> dict:
        """Returns {intent, title?, due?, priority?, reply?}. Falls back to regex."""
        today = date.today()
        system = (
            "Parse the user's Telegram message to a personal-assistant bot into JSON.\n"
            f"Today is {today.isoformat()} ({today.strftime('%A')}).\n"
            'Intents: "add_task" (fields: title, due as ISO date or null, priority 1-4),\n'
            '"complete_task" (field: query - words identifying the task),\n'
            '"defer_task" (fields: query, due or null), "list_tasks", "set_priorities"\n'
            '(field: priorities - array of strings), "chat" (anything else).\n'
            "Respond ONLY with the JSON object. No preamble, no markdown fences.")
        out = self._call(CFG.model_fast, system, [{"role": "user", "content": text}], 300)
        if out:
            try:
                cleaned = re.sub(r"```json|```", "", out).strip()
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict) and "intent" in parsed:
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return self._regex_fallback(text)

    @staticmethod
    def _regex_fallback(text: str) -> dict:
        t = text.strip()
        low = t.lower()
        m = re.match(r"^(add|remind me to|remember to|need to)\s+(.+)$", low)
        if m:
            title = t[m.start(2):]
            due = None
            if "tomorrow" in low:
                due = (date.today() + timedelta(days=1)).isoformat()
                title = re.sub(r"\s*tomorrow\s*", " ", title, flags=re.I).strip()
            elif "today" in low:
                due = date.today().isoformat()
                title = re.sub(r"\s*today\s*", " ", title, flags=re.I).strip()
            return {"intent": "add_task", "title": title, "due": due, "priority": 3}
        if low in ("tasks", "list", "what's on", "whats on"):
            return {"intent": "list_tasks"}
        return {"intent": "chat"}

    # ---------- brief polish (smart model) ----------
    def polish_brief(self, raw_brief: str, context: str) -> str | None:
        system = PERSONA + (
            "\nRewrite the draft morning brief below in your voice. Keep every fact, "
            "date and number exactly as given — do not invent events, tasks or stats. "
            "Fit one phone screen. End by asking for today's priorities.")
        return self._call(CFG.model_smart, system,
                          [{"role": "user", "content": f"{context}\n\nDRAFT:\n{raw_brief}"}], 700)

    # ---------- freeform chat (smart model) ----------
    def chat(self, text: str) -> str | None:
        history = self.db.recent_dialogue(12)
        msgs = []
        for h in history:
            if h["direction"] == "in":
                msgs.append({"role": "user", "content": h["text"]})
            elif h["direction"] == "out":
                msgs.append({"role": "assistant", "content": h["text"]})
        # ensure alternation ends on the new user turn
        msgs = [m for i, m in enumerate(msgs) if i == 0 or m["role"] != msgs[i - 1]["role"]]
        if msgs and msgs[0]["role"] == "assistant":
            msgs = msgs[1:]
        if msgs and msgs[-1]["role"] == "user":
            msgs.pop()
        msgs.append({"role": "user", "content": text})
        return self._call(CFG.model_smart, PERSONA, msgs, 600)
