from __future__ import annotations

import re


FPS = 16
ALLOWED_DURATIONS = (4, 5, 6, 8, 10, 12, 15)

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
    sizes = {
        "480p": {
            "16:9": (832, 480),
            "9:16": (480, 832),
            "1:1": (480, 480),
            "4:3": (640, 480),
            "3:4": (480, 640),
            "21:9": (1120, 480),
        },
        "720p": {
            "16:9": (1280, 720),
            "9:16": (720, 1280),
            "1:1": (720, 720),
            "4:3": (960, 720),
            "3:4": (720, 960),
            "21:9": (1680, 720),
        },
    }
    try:
        return sizes[resolution][ratio]
    except KeyError as exc:
        raise ValueError(f"unsupported Wan resolution/ratio: {resolution}/{ratio}") from exc


def frames_for_duration(duration: int) -> int:
    if duration not in ALLOWED_DURATIONS:
        raise ValueError(f"unsupported Wan duration: {duration}")
    # Wan's temporal VAE expects 4N+1 frames. Every supported whole-second
    # duration at 16 fps naturally lands on that grid.
    return duration * FPS + 1


def ensure_trigger(prompt: str, trigger: str = "nsfwsks") -> str:
    return prompt if trigger.lower() in prompt.lower() else f"{trigger}, {prompt}"
