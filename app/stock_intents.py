from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


PUNCT_PATTERN = re.compile(r"[\s\t\r\n，。！？!?、；;：:“”\"'‘’（）()\[\]【】<>《》]+")
NUMBER_FRAGMENT = (
    r"(?:百分之(?:\d{1,3}|[零一二两三四五六七八九十百]+)"
    r"|\d{1,3}(?:%|％)?"
    r"|[零一二两三四五六七八九十百半]+(?:%|％|成|格|档|级)?)"
)
BAD_STOCK_TEXTS = (
    "网络连接失败",
    "连接失败",
    "服务异常",
    "暂时无法",
    "暂时不可用",
    "没听清",
    "没听懂",
    "听不清",
    "不知道",
    "无法处理",
    "请稍后再试",
    "操作失败",
    "执行失败",
)
SUCCESS_HINTS = (
    "好的",
    "好嘞",
    "收到",
    "已",
    "正在",
    "马上",
    "为你",
    "为您",
)
LEADING_FILLERS = (
    "请帮我",
    "麻烦你",
    "麻烦帮我",
    "能不能帮我",
    "可以帮我",
    "帮我",
    "麻烦",
    "给我",
    "请你",
    "请",
    "把",
    "将",
)
TRAILING_FILLERS = (
    "谢谢你",
    "谢谢",
    "好吗",
    "行吗",
    "可以吗",
    "吧",
    "呀",
    "啊",
    "呢",
)
STOCK_WEATHER_KEYWORDS = (
    "天气",
    "空气质量",
    "紫外线",
    "会下雨",
    "下雨吗",
    "有雨吗",
    "雨大吗",
    "风大吗",
    "几级风",
    "冷不冷",
    "热不热",
    "适合出门吗",
)
STOCK_LOCATION_KEYWORDS = (
    "我在哪",
    "我在哪里",
    "我现在在哪",
    "我现在在哪里",
    "这里是哪里",
    "这是哪里",
    "当前位置",
    "我的位置",
    "我在哪个城市",
    "所在城市",
)
STOCK_WEATHER_TIME_WORDS = ("今天", "明天", "后天", "现在", "当前", "今晚", "明早", "明天早上")
STOCK_TIME_PATTERNS = (
    re.compile(r"^(?:现在)?几点(?:了)?$"),
    re.compile(r"^现在时间(?:是)?多少$"),
    re.compile(r"^今天(?:几号|几月几号|星期几|周几)$"),
    re.compile(r"^今天日期$"),
)


@dataclass(frozen=True)
class MatchedStockIntent:
    route: str
    category: str
    allow_llm_fallback: bool
    normalized_text: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "category": self.category,
            "allow_llm_fallback": self.allow_llm_fallback,
            "normalized_text": self.normalized_text,
        }


@dataclass(frozen=True)
class _IntentSpec:
    route: str
    category: str
    allow_llm_fallback: bool = False


