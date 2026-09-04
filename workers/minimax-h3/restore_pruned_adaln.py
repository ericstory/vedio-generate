"""Restore a curve-form (pruned AdaLN) MiniMax H3 transformer export to the layout SGLang loads.

ComfyUI's H3 loader accepts a "curve-form" checkpoint that drops the time
embedder and shrinks every `*.adaln_proj.linear.weight` from [out, 2688] to
[out, k] (k = 8), shipping instead a shared basis of the time-embedding curve:

    adaln_basis [k, 2688]   adaln_mean [2688]   adaln_t_table [grid, k]

At inference it lerps `adaln_t_table` at t in [0, 1] and feeds the k
coordinates straight into the projections, with no SiLU (comfy/ldm/minimax/
model.py, `use_adaln_curves`). SGLang v0.5.18 implements only the stock form,
where each projection consumes the full 2688-dim SiLU(time_embedder(t)) and
shape-checks it, so 10Eros Max and every other curve-form export is unloadable
there as published.

The two forms are the same affine map along the curve. With
c(t) = SiLU(time_embedder(t)) and table[t] = basis @ (c(t) - mean):

    pruned_W @ table[t] + pruned_b
      = (pruned_W @ basis) @ c(t) + (pruned_b - pruned_W @ basis @ mean)

so `full_W = pruned_W @ basis` and `full_b = pruned_b - full_W @ mean` reproduce
the curve-form model at every grid point, up to the bf16 rounding of the stored
basis. Measured on 10Eros Max beta4 against the official time embedder: the
restored projections differ from the table lookup by 0.13-0.25% RMS, below the
0.39% bf16 step of the weights themselves. The basis rows are orthonormal
(max |B B^T - I| = 0.003) and MiniMax's fine-tunes leave the time embedder
untouched (official and PinkCherry are bit-identical), which is why the four
`time_embedder.*` tensors can come from any stock shard.

The output also puts fused QKV back into the per-head row order SGLang expects
(see regroup_qkv.py), so the worker downloads it and loads it as is.

Pure numpy, streaming one tensor at a time: a 40 GB input becomes a 66 GB
output on a CPU Pod with a few GB of RAM.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from regroup_qkv import HEADS, HEAD_DIM, standard_to_grouped

BASIS_KEY = "adaln_basis"
MEAN_KEY = "adaln_mean"
TABLE_KEY = "adaln_t_table"
CURVE_KEYS = (BASIS_KEY, MEAN_KEY, TABLE_KEY)
ADALN_WEIGHT_SUFFIX = ".adaln_proj.linear.weight"
ADALN_BIAS_SUFFIX = ".adaln_proj.linear.bias"
QKV_SUFFIX = "attn.qkv_proj.weight"
TIME_EMBEDDER_KEYS = (
    "time_embedder.proj_in.weight",
    "time_embedder.proj_in.bias",
    "time_embedder.proj_out.weight",
    "time_embedder.proj_out.bias",
)
ITEM_BYTES = {"BF16": 2, "F16": 2, "F32": 4}
# Also the HTTP request size when the source is a URL: small enough that a
# retry is cheap, large enough that the request count stays in the hundreds.
COPY_CHUNK = 64 * 1024 * 1024


class RangeReader:
    """A read-only file over HTTP Range requests: seek/read/tell and nothing more.

    RunPod's CPU Pods cap the container disk at 80 GB and silently drop the Pod
    volume, which is not enough for a 40 GB input next to a 66 GB output. The
    restorer only ever reads one tensor at a time, so the input can stay on the
    Hub and be fetched by byte range as it goes (the Hub's CDN honours Range
    across its redirect). Every request is retried; a short response is an error.
    """

    def __init__(self, url: str, *, retries: int = 6, timeout: float = 180.0) -> None:
        self.url = url
        self.retries = retries
        self.timeout = timeout
        self.position = 0
        self.name = url.rsplit("/", 1)[-1]

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence != os.SEEK_SET:
            raise ValueError("RangeReader only supports absolute seeks")
        self.position = offset
        return offset

    def tell(self) -> int:
        return self.position

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("RangeReader needs an explicit size")
        if size == 0:
            return b""
        start, end = self.position, self.position + size - 1
        last: Exception | None = None
        for attempt in range(self.retries):
            request = urllib.request.Request(
                self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "papa-restore/1.0"}
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    if response.status != 206:
                        raise IOError(f"server ignored the Range header (HTTP {response.status})")
                    data = response.read()
                if len(data) != size:
                    raise IOError(f"short range response: {len(data)} of {size} bytes")
                self.position += size
                return data
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
                last = exc
                time.sleep(min(2**attempt, 30))
        raise IOError(f"range read {start}-{end} of {self.url} failed after {self.retries} attempts: {last}")

    def close(self) -> None:
        return None


def open_source(source: str | Path):
    """A local path opens as a file; an http(s) URL as a RangeReader."""
    text = str(source)
    if text.startswith(("http://", "https://")):
        return RangeReader(text)
    return open(source, "rb")


def read_header(handle) -> tuple[dict[str, Any], int]:
    length = struct.unpack("<Q", handle.read(8))[0]
    return json.loads(handle.read(length)), 8 + length


def bf16_to_f32(raw: bytes) -> np.ndarray:
    bits = np.frombuffer(raw, dtype=np.uint16).astype(np.uint32) << 16
    return bits.view(np.float32)


def f32_to_bf16(values: np.ndarray) -> bytes:
    """Round-to-nearest-even, the same rounding torch applies."""
    bits = np.ascontiguousarray(values, dtype=np.float32).view(np.uint32)
    rounded = (bits + 0x7FFF + ((bits >> 16) & 1)) >> 16
    return rounded.astype(np.uint16).tobytes()


def decode(raw: bytes, dtype: str, shape: list[int]) -> np.ndarray:
    if dtype == "BF16":
        values = bf16_to_f32(raw)
    elif dtype == "F32":
        values = np.frombuffer(raw, dtype=np.float32)
    elif dtype == "F16":
        values = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
    else:
        raise ValueError(f"unsupported dtype {dtype}")
    return values.reshape(shape)


def encode(values: np.ndarray, dtype: str) -> bytes:
    if dtype == "BF16":
        return f32_to_bf16(values)
    if dtype == "F32":
        return np.ascontiguousarray(values, dtype=np.float32).tobytes()
    if dtype == "F16":
        return np.ascontiguousarray(values, dtype=np.float16).tobytes()
    raise ValueError(f"unsupported dtype {dtype}")


def load_tensor(handle, base: int, meta: dict[str, Any]) -> np.ndarray:
    start, end = meta["data_offsets"]
    handle.seek(base + start)
    return decode(handle.read(end - start), meta["dtype"], meta["shape"])


def silu(x: np.ndarray) -> np.ndarray:
    return x / (1.0 + np.exp(-x))


def time_embedding_curve(t: np.ndarray, te: dict[str, np.ndarray]) -> np.ndarray:
    """SiLU(time_embedder(t)) for t in [0, 1], mirroring ComfyUI's TimeEmbedder (cos before sin)."""
    freq_dim = te["time_embedder.proj_in.weight"].shape[1]
    half = freq_dim // 2
    freqs = np.exp(-math.log(10000.0) * np.arange(half, dtype=np.float64) / half)
    args = t.astype(np.float64)[:, None] * freqs[None]
    emb = np.concatenate([np.cos(args), np.sin(args)], axis=-1)
    hidden = silu(emb @ te["time_embedder.proj_in.weight"].T.astype(np.float64) + te["time_embedder.proj_in.bias"])
    return silu(hidden @ te["time_embedder.proj_out.weight"].T.astype(np.float64) + te["time_embedder.proj_out.bias"])


