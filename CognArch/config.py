# ================= ⚙️ 配置文件 =================
# 使用方法：runtime override > web_config.json > 环境变量 > config.py 默认值

import json
import os

from paths import get_config_path as _get_config_path

# ========== 配置文件路径 ==========
def get_config_path():
    """获取配置文件路径"""
    return _get_config_path()

# ========== 默认配置 ==========
DEFAULT_API_KEY = ""
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_LANGUAGE = "Simplified Chinese"

_UNSET = object()
_RUNTIME_CONFIG = {
    "api_key": _UNSET,
    "base_url": _UNSET,
    "language": _UNSET,
}


def set_runtime_config(api_key=_UNSET, base_url=_UNSET, language=_UNSET):
    """Set per-process runtime overrides without persisting to disk."""
    if api_key is not _UNSET:
        _RUNTIME_CONFIG["api_key"] = api_key
    if base_url is not _UNSET:
        _RUNTIME_CONFIG["base_url"] = base_url
    if language is not _UNSET:
        _RUNTIME_CONFIG["language"] = language


def clear_runtime_config():
    """Clear all runtime overrides."""
    _RUNTIME_CONFIG["api_key"] = _UNSET
    _RUNTIME_CONFIG["base_url"] = _UNSET
    _RUNTIME_CONFIG["language"] = _UNSET


def clear_runtime_api_key():
    """Clear only the runtime API key override."""
    _RUNTIME_CONFIG["api_key"] = _UNSET

# ========== 动态配置读取函数 ==========
def load_dynamic_config():
    """从配置文件加载动态配置"""
    config_path = get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return None

def get_api_key():
    """动态获取 API Key"""
    runtime_api_key = _RUNTIME_CONFIG.get("api_key", _UNSET)
    if runtime_api_key is not _UNSET:
        return runtime_api_key
    config = load_dynamic_config()
    if config and config.get('api_key'):
        return config['api_key']
    # 回退到环境变量或默认值
    return os.environ.get("DEEPSEEK_API_KEY", DEFAULT_API_KEY)

def get_base_url():
    """动态获取 Base URL"""
    runtime_base_url = _RUNTIME_CONFIG.get("base_url", _UNSET)
    if runtime_base_url is not _UNSET:
        return runtime_base_url or DEFAULT_BASE_URL
    config = load_dynamic_config()
    if config and config.get('base_url'):
        return config['base_url']
    # 回退到环境变量或默认值
    return os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)

def get_target_language():
    """动态获取目标语言"""
    runtime_language = _RUNTIME_CONFIG.get("language", _UNSET)
    if runtime_language is not _UNSET:
        return runtime_language or DEFAULT_LANGUAGE
    config = load_dynamic_config()
    if config and config.get('language'):
        return config['language']
    return DEFAULT_LANGUAGE

# ========== 保留静态变量用于兼容（旧代码可能直接导入） ==========
API_KEY = get_api_key()
BASE_URL = get_base_url()
TARGET_LANGUAGE = get_target_language()

# ========== 并发设置 ==========
MAX_WORKERS = 5  # 并发处理的线程数

# ========== 文本处理设置 ==========
# DeepSeek-V4 上下文窗口 1M tokens（约 160 万中文字符），输出最大 384K tokens
CHUNK_SIZE = 1300000  # 长文本分块/装箱上限（字符数）
OVERLAP = 5000        # 分块重叠字符数
MAX_LENGTH = 1400000  # 超过此长度自动触发分块并发处理

# ========== 模型偏好配置 ==========
_ANALYTICAL_REDUCE_PRO = False  # analytical Reduce 阶段是否使用 deepseek-v4-pro
_GENERATIVE_FINAL_PRO = False   # generative 最终生成是否使用 deepseek-v4-pro


def set_analytical_reduce_pro(enabled: bool) -> None:
    global _ANALYTICAL_REDUCE_PRO
    _ANALYTICAL_REDUCE_PRO = bool(enabled)


def get_analytical_reduce_pro() -> bool:
    return _ANALYTICAL_REDUCE_PRO


def set_generative_final_pro(enabled: bool) -> None:
    global _GENERATIVE_FINAL_PRO
    _GENERATIVE_FINAL_PRO = bool(enabled)


def get_generative_final_pro() -> bool:
    return _GENERATIVE_FINAL_PRO


def get_reduce_model_name(worker: str) -> str:
    """获取 Reduce/最终生成阶段的模型名。
    worker: 'analytical' | 'generative'
    """
    if worker == "analytical" and _ANALYTICAL_REDUCE_PRO:
        return "deepseek-v4-pro"
    if worker == "generative" and _GENERATIVE_FINAL_PRO:
        return "deepseek-v4-pro"
    return "deepseek-v4-flash"


# DeepSeek V4 Thinking Max 的统一参数（思考模式下 temperature 不生效）
THINKING_MAX_PARAMS = {
    "extra_body": {"thinking": {"type": "enabled"}},
    "reasoning_effort": "max",
}
