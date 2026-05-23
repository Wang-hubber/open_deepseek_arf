"""RuleBasedMemoryWriter — extract facts from conversation turns using heuristics."""
from __future__ import annotations

import re
import time
import uuid

from arf.core.protocols import MemoryEntry, MemoryStore, MemoryWriter

# Keyword → category mapping.  English + Chinese.
_EXTRACTION_RULES: list[tuple[list[str], str]] = [
    # Preference: things the user likes / wants / chooses
    (["prefer", "prefers", "preference", "like", "love", "favorite",
      "偏好", "喜欢", "喜爱", "最爱", "倾向于", "倾向", "选择"], "preference"),
    # Fact: statements about what IS
    (["名字是", "名称是", "叫做", "叫", "我是", "他是", "她是", "位于",
      "住在", "工作是", "职业是", "电话", "邮箱", "地址是",
      "name is", "located in", "works as", "email is"], "fact"),
    # Decision: agreements / conclusions / action items
    (["决定", "确认", "同意", "批准", "采纳", "按照", "使用",
      "decided", "confirmed", "agreed", "approved", "will use"], "decision"),
    # Always / never rules
    (["always", "never", "must", "should", "don't", "cannot",
      "总是", "从不", "必须", "应该", "不能", "不要", "禁止"], "preference"),
    # Remember directive: explicit memory command
    (["remember", "记住", "记下", "记录", "备忘", "提醒"], "fact"),
]

# Maximum characters per extracted entry
_MAX_CHARS = 500


class RuleBasedMemoryWriter:
    """Heuristic memory writer with English + Chinese keyword support.

    Scans assistant messages for patterns indicating the user expressed
    a preference, stated a fact, made a decision, or gave a rule.
    Extracts the relevant content and persists it via MemoryStore.

    For production-quality extraction, inject a model_capable callback
    that calls an LLM to perform structured extraction from the raw turn
    messages.
    """

    def __init__(self, model_call: callable | None = None) -> None:
        self._call_model = model_call

    async def extract_and_write(
        self,
        store: MemoryStore,
        turn_messages: list[dict],
        existing_entries: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        new_entries: list[MemoryEntry] = []
        seen_contents: set[str] = {e.content for e in existing_entries}

        for msg in turn_messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "").strip()
            if not content or len(content) < 10:
                continue

            category = self._classify(content)
            if category is None:
                continue

            snippet = content[:_MAX_CHARS]
            if snippet in seen_contents:
                continue
            seen_contents.add(snippet)

            entry = MemoryEntry(
                id=str(uuid.uuid4()),
                content=snippet,
                category=category,
                timestamp=time.time(),
                source_turn=0,
            )
            await store.save(entry)
            new_entries.append(entry)

        return new_entries + existing_entries

    def _classify(self, content: str) -> str | None:
        """Return the best-matching category, or None if no rule fires."""
        lowered = content.lower()
        for keywords, category in _EXTRACTION_RULES:
            for kw in keywords:
                if kw in lowered:
                    return category
        return None
