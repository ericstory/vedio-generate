from __future__ import annotations

import re


# MiniMax H3 emits a fixed 24 fps canonical output; sglang rejects any request
# that tries to change it (no frame interpolation, no upscaling).
FPS = 24
MIN_DURATION_SECONDS = 4
MAX_DURATION_SECONDS = 15
# 768 is the only short edge MiniMax publishes recipes and reference outputs
# for. Other values resolve to a valid canvas but are outside anything either
# MiniMax or SGLang has measured, so they are flagged rather than silently used.
RECOMMENDED_SHORT_EDGE = 768
# H3 resolves the canvas from the short edge, then applies a 768x1344 soft area
# cap before rounding each axis. Mirrored here only to report the expected
# canvas back to the control plane; the pipeline stays the source of truth.
MAX_PIXELS = 768 * 1344
SUPPORTED_ASPECT_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
# The product API speaks in resolution labels; H3 speaks in short edges.
RESOLUTION_SHORT_EDGES = {"480p": 480, "720p": 720, "768p": RECOMMENDED_SHORT_EDGE}

_SEXUAL_TERMS = re.compile(
    r"\b(sex|sexual|nude|naked|porn|explicit|erotic)\b|色情|裸体|性爱|性交|裸露",
    re.IGNORECASE,
)
_MINOR_TERMS = re.compile(
    r"\b(child|children|kid|minor|teen|underage|schoolgirl|schoolboy)\b|"
    r"未成年|儿童|小孩|幼女|幼男|学生妹",
    re.IGNORECASE,
)
_ABUSE_TERMS = re.compile(
    r"\b(rape|non.?consensual|forced sex|incest|bestiality|drugged|unconscious)\b|"
    r"强奸|迷奸|非自愿|乱伦|兽交|昏迷",
    re.IGNORECASE,
)
_REAL_PERSON_TERMS = re.compile(
    r"\b(deepfake|celebrity|public figure|real person|politician)\b|"
    r"深度伪造|真人换脸|名人|公众人物|政治人物",
    re.IGNORECASE,
)


def validate_prompt(prompt: str) -> None:
    if not prompt or len(prompt) > 3000:
        raise ValueError("prompt must contain 1-3000 characters")
    if _ABUSE_TERMS.search(prompt):
        raise ValueError("prompt violates the self-hosted content policy")
    if _SEXUAL_TERMS.search(prompt) and (
        _MINOR_TERMS.search(prompt) or _REAL_PERSON_TERMS.search(prompt)
    ):
        raise ValueError("prompt violates the self-hosted content policy")


def short_edge_for(resolution: str) -> int:
    try:
        return RESOLUTION_SHORT_EDGES[resolution]
    except KeyError as exc:
        supported = ", ".join(sorted(RESOLUTION_SHORT_EDGES))
        raise ValueError(
            f"unsupported MiniMax H3 resolution: {resolution} (supported: {supported})"
        ) from exc


def validate_aspect_ratio(ratio: str) -> str:
    if ratio not in SUPPORTED_ASPECT_RATIOS:
        supported = ", ".join(SUPPORTED_ASPECT_RATIOS)
        raise ValueError(
            f"unsupported MiniMax H3 aspect ratio: {ratio} (supported: {supported})"
        )
    return ratio


def validate_duration(duration: int) -> int:
    if not MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS:
        raise ValueError(
            f"MiniMax H3 supports {MIN_DURATION_SECONDS}-{MAX_DURATION_SECONDS} "
            f"second clips, got {duration}"
        )
    return duration


def build_target(*, ratio: str, resolution: str, duration: int) -> dict[str, object]:
    """Build the H3 `target` block the SGLang pipeline resolves the canvas from."""
    return {
        "short_edge": short_edge_for(resolution),
        "aspect_ratio": validate_aspect_ratio(ratio),
        "duration_seconds": float(validate_duration(duration)),
    }


def expected_canvas(ratio: str, resolution: str) -> tuple[int, int]:
    """Mirror the H3 canvas rule closely enough to report an expected size.

    H3 scales the requested short edge by the aspect ratio, clamps to the
    768x1344 pixel budget and rounds each axis to the canvas multiple. This is
    reporting only: the pipeline resolves the authoritative geometry and its
    answer is what lands in the job metadata.
    """
    short_edge = short_edge_for(resolution)
    num, den = (int(part) for part in validate_aspect_ratio(ratio).split(":"))
    ratio_value = max(num, den) / min(num, den)
    # The short edge is what the caller asked for, so the area cap comes off the
    # long edge. 16:9 at a 768 short edge lands on 1344x768, which is the canvas
    # MiniMax and SGLang publish every H3 benchmark against.
    long_edge = min(round(short_edge * ratio_value), MAX_PIXELS // short_edge)
    short_edge -= short_edge % 16
    long_edge -= long_edge % 16
    if num >= den:
        return (long_edge, short_edge)
    return (short_edge, long_edge)


def frames_for_duration(duration: int) -> int:
    """Canonical 24 fps frame count H3 aligns a whole-second request to."""
    return validate_duration(duration) * FPS


def is_verified_configuration(resolution: str) -> bool:
    """Whether this request stays on the only measured MiniMax/SGLang recipe."""
    return short_edge_for(resolution) == RECOMMENDED_SHORT_EDGE


def estimate_denoise_seconds(
    *,
    ratio: str,
    resolution: str,
    duration: int,
    steps: int,
    seconds_per_megapixel_step: float,
) -> float | None:
    """Project denoise time from a measured constant, or None when uncalibrated.

    `seconds_per_megapixel_step` has no default on purpose. Nothing in this
    repository has timed H3 on the production GPU yet, so the pod-timeout guard
    stays off until a real run supplies the constant.
    """
    if seconds_per_megapixel_step <= 0:
        return None
    width, height = expected_canvas(ratio, resolution)
    megapixels = (width * height) / 1_000_000
    return megapixels * frames_for_duration(duration) * steps * seconds_per_megapixel_step


def validate_runtime_budget(
    *,
    ratio: str,
    resolution: str,
    duration: int,
    steps: int,
    seconds_per_megapixel_step: float,
    budget_seconds: float,
) -> float | None:
    """Reject jobs projected to blow the one-shot Pod timeout before billing.

    Wan lost paid runs to exactly this: a 720p/12s job could not finish inside
    the 30 minute cap and only failed after the GPU had been billed for it.
    """
    projected = estimate_denoise_seconds(
        ratio=ratio,
        resolution=resolution,
        duration=duration,
        steps=steps,
        seconds_per_megapixel_step=seconds_per_megapixel_step,
    )
    if projected is None or budget_seconds <= 0:
        return projected
    if projected > budget_seconds:
        raise ValueError(
            f"MiniMax H3 job projected at {projected:.0f}s of denoise exceeds the "
            f"{budget_seconds:.0f}s budget ({resolution}/{duration}s at {steps} "
            "steps). Reduce duration, resolution or steps."
        )
    return projected


def ensure_trigger(prompt: str, trigger: str) -> str:
    """Prefix the checkpoint's trigger phrase when one is configured.

    PinkCherry for H3 publishes no trigger word, so the default is empty and
    this is a no-op unless a future checkpoint needs one.
    """
    trigger = trigger.strip()
    if not trigger or trigger.lower() in prompt.lower():
        return prompt
    return f"{trigger}, {prompt}"
