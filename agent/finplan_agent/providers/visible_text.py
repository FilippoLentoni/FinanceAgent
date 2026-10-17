"""Exclude model reasoning blocks before any text reaches the graph or stream."""
from __future__ import annotations


class VisibleText:
    """Incremental filter, including tags split across arbitrary stream chunks.

    Native Bedrock reasoning blocks are already excluded by the adapter. Some models
    instead return these literal tags in text blocks. Buffer possible tag prefixes so
    neither the tags nor their contents escape through an earlier token callback.
    """

    _OPEN = ("<think>", "<thinking>", "<analysis>")
    _CLOSE = ("</think>", "</thinking>", "</analysis>")
    _TAGS = _OPEN + _CLOSE

    def __init__(self) -> None:
        self._pending = ""
        self._depth = 0

    def feed(self, text: str) -> str:
        self._pending += text
        visible = []
        while self._pending:
            if not self._pending.startswith("<"):
                end = self._pending.find("<")
                end = end if end >= 0 else len(self._pending)
                if not self._depth:
                    visible.append(self._pending[:end])
                self._pending = self._pending[end:]
                continue
            lowered = self._pending.lower()
            tag = next((tag for tag in self._TAGS if lowered.startswith(tag)), None)
            if tag:
                self._depth = self._depth + 1 if tag in self._OPEN else max(0, self._depth - 1)
                self._pending = self._pending[len(tag):]
            elif any(tag.startswith(lowered) for tag in self._TAGS):
                break
            else:
                if not self._depth:
                    visible.append(self._pending[0])
                self._pending = self._pending[1:]
        return "".join(visible)

    def finish(self) -> str:
        # A remaining prefix could start a reasoning block. Suppress it, along with
        # any unfinished reasoning block, rather than leaking it at stream completion.
        self._pending = ""
        return ""


def visible_text(text: str) -> str:
    filtered = VisibleText()
    return filtered.feed(text) + filtered.finish()
