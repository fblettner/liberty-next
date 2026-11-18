"""Files attached to a question — what the user gives the assistant to READ (a screenshot of an
error, a PDF procedure, a spreadsheet to compare with what Liberty holds). Nothing is loaded
anywhere: the file becomes one content block of the user's message, and that is all.

* an image (png / jpeg / gif / webp) and a PDF go to the model as they are (base64);
* a spreadsheet (xlsx / xlsm) or a delimited file (csv / tsv) is read with the nomaflow
  ``file_load`` reader — first sheet, detected delimiter — and sent as delimited text;
* a text file (txt / log / md / json / xml / sql / toml / yaml…) is sent as text.

Text is capped (:data:`TEXT_MAX_CHARS`, with a note saying so): a file is paid in input tokens
at every question of the conversation it is attached to. ``[ai] attachment_max_mb`` bounds the
upload (0 = no attachment at all); an image has the API's own 5 MB limit.
"""

from __future__ import annotations

import base64
import csv
import io
import tempfile
from pathlib import Path
from typing import Any

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
PDF_TYPE = "application/pdf"
SHEET_SUFFIXES = {".xlsx", ".xlsm"}
DELIMITED_SUFFIXES = {".csv", ".tsv"}
TEXT_SUFFIXES = {".txt", ".log", ".md", ".json", ".xml", ".sql", ".toml", ".yaml", ".yml", ".ini", ".properties", ".html", ".py", ".java"}

IMAGE_MAX_BYTES = 5 * 1024 * 1024       # the Messages API's limit for one image
TEXT_MAX_CHARS = 60_000                 # what one text / spreadsheet attachment carries at most
BLOCK_TYPES = ("text", "image", "document")   # what a user message may carry (the chat route checks)


class AttachmentError(ValueError):
    """The file cannot be attached — the message is for the user."""


def accepted() -> list[str]:
    """The file extensions that can be attached (for the picker)."""
    return sorted({*IMAGE_TYPES, ".pdf", *SHEET_SUFFIXES, *DELIMITED_SUFFIXES, *TEXT_SUFFIXES})


def _cut(text: str, what: str) -> tuple[str, str | None]:
    if len(text) <= TEXT_MAX_CHARS:
        return text, None
    return text[:TEXT_MAX_CHARS], f"{what} is long: only its first {TEXT_MAX_CHARS} characters are attached."


def _table_text(name: str, data: bytes, suffix: str) -> tuple[str, str | None]:
    """A spreadsheet or delimited file as comma-separated text, through the file_load reader."""
    from liberty.jobs.steps.file_load import open_reader

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    rows = 0
    note: str | None = None
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"attachment{suffix}"
        path.write_bytes(data)
        try:
            for _n, cells in open_reader(path, sheet=None, delimiter=None, encoding="utf-8-sig"):
                if all(c is None or str(c).strip() == "" for c in cells):
                    continue
                writer.writerow(["" if c is None else c for c in cells])
                rows += 1
                if out.tell() > TEXT_MAX_CHARS:
                    note = f"{name} is long: only its first {rows} rows are attached."
                    break
        except Exception as exc:  # noqa: BLE001 — a damaged or protected file: say so, attach nothing
            raise AttachmentError(f"{name} could not be read ({type(exc).__name__}: {exc})") from exc
    if rows == 0:
        raise AttachmentError(f"{name} is empty")
    return out.getvalue()[: TEXT_MAX_CHARS + 2000], note


def to_block(name: str, data: bytes, *, max_bytes: int) -> dict[str, Any]:
    """``{name, kind, size, block[, note]}`` for an uploaded file: *block* is the content block
    the browser adds to the user's message; *note* says what was cut. Raises
    :class:`AttachmentError` for a file that is too big, empty, of an unknown kind or unreadable."""
    name = Path(name or "file").name
    suffix = Path(name).suffix.lower()
    if not data:
        raise AttachmentError(f"{name} is empty")
    if len(data) > max_bytes:
        raise AttachmentError(f"{name} is {len(data) / 1_048_576:.1f} MB — the limit is {max_bytes / 1_048_576:.0f} MB")
    out: dict[str, Any] = {"name": name, "size": len(data)}
    if suffix in IMAGE_TYPES:
        if len(data) > IMAGE_MAX_BYTES:
            raise AttachmentError(f"{name} is {len(data) / 1_048_576:.1f} MB — an image is 5 MB at most")
        return {**out, "kind": "image", "block": {
            "type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES[suffix], "data": base64.standard_b64encode(data).decode("ascii")}}}
    if suffix == ".pdf":
        if not data.startswith(b"%PDF"):
            raise AttachmentError(f"{name} is not a PDF file")
        return {**out, "kind": "pdf", "block": {
            "type": "document", "title": name, "source": {"type": "base64", "media_type": PDF_TYPE, "data": base64.standard_b64encode(data).decode("ascii")}}}
    if suffix in SHEET_SUFFIXES or suffix in DELIMITED_SUFFIXES:
        text, note = _table_text(name, data, suffix)
        kind = "table"
    elif suffix in TEXT_SUFFIXES:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        if not text.strip():
            raise AttachmentError(f"{name} is empty")
        text, note = _cut(text, name)
        kind = "text"
    else:
        raise AttachmentError(f"{name}: this kind of file cannot be attached — use {', '.join(accepted())}")
    block = {"type": "text", "text": f'<attached_file name="{name}">\n{text}\n</attached_file>' + (f"\n[{note}]" if note else "")}
    return {**out, "kind": kind, "block": block, **({"note": note} if note else {})}


def check_content(content: Any, *, max_bytes: int) -> None:
    """A user message's parts, as the chat route receives them: text, image and document blocks
    only, attachments within the limit (and none at all when attachments are off)."""
    if isinstance(content, str):
        return
    size = 0
    for part in content or []:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind not in BLOCK_TYPES:
            raise AttachmentError(f"a message part of type {kind!r} is not accepted")
        if kind in ("image", "document"):
            if max_bytes <= 0:
                raise AttachmentError("attachments are disabled on this server")
            src = part.get("source") or {}
            if src.get("type") != "base64" or not isinstance(src.get("data"), str):
                raise AttachmentError("an attachment must be sent as base64 data")
            size += len(src["data"]) * 3 // 4
    if size > max_bytes > 0:
        raise AttachmentError(f"the attachments of this message are {size / 1_048_576:.1f} MB — the limit is {max_bytes / 1_048_576:.0f} MB")