def _tensor_keys(header: dict[str, Any]) -> list[str]:
    return sorted(key for key in header if key != "__metadata__")


def restore(
    pruned_path: str | Path,
    donor_path: str | Path,
    output_path: Path,
    *,
    regroup: bool = True,
    heads: int = HEADS,
    head_dim: int = HEAD_DIM,
    extra_metadata: dict[str, str] | None = None,
    log: Callable[[str], None] = lambda line: print(line, flush=True),
) -> dict[str, Any]:
    """Write the restored checkpoint and return a summary of what was done."""
    pruned = open_source(pruned_path)
    donor = open_source(donor_path)
    try:
        header, base = read_header(pruned)
        donor_header, donor_base = read_header(donor)
        missing = [key for key in CURVE_KEYS if key not in header]
        if missing:
            raise ValueError(f"{pruned_path} is not a curve-form export, missing {missing}")
        missing = [key for key in TIME_EMBEDDER_KEYS if key not in donor_header]
        if missing:
            raise ValueError(f"donor {donor_path} lacks {missing}")
        for key in TIME_EMBEDDER_KEYS:
            if key in header:
                raise ValueError(f"{pruned_path} already carries {key}; not a curve-form export")

        basis = load_tensor(pruned, base, header[BASIS_KEY]).astype(np.float64)  # [k, D]
        mean = load_tensor(pruned, base, header[MEAN_KEY]).astype(np.float64)    # [D]
        k, full_dim = basis.shape
        orthogonality = float(np.abs(basis @ basis.T - np.eye(k)).max())
        if orthogonality > 0.05:
            raise ValueError(f"adaln_basis rows are not orthonormal (max |BB^T - I| = {orthogonality:.3g})")
        basis_mean = basis @ mean  # [k]

        # Plan the output: every tensor's dtype and shape up front so the header
        # can be written before the data streams through.
        entries: list[tuple[str, str, list[int]]] = []
        producers: dict[str, Callable[[], Iterator[bytes]]] = {}
        restored_weights: list[str] = []
        restored_biases: list[str] = []
        regrouped: list[str] = []
        copied = 0

        def copy_from(handle, hbase: int, meta: dict[str, Any]) -> Callable[[], Iterator[bytes]]:
            def produce() -> Iterator[bytes]:
                start, end = meta["data_offsets"]
                handle.seek(hbase + start)
                remaining = end - start
                while remaining:
                    chunk = handle.read(min(COPY_CHUNK, remaining))
                    if not chunk:
                        raise IOError("short read")
                    remaining -= len(chunk)
                    yield chunk
            return produce

        def expand_weight(meta: dict[str, Any]) -> Callable[[], Iterator[bytes]]:
            def produce() -> Iterator[bytes]:
                pruned_w = load_tensor(pruned, base, meta).astype(np.float32)          # [out, k]
                full_w = pruned_w @ basis.astype(np.float32)                            # [out, D]
                yield encode(full_w, meta["dtype"])
            return produce

        def expand_bias(meta: dict[str, Any], weight_meta: dict[str, Any]) -> Callable[[], Iterator[bytes]]:
            def produce() -> Iterator[bytes]:
                pruned_b = load_tensor(pruned, base, meta).astype(np.float64)           # [out]
                pruned_w = load_tensor(pruned, base, weight_meta).astype(np.float64)    # [out, k]
                full_b = pruned_b - pruned_w @ basis_mean
                yield encode(full_b, meta["dtype"])
            return produce

        def regroup_qkv(meta: dict[str, Any]) -> Callable[[], Iterator[bytes]]:
            def produce() -> Iterator[bytes]:
                start, end = meta["data_offsets"]
                pruned.seek(base + start)
                raw = pruned.read(end - start)
                rows, cols = meta["shape"]
                yield standard_to_grouped(raw, heads=heads, head_dim=head_dim, row_bytes=cols * ITEM_BYTES[meta["dtype"]])
            return produce

        for key in _tensor_keys(header):
            meta = header[key]
            if key in CURVE_KEYS:
                continue
            if key.endswith(ADALN_WEIGHT_SUFFIX):
                out, width = meta["shape"]
                if width != k:
                    raise ValueError(f"{key}: expected {k} columns, got {width}")
                entries.append((key, meta["dtype"], [out, full_dim]))
                producers[key] = expand_weight(meta)
                restored_weights.append(key)
            elif key.endswith(ADALN_BIAS_SUFFIX):
                weight_key = key[: -len(ADALN_BIAS_SUFFIX)] + ADALN_WEIGHT_SUFFIX
                if weight_key not in header:
                    raise ValueError(f"{key} has no matching weight")
                entries.append((key, meta["dtype"], list(meta["shape"])))
                producers[key] = expand_bias(meta, header[weight_key])
                restored_biases.append(key)
            elif regroup and key.endswith(QKV_SUFFIX):
                rows, _ = meta["shape"]
                if rows != 3 * heads * head_dim:
                    raise ValueError(f"{key}: expected {3 * heads * head_dim} rows, got {rows}")
                entries.append((key, meta["dtype"], list(meta["shape"])))
                producers[key] = regroup_qkv(meta)
                regrouped.append(key)
            else:
                entries.append((key, meta["dtype"], list(meta["shape"])))
                producers[key] = copy_from(pruned, base, meta)
                copied += 1
        for key in TIME_EMBEDDER_KEYS:
            meta = donor_header[key]
            entries.append((key, meta["dtype"], list(meta["shape"])))
            producers[key] = copy_from(donor, donor_base, meta)
        if len(restored_weights) != len(restored_biases):
            raise ValueError("every adaln weight needs its bias and vice versa")
        entries.sort(key=lambda entry: entry[0])

        metadata = {str(k_): str(v) for k_, v in (header.get("__metadata__") or {}).items()}
        metadata.update(
            {
                "adaln_restored_from": Path(str(pruned_path)).name,
                "adaln_restoration": "full_W = pruned_W @ adaln_basis; full_bias = pruned_bias - full_W @ adaln_mean",
                "adaln_basis_orthogonality": f"{orthogonality:.4g}",
                "time_embedder_donor": Path(str(donor_path)).name,
                "qkv_row_layout": "per_head" if regroup else "standard",
            }
        )
        metadata.update(extra_metadata or {})
        out_header: dict[str, Any] = {"__metadata__": metadata}
        offset = 0
        for key, dtype, shape in entries:
            size = ITEM_BYTES[dtype] * int(np.prod(shape))
            out_header[key] = {"dtype": dtype, "shape": shape, "data_offsets": [offset, offset + size]}
            offset += size
        encoded = json.dumps(out_header, separators=(",", ":")).encode("utf-8")
        encoded += b" " * (-len(encoded) % 8)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        written = 0
        with open(output_path, "wb") as out:
            out.write(struct.pack("<Q", len(encoded)))
            out.write(encoded)
            for index, (key, dtype, shape) in enumerate(entries, 1):
                expected = out_header[key]["data_offsets"][1] - out_header[key]["data_offsets"][0]
                produced = 0
                for chunk in producers[key]():
                    out.write(chunk)
                    produced += len(chunk)
                if produced != expected:
                    raise ValueError(f"{key}: produced {produced} bytes, header says {expected}")
                written += produced
                if index % 50 == 0 or index == len(entries):
                    log(f"[{index}/{len(entries)}] {written / 2**30:.1f} GiB written, {time.monotonic() - started:.0f}s")
            out.flush()
            os.fsync(out.fileno())
        return {
            "tensors": len(entries),
            "bytes": 8 + len(encoded) + offset,
            "restored_weights": len(restored_weights),
            "restored_biases": len(restored_biases),
            "regrouped_qkv": len(regrouped),
            "copied": copied,
            "basis_rank": k,
            "full_dim": full_dim,
            "basis_orthogonality": orthogonality,
        }
    finally:
        pruned.close()
        donor.close()