_CONTROL_PATTERNS: tuple[tuple[_IntentSpec, tuple[re.Pattern[str], ...]], ...] = (
    (
        _IntentSpec(route="stock_control_pause", category="media_control"),
        (
            re.compile(r"^(?:暂停(?:播放|音乐)?|先暂停(?:一下)?|停(?:一下|一会|一会儿)?|先停(?:一下)?|停止(?:播放|音乐)?|暂停下)$"),
            re.compile(r"^(?:别播了|不要播了|别放了|不要放了|停掉(?:播放|音乐)?)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_resume", category="media_control"),
        (
            re.compile(r"^(?:继续(?:播放|放歌)?|恢复(?:播放)?|接着(?:播|放|播放)|继续下去)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_next", category="media_control"),
        (
            re.compile(r"^(?:下一首(?:歌)?|下首|下一曲|切到下一首|切下一首|换下一首|来下一首|播放下一首)$"),
            re.compile(r"^(?:换首歌|切歌)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_previous", category="media_control"),
        (
            re.compile(r"^(?:上一首(?:歌)?|上首|上一曲|切到上一首|切上一首|回到上一首|返回上一首|播放上一首)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_volume_up", category="volume_control"),
        (
            re.compile(rf"^(?:把)?(?:音量|声音)(?:调|开|弄)?(?:大|高|响)(?:一点|一些|点|点儿|些)?(?:{NUMBER_FRAGMENT})?$"),
            re.compile(rf"^(?:把)?(?:音量|声音)(?:加(?:大)?|提高|增大|调高|调大|开大)(?:{NUMBER_FRAGMENT})?$"),
            re.compile(r"^(?:大声一点|声音大一点|再大声一点)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_volume_down", category="volume_control"),
        (
            re.compile(rf"^(?:把)?(?:音量|声音)(?:调|开|弄)?(?:小|低)(?:一点|一些|点|点儿|些)?(?:{NUMBER_FRAGMENT})?$"),
            re.compile(rf"^(?:把)?(?:音量|声音)(?:减(?:小)?|降低|调低|调小|关小|缩小)(?:{NUMBER_FRAGMENT})?$"),
            re.compile(r"^(?:小声一点|声音小一点|再小声一点|安静一点)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_volume_set", category="volume_control"),
        (
            re.compile(rf"^(?:把)?(?:音量|声音)(?:调到|设为|设置到|设置成|开到)(?:最大|最小|一半|半|{NUMBER_FRAGMENT})$"),
            re.compile(r"^(?:音量最大|音量最小|最大声(?:音)?|最小声(?:音)?)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_mute", category="volume_control"),
        (
            re.compile(r"^(?:静音|关闭声音|关掉声音|把声音关掉|把声音关了|别出声)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_unmute", category="volume_control"),
        (
            re.compile(r"^(?:取消静音|恢复声音|打开声音|声音恢复|取消勿扰)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_random_mode", category="playback_mode"),
        (
            re.compile(r"^(?:打开|开启|切换到|设置成|设成)?随机播放$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_sequence_mode", category="playback_mode"),
        (
            re.compile(r"^(?:打开|开启|切换到|设置成|设成)?顺序播放$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_single_loop", category="playback_mode"),
        (
            re.compile(r"^(?:打开|开启|切换到|设置成|设成)?单曲循环$"),
        ),
    ),
    (
        _IntentSpec(route="stock_control_list_loop", category="playback_mode"),
        (
            re.compile(r"^(?:打开|开启|切换到|设置成|设成)?(?:列表循环|循环播放|全部循环)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_system_sleep", category="device_control"),
        (
            re.compile(r"^(?:休眠|睡眠|进入睡眠|去睡觉|睡吧)$"),
            re.compile(r"^(?:打开|开启|进入)?(?:免打扰|勿扰模式)$"),
        ),
    ),
    (
        _IntentSpec(route="stock_system_shutdown", category="device_control"),
        (
            re.compile(r"^(?:关机|关闭音箱|关闭设备|关掉音箱|关闭这个音箱)$"),
            re.compile(r"^(?:重启|重启音箱|重启设备)$"),
        ),
    ),
)


def normalize_stock_text(text: str) -> str:
    cleaned = PUNCT_PATTERN.sub("", text).strip().lower()
    if not cleaned:
        return ""
    changed = True
    while changed and cleaned:
        changed = False
        for prefix in LEADING_FILLERS:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :]
                changed = True
        for suffix in TRAILING_FILLERS:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                changed = True
    return cleaned.strip()


def match_stock_intent(text: str) -> MatchedStockIntent | None:
    cleaned = normalize_stock_text(text)
    if not cleaned:
        return None

    for spec, patterns in _CONTROL_PATTERNS:
        if any(pattern.fullmatch(cleaned) for pattern in patterns):
            return MatchedStockIntent(
                route=spec.route,
                category=spec.category,
                allow_llm_fallback=spec.allow_llm_fallback,
                normalized_text=cleaned,
            )

    if any(keyword in cleaned for keyword in STOCK_LOCATION_KEYWORDS):
        return MatchedStockIntent(
            route="stock_location",
            category="stock_info",
            allow_llm_fallback=True,
            normalized_text=cleaned,
        )

    if any(keyword in cleaned for keyword in STOCK_WEATHER_KEYWORDS):
        return MatchedStockIntent(
            route="stock_weather",
            category="stock_info",
            allow_llm_fallback=True,
            normalized_text=cleaned,
        )

    if "几度" in cleaned and any(word in cleaned for word in STOCK_WEATHER_TIME_WORDS):
        return MatchedStockIntent(
            route="stock_weather",
            category="stock_info",
            allow_llm_fallback=True,
            normalized_text=cleaned,
        )

    if any(pattern.fullmatch(cleaned) for pattern in STOCK_TIME_PATTERNS):
        return MatchedStockIntent(
            route="stock_time",
            category="stock_info",
            allow_llm_fallback=True,
            normalized_text=cleaned,
        )

    return None


def stock_response_is_actionable(intent: MatchedStockIntent, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False

    code = str(payload.get("code") or "").strip()
    service = str(payload.get("service") or "").strip()
    match_type = str(payload.get("matchType") or "").strip()
    general = payload.get("general")
    general_text = ""
    if isinstance(general, dict):
        general_text = str(general.get("text") or "").strip()
    if not general_text:
        general_text = str(payload.get("text") or "").strip()

    if code == "SETTING_EXEC":
        return True

    if service and service != "cn.yunzhisheng.chat":
        return True

    if isinstance(payload.get("data"), dict) and payload.get("data"):
        return True

    if any(item in general_text for item in BAD_STOCK_TEXTS):
        return False

    if match_type and match_type != "NOT_UNDERSTAND":
        return True

    if general_text and any(item in general_text for item in SUCCESS_HINTS):
        return True

    if intent.category == "stock_info" and general_text:
        return True

    return False
