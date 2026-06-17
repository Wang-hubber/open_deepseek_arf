# arf/core/message_builder.py
"""MessageBuilder — construct the two-layer system message structure.

messages = [
  {role: "system", content: "<Structured System Prompt>"},   # [0]: identity + hard rules
  {role: "system", content: "<system-reminder>"},             # [1]: skills + tools + memory
  ...user/assistant/tool messages...
]
"""
from __future__ import annotations


class MessageBuilder:
    """Construct the initial message list with two system layers."""

    @staticmethod
    def build_initial_messages(
        system_prompt: str,
        system_reminder_parts: list[str] | None = None,
    ) -> list[dict]:
        """Build the initial messages list.

        Args:
            system_prompt: The structured agent identity prompt (fixed).
            system_reminder_parts: Optional sections for the reminder message.
        """
        msgs = [
            {"role": "system", "content": system_prompt},
        ]
        reminder = MessageBuilder.build_reminder(system_reminder_parts or [])
        if reminder["content"]:
            msgs.append(reminder)
        return msgs

    @staticmethod
    def build_reminder(parts: list[str]) -> dict:
        """Build the system-reminder message from named sections.

        Each string in *parts* is a section (e.g. "## Available Skills\n...")
        Empty strings are filtered out.
        """
        non_empty = [p for p in parts if p.strip()]
        if not non_empty:
            return {"role": "system", "content": ""}
        return {"role": "system", "content": "\n\n".join(non_empty)}

    @staticmethod
    def update_reminder(messages: list[dict], reminder_content: str) -> list[dict]:
        """Replace the system-reminder (messages[1]) with new content."""
        if len(messages) >= 2 and messages[1].get("role") == "system":
            if reminder_content:
                messages[1] = {"role": "system", "content": reminder_content}
            else:
                messages.pop(1)
        elif reminder_content:
            messages.insert(1, {"role": "system", "content": reminder_content})
        return messages