def verify(
    pruned_path: str | Path,
    donor_path: str | Path,
    output_path: Path,
    *,
    sample_keys: tuple[str, ...] = ("blocks.0", "blocks.49", "final_layer"),
    max_relative_rms: float = 0.01,
    log: Callable[[str], None] = lambda line: print(line, flush=True),
) -> dict[str, float]:
    """Compare the restored projections with the table lookup on the curve grid.

    ComfyUI computes `pruned_W @ table[t] + pruned_b`; the restored file makes
    SGLang compute `full_W @ SiLU(te(t)) + full_b`. On the grid the two must
    agree to within bf16 rounding, and anything past 1% RMS means the table was
    not built from this time embedder (wrong t convention, or an embedder the
    export retrained) and the file must not ship.
    """
    pruned, donor = open_source(pruned_path), open_source(donor_path)
    try:
        with open(output_path, "rb") as restored:
            header, base = read_header(pruned)
            donor_header, donor_base = read_header(donor)
            out_header, out_base = read_header(restored)
            basis = load_tensor(pruned, base, header[BASIS_KEY]).astype(np.float64)
            table = load_tensor(pruned, base, header[TABLE_KEY]).astype(np.float64)
            te = {key: load_tensor(donor, donor_base, donor_header[key]).astype(np.float64) for key in TIME_EMBEDDER_KEYS}
            grid = table.shape[0]
            t = np.arange(grid, dtype=np.float64) / (grid - 1)
            curve = time_embedding_curve(t, te)  # [grid, D]
            results: dict[str, float] = {}
            for prefix in sample_keys:
                weight_key = prefix + ADALN_WEIGHT_SUFFIX
                bias_key = prefix + ADALN_BIAS_SUFFIX
                if weight_key not in header:
                    continue
                pruned_w = load_tensor(pruned, base, header[weight_key]).astype(np.float64)
                pruned_b = load_tensor(pruned, base, header[bias_key]).astype(np.float64)
                full_w = load_tensor(restored, out_base, out_header[weight_key]).astype(np.float64)
                full_b = load_tensor(restored, out_base, out_header[bias_key]).astype(np.float64)
                reference = table @ pruned_w.T + pruned_b
                ours = curve @ full_w.T + full_b
                rms = float(np.sqrt(((ours - reference) ** 2).mean()) / np.sqrt((reference**2).mean()))
                results[prefix] = rms
                log(f"verify {prefix}: relative RMS vs table lookup = {rms:.4g}")
                if rms > max_relative_rms:
                    raise ValueError(f"{prefix}: restored projection deviates {rms:.3%} RMS from the curve table")
            return results
    finally:
        pruned.close()
        donor.close()


