from __future__ import annotations

import re


FPS = 24


def quantization_for_compute_capability(
    requested: str, major: int, minor: int = 0
) -> str:
    """Resolve automatic quantization for mixed Hopper/Blackwell and Ampere fleets."""
    requested = requested.strip().lower()
    if requested != "auto":
        return requested
    # Ada (8.9), Hopper (9.x), and Blackwell support the FP8 path. Ampere does not.
    return "fp8-cast" if (major, minor) >= (8, 9) else ""

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


def dimensions(ratio: str, resolution: str) -> tuple[int, int]:
    # The native two-stage pipeline requires both dimensions to be divisible by 64.
    sizes = {
        "480p": {
            "16:9": (768, 448),
            "9:16": (448, 768),
            "1:1": (448, 448),
            "4:3": (640, 448),
            "3:4": (448, 640),
            "21:9": (1024, 448),
        },
        "720p": {
            "16:9": (1280, 704),
            "9:16": (704, 1280),
            "1:1": (704, 704),
            "4:3": (960, 704),
            "3:4": (704, 960),
            "21:9": (1664, 704),
        },
    }
    try:
        return sizes[resolution][ratio]
    except KeyError as exc:
        raise ValueError(f"unsupported resolution/ratio: {resolution}/{ratio}") from exc


def frame_count(duration: int) -> int:
    # LTX uses the temporal grid 8n + 1.
    return ((FPS * duration + 7) // 8) * 8 + 1
