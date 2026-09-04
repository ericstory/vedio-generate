"""Put a fused QKV export back into the per-head row order SGLang's H3 loader expects.

The stock MiniMax checkpoint stores every `attn.qkv_proj.weight` grouped per
head: [q_0, k_0, v_0, q_1, k_1, v_1, ...], each block `head_dim` rows, and
SGLang's loader (`_reorder_grouped_qkv_to_qkv`) assumes exactly that. The
PinkCherry bf16 export stores the same tensor as [q_all, k_all, v_all]: its
rows 128-255 are the stock's rows 384-511 (q_1) bit for bit, while the stock
keeps k_0 there. Loaded as-is, every attention head reads someone else's K and
V, and every frame comes out as the same prompt-independent noise texture --
which is what the first ten "successful" H3 runs were.

A row permutation leaves each tensor's byte length untouched, so the file is
rewritten in place. `fc1` keeps the stock [gate; up] order and needs no change.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

HEADS = 56
HEAD_DIM = 128
MARKER_SUFFIX = ".grouped"


def _read_header(handle) -> tuple[dict, int]:
    length = struct.unpack("<Q", handle.read(8))[0]
    return json.loads(handle.read(length)), 8 + length


def standard_to_grouped(raw: bytes, *, heads: int, head_dim: int, row_bytes: int) -> bytes:
    """[q_all, k_all, v_all] -> [q_0, k_0, v_0, q_1, ...] on raw row-major bytes."""
    block = head_dim * row_bytes
    if len(raw) != 3 * heads * block:
        raise ValueError(f"unexpected qkv byte length {len(raw)} for {heads}x{head_dim}")
    chunks = []
    for head in range(heads):
        for part in range(3):
            start = (part * heads + head) * block
            chunks.append(raw[start : start + block])
    return b"".join(chunks)


def regroup_qkv_in_place(path: Path, *, heads: int = HEADS, head_dim: int = HEAD_DIM) -> int:
    """Rewrite every `*attn.qkv_proj.weight` in `path`; returns the tensor count.

    Idempotent through a marker file next to the checkpoint, because running the
    permutation twice would scramble the rows again.
    """
    path = Path(path)
    marker = path.with_name(path.name + MARKER_SUFFIX)
    if marker.exists():
        return 0
    done = 0
    with path.open("r+b") as handle:
        header, base = _read_header(handle)
        for key in sorted(k for k in header if k.endswith("attn.qkv_proj.weight")):
            meta = header[key]
            rows, cols = meta["shape"]
            if rows != 3 * heads * head_dim:
                raise ValueError(f"{key}: expected {3 * heads * head_dim} rows, got {rows}")
            item = {"BF16": 2, "F16": 2, "F32": 4}[meta["dtype"]]
            start, end = meta["data_offsets"]
            handle.seek(base + start)
            raw = handle.read(end - start)
            handle.seek(base + start)
            handle.write(standard_to_grouped(raw, heads=heads, head_dim=head_dim, row_bytes=cols * item))
            done += 1
        handle.flush()
        os.fsync(handle.fileno())
    marker.write_text(f"qkv rows regrouped per head: {done} tensors\n", encoding="utf-8")
    return done