def check_against_index(output_path: Path, index_path: Path) -> None:
    """The restored key set must equal the stock transformer's, name for name."""
    expected = set(json.load(open(index_path, "rb"))["weight_map"])
    with open(output_path, "rb") as restored:
        header, _ = read_header(restored)
    actual = set(_tensor_keys(header))
    if actual != expected:
        raise ValueError(
            f"key set differs from the stock transformer: missing {sorted(expected - actual)[:10]}, "
            f"extra {sorted(actual - expected)[:10]}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pruned", required=True, help="curve-form export (10Eros Max beta4): local path or https URL")
    parser.add_argument("--donor", required=True, help="stock shard carrying time_embedder.*: local path or https URL")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--index", type=Path, help="stock model.safetensors.index.json to compare the key set against")
    parser.add_argument("--no-regroup", action="store_true", help="keep [q_all, k_all, v_all] QKV rows")
    parser.add_argument("--metadata", action="append", default=[], help="extra key=value safetensors metadata")
    args = parser.parse_args(argv)
    extra = dict(item.split("=", 1) for item in args.metadata)
    summary = restore(args.pruned, args.donor, args.output, regroup=not args.no_regroup, extra_metadata=extra)
    print(json.dumps({"stage": "restored", **summary}), flush=True)
    if args.index:
        check_against_index(args.output, args.index)
        print(json.dumps({"stage": "index_checked"}), flush=True)
    results = verify(args.pruned, args.donor, args.output)
    print(json.dumps({"stage": "verified", "relative_rms": results}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
