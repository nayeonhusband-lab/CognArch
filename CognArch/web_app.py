"""
CognArch Streamlit Web Application (Modern Minimalist Edition)
================================================================
本地 Web UI，参考 Manus 极简现代风格 (大圆角、浅色系、无衬线字体、悬浮阴影)。
核心逻辑与架构保持完全一致。
"""

import streamlit as st
import os
import sys
import json
import re
import shutil
from datetime import datetime

# 添加应用目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paths import APP_ROOT, DATA_ROOT

# APP_ROOT 放代码和内置资源；PROJECT_ROOT 保留旧变量名，指向可写数据根。
APP_ROOT_STR = str(APP_ROOT)
PROJECT_ROOT = str(DATA_ROOT)

from orchestrator import Orchestrator, SYSTEM_PATHS
from utils import resolve_path  # 导入路径解析工具
import vector_store  # 导入向量库同步功能
from config import (
    get_config_path,
    set_runtime_config,
    set_analytical_reduce_pro,
    get_analytical_reduce_pro,
    set_generative_final_pro,
    get_generative_final_pro,
)


# ==================== 1. 全局配置 (必须在第一行) ====================
st.set_page_config(
    page_title="CognArch",
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="expanded"
)


# ==================== 2. 注入 Manus 极简现代风 CSS 样式 ====================
def inject_manus_minimalist_css():
    st.markdown("""
<style>
    /* 引入现代无衬线字体 */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    /* 全局背景与现代字体 */
    .stApp {
        background: #ffffff;
        color: #111827;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }

    /* 优雅地隐藏默认菜单、Deploy按钮和页脚 */
    header {background: transparent !important;}
    footer {visibility: hidden !important;}
    .stDeployButton {display: none !important;}
    #MainMenu {visibility: hidden !important;}

    /* 🚀 彻底隐藏侧边栏顶部的折叠/展开按钮，解决 arrow 乱码问题，让侧边栏成为固定式导航 */
    [data-testid="stSidebarCollapseButton"] {
        display: none !important;
    }

    /* 防止强制覆盖 Streamlit 系统图标导致变成纯文本 */
    span[class*="material"] {
        font-family: 'Material Symbols Rounded', 'Material Icons', sans-serif !important;
    }

    /* 主页面间距，拉宽并留白 */
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 5rem !important;
        max-width: 900px !important; /* 限制主内容宽度，营造居中沉浸感 */
    }

    /* 侧边栏：极简浅灰白 */
    [data-testid="stSidebar"] {
        background-color: #f9fafb !important;
        border-right: 1px solid #f3f4f6 !important;
        min-width: 280px !important;
    }

    /* 侧边栏标题弱化 */
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
        text-shadow: none;
        border-bottom: none;
        font-weight: 600;
        font-size: 1rem;
        color: #111827 !important;
        padding-bottom: 0.5rem;
        margin-top: 1rem;
    }

    /* 聊天气泡：去除边框，极简扁平化 */
    .stChatMessage {
        background: transparent;
        border: none;
        box-shadow: none;
        margin-bottom: 1.5rem;
        padding: 0.5rem 1rem;
        color: #111827 !important;
    }
    /* 区分用户和AI气泡 */
    .stChatMessage[data-testid="stChatMessage"]:nth-child(odd) {
        background: #f9fafb;
        border-radius: 16px;
    }
    .stChatMessage * {
        color: #111827 !important;
        line-height: 1.6;
    }

    /* 页面上部主标题：Manus 风格 */
    .manus-title {
        color: #111827;
        font-weight: 500;
        text-align: center;
        margin-top: 4rem;
        margin-bottom: 1.5rem;
        font-size: 2.25rem;
        letter-spacing: -0.025em;
    }

    /* 🌟 Memo Tips 卡片样式 (现代质感) */
    .memo-tips {
        background-color: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 16px;
        padding: 1.5rem 2rem;
        margin: 0 auto 3rem auto;
        max-width: 760px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.02), 0 2px 4px -1px rgba(0, 0, 0, 0.02);
        color: #4b5563;
        font-size: 0.95rem;
        line-height: 1.6;
    }
    .memo-title {
        font-weight: 600;
        color: #111827;
        font-size: 1.05rem;
        margin-bottom: 0.75rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .memo-tips ol {
        margin-top: 0;
        margin-bottom: 0;
        padding-left: 1.25rem;
    }
    .memo-tips li {
        margin-bottom: 0.5rem;
    }
    .memo-tips li:last-child {
        margin-bottom: 0;
    }
    .memo-tips b {
        color: #111827;
        font-weight: 600;
    }

    /* === 按钮样式系统 === */
    /* 次级按钮 (默认) */
    .stButton > button {
        background: #f3f4f6 !important;
        color: #111827 !important;
        border: none !important;
        border-radius: 8px !important;
        font-weight: 500 !important;
        transition: all 0.2s ease !important;
        padding: 0.5rem 1rem !important;
    }
    .stButton > button:hover {
        background: #e5e7eb !important;
        box-shadow: none !important;
        border: none !important;
        color: #111827 !important;
    }
    /* 主按钮 (Primary) */
    .stButton > button[kind="primary"] {
        background: #111827 !important;
        color: #ffffff !important;
    }
    .stButton > button[kind="primary"]:hover {
        background: #374151 !important;
        color: #ffffff !important;
    }

    /* 输入框：超大圆角、居中悬浮感 */
    .stTextInput > div > div > input, .stTextArea > div > div > textarea {
        background-color: #ffffff !important;
        color: #111827 !important;
        border: 1px solid #e5e7eb !important;
        border-radius: 12px !important;
        padding: 0.75rem 1rem !important;
        box-shadow: 0 1px 2px 0 rgba(0,0,0,0.05) !important;
    }
    
    /* 底部 Chat Input 专属大圆角悬浮样式 */
    .stChatInput > div {
        background-color: #ffffff !important;
        border: 1px solid #e5e7eb !important;
        border-radius: 24px !important; /* 超大圆角 */
        padding: 0.5rem 1rem !important;
        box-shadow: 0 10px 15px -3px rgba(0,0,0,0.05), 0 4px 6px -2px rgba(0,0,0,0.025) !important;
    }
    
    .stTextInput > div > div > input:focus, .stChatInput > div:focus-within {
        border: 1px solid #e5e7eb !important;
        box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.1) !important;
    }

    /* Tab 样式：现代胶囊形 (Pill-shaped) */
    .stTabs [data-baseweb="tab-list"] {
        gap: 1rem;
        border-bottom: none;
        justify-content: center;
        margin-bottom: 1.5rem;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: #f3f4f6;
        border-radius: 9999px;
        border: none;
        color: #6b7280;
        font-weight: 500;
        padding: 0.5rem 1.5rem;
        margin: 0;
    }
    .stTabs [aria-selected="true"] {
        background-color: #111827 !important;
        color: #ffffff !important;
    }
    
    /* 侧边栏 Expander 样式调整 */
    [data-testid="stExpander"] {
        border: 1px solid #e5e7eb !important;
        border-radius: 12px !important;
        background-color: #ffffff !important;
    }
    [data-testid="stExpander"] p {
        font-weight: 500 !important;
    }
    
    /* 强制隐藏 Expander 自带的下拉小箭头，做到极致极简 */
    [data-testid="stExpanderToggleIcon"] {
        display: none !important;
    }
</style>
""", unsafe_allow_html=True)


# ==================== 3. 核心业务逻辑 (保持原样不动) ====================
def init_orchestrator():
    """初始化 Orchestrator（单例），支持配置变化时重建"""
    config = load_config()
    current_config_key = f"{config.get('api_key', '')}_{config.get('base_url', '')}_{config.get('remember_api_key', False)}"

    if st.session_state.get('last_applied_config_key') != current_config_key:
        apply_runtime_config(config)
        st.session_state.last_applied_config_key = current_config_key

    if 'orchestrator' not in st.session_state:
        st.session_state.orchestrator = Orchestrator()
        st.session_state.chat_history = []
        st.session_state.last_config_key = current_config_key
    elif st.session_state.get('last_config_key') != current_config_key:
        st.session_state.orchestrator = Orchestrator()
        st.session_state.chat_history = []
        st.session_state.last_config_key = current_config_key

    return st.session_state.orchestrator


def normalize_web_config(config=None):
    """Normalize web config so UI and runtime always see a stable shape."""
    config = config or {}
    api_key = (config.get('api_key') or '').strip()
    remember_api_key = bool(config.get('remember_api_key', bool(api_key)))

    return {
        'api_key': api_key,
        'base_url': (config.get('base_url') or 'https://api.deepseek.com/v1').strip() or 'https://api.deepseek.com/v1',
        'language': config.get('language') or 'Simplified Chinese',
        'remember_api_key': remember_api_key,
    }


def refresh_runtime_clients():
    """Refresh module-level OpenAI clients so runtime BYOK takes effect immediately."""
    import analytical_worker
    import generative_worker

    analytical_worker.client = analytical_worker.get_openai_client()
    generative_worker.client = generative_worker.get_openai_client()


def apply_runtime_config(config):
    """Apply current web config to runtime-only overrides."""
    normalized = normalize_web_config(config)
    set_runtime_config(
        api_key=normalized.get('api_key', ''),
        base_url=normalized.get('base_url', 'https://api.deepseek.com/v1'),
        language=normalized.get('language', 'Simplified Chinese'),
    )
    refresh_runtime_clients()


def load_config():
    """加载配置"""
    if 'config' not in st.session_state:
        config = load_config_from_file() or {}
        st.session_state.config = normalize_web_config(config)
    else:
        st.session_state.config = normalize_web_config(st.session_state.config)
    return st.session_state.config


def load_config_from_file():
    """从配置文件加载配置"""
    config_path = get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                normalized = normalize_web_config(json.load(f))
                if not normalized.get('remember_api_key'):
                    normalized['api_key'] = ''
                return normalized
        except:
            pass
    return None


def save_config_to_file(config):
    """保存配置到文件"""
    config_path = get_config_path()
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False


def get_dynamic_api_key():
    return load_config().get('api_key', '')

def get_dynamic_base_url():
    return load_config().get('base_url', 'https://api.deepseek.com/v1')

def get_dynamic_language():
    return load_config().get('language', 'Simplified Chinese')


def get_notes_list():
    """获取知识库笔记列表"""
    notes_dir = os.path.join(PROJECT_ROOT, "knowledge_base", "notes")
    if not os.path.exists(notes_dir):
        return []
    files = []
    for f in os.listdir(notes_dir):
        if f.endswith('.md'):
            full_path = os.path.join(notes_dir, f)
            mtime = os.path.getmtime(full_path)
            files.append({
                'name': f,
                'path': full_path,
                'mtime': mtime
            })
    return sorted(files, key=lambda x: x['mtime'], reverse=True)


def get_sessions_list():
    """获取会话列表及产物（按会话分组）"""
    sessions_dir = os.path.join(PROJECT_ROOT, "sessions")
    if not os.path.exists(sessions_dir):
        return []
    sessions = []
    for d in os.listdir(sessions_dir):
        session_path = os.path.join(sessions_dir, d)
        if os.path.isdir(session_path):
            mtime = os.path.getmtime(session_path)
            outputs_path = os.path.join(session_path, "outputs")
            outputs = []
            if os.path.exists(outputs_path):
                for f in os.listdir(outputs_path):
                    if f.endswith('.md'):
                        outputs.append(f)
            sessions.append({
                'name': d,
                'path': session_path,
                'outputs': outputs,
                'outputs_path': outputs_path,
                'mtime': mtime
            })
    return sorted(sessions, key=lambda x: x['mtime'], reverse=True)


from file_hash_cache import (
    DUPLICATES_DIRNAME,
    SUPPORTED_INPUT_EXTS,
    build_file_infos,
    build_hash_lookup,
    clear_confirm_force_rerun,
    clear_force_rerun_for_missing_files,
    get_confirm_force_rerun_path,
    get_or_compute_file_hash,
    hash_bytes,
    is_force_rerun,
    mark_force_rerun,
    next_available_path,
    rebuild_hash_index,
    remove_hash_entry,
    set_confirm_force_rerun,
)

def get_all_outputs():
    """获取所有会话中的 MD 产物，按时间排序"""
    sessions_dir = os.path.join(PROJECT_ROOT, "sessions")
    if not os.path.exists(sessions_dir):
        return []

    all_outputs = []
    for d in os.listdir(sessions_dir):
        session_path = os.path.join(sessions_dir, d)
        if os.path.isdir(session_path):
            outputs_path = os.path.join(session_path, "outputs")
            if os.path.exists(outputs_path):
                for f in os.listdir(outputs_path):
                    if f.endswith('.md'):
                        full_path = os.path.join(outputs_path, f)
                        mtime = os.path.getmtime(full_path)
                        all_outputs.append({
                            'name': f,
                            'path': full_path,
                            'session': d,
                            'mtime': mtime
                        })
    return sorted(all_outputs, key=lambda x: x['mtime'], reverse=True)


def get_inputs_list():
    """Return real pending files from inputs/ top level only."""
    inputs_dir = os.path.join(PROJECT_ROOT, "inputs")
    return build_file_infos(inputs_dir, recursive=False)


def get_duplicate_inputs_list():
    """Return files parked under inputs/_duplicates."""
    duplicates_dir = os.path.join(PROJECT_ROOT, "inputs", DUPLICATES_DIRNAME)
    return build_file_infos(duplicates_dir, recursive=False)


def _classify_input_files():
    """Classify top-level inputs into pending vs conflicts, using processed hashes as source of truth."""
    clear_force_rerun_for_missing_files()
    pending = []
    conflicts = list(get_duplicate_inputs_list())
    processed_lookup = build_hash_lookup(os.path.join(PROJECT_ROOT, "processed"), recursive=False)
    seen_hashes = set()

    for info in get_inputs_list():
        info = dict(info)
        digest = info.get("sha256") or get_or_compute_file_hash(info["path"])
        info["sha256"] = digest
        force_rerun = is_force_rerun(info["path"])
        info["force_rerun"] = force_rerun
        if digest in seen_hashes and not force_rerun:
            info["conflict_reason"] = "duplicate_input"
            conflicts.append(info)
            continue
        if digest in processed_lookup and not force_rerun:
            info["conflict_reason"] = "already_processed"
            conflicts.append(info)
            continue
        pending.append(info)
        seen_hashes.add(digest)

    return pending, sorted(conflicts, key=lambda x: x["mtime"], reverse=True)


def _write_uploaded_file(target_dir, filename, content: bytes) -> str:
    os.makedirs(target_dir, exist_ok=True)
    target_path = next_available_path(os.path.join(target_dir, filename))
    with open(target_path, "wb") as f:
        f.write(content)
    get_or_compute_file_hash(target_path)
    return target_path


def _save_uploaded_inputs(uploaded_files) -> dict:
    inputs_dir = os.path.join(PROJECT_ROOT, "inputs")
    duplicates_dir = os.path.join(inputs_dir, DUPLICATES_DIRNAME)
    os.makedirs(inputs_dir, exist_ok=True)
    os.makedirs(duplicates_dir, exist_ok=True)

    processed_lookup = build_hash_lookup(os.path.join(PROJECT_ROOT, "processed"), recursive=False)
    input_lookup = build_hash_lookup(inputs_dir, recursive=False)
    duplicate_lookup = build_hash_lookup(duplicates_dir, recursive=False)

    saved = []
    duplicates = []
    deduped_existing = []

    for uploaded_file in uploaded_files:
        file_bytes = bytes(uploaded_file.getbuffer())
        digest = hash_bytes(file_bytes)
        existing_duplicate = (
            duplicate_lookup.get(digest)
            or input_lookup.get(digest)
            or processed_lookup.get(digest)
            or []
        )

        if existing_duplicate:
            if digest not in duplicate_lookup:
                target_path = _write_uploaded_file(duplicates_dir, uploaded_file.name, file_bytes)
                duplicate_lookup.setdefault(digest, []).append({"path": target_path})
                duplicates.append(os.path.basename(target_path))
            else:
                deduped_existing.append(uploaded_file.name)
            continue

        target_path = _write_uploaded_file(inputs_dir, uploaded_file.name, file_bytes)
        input_lookup.setdefault(digest, []).append({"path": target_path})
        saved.append(os.path.basename(target_path))

    return {"saved": saved, "duplicates": duplicates, "deduped_existing": deduped_existing}


def _delete_sidebar_file(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)
    remove_hash_entry(path)
    clear_confirm_force_rerun()


def _promote_duplicate_for_rerun(path: str) -> str:
    inputs_dir = os.path.join(PROJECT_ROOT, "inputs")
    base_name = os.path.basename(path)
    stem, ext = os.path.splitext(base_name)
    rerun_name = f"{stem}__rerun_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
    target_path = next_available_path(os.path.join(inputs_dir, rerun_name))
    shutil.move(path, target_path)
    remove_hash_entry(path)
    get_or_compute_file_hash(target_path)
    mark_force_rerun(target_path)
    clear_confirm_force_rerun()
    return target_path


def read_md_file(file_path):
    """读取 MD 文件内容"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"读取失败: {str(e)}"


def render_sidebar():
    """渲染侧边栏"""
    st.sidebar.markdown("<h2 style='font-size: 1.5rem; font-weight: 700; color: #111827; margin-bottom: 1.5rem;'>✨ CognArch</h2>", unsafe_allow_html=True)

    # 1. 隐藏技术细节：将配置收纳到 Expander 中
    with st.sidebar.expander("⚙️ 高级配置 (API / 语言)", expanded=False):
        config = load_config()
        current_lang = config.get('language', 'Simplified Chinese')
        language = st.selectbox(
            "输出语言",
            options=["Simplified Chinese", "English"],
            index=0 if current_lang == "Simplified Chinese" else 1
        )
        api_key = st.text_input("API Key", value=config.get('api_key', ''), type="password", placeholder="sk-...")
        base_url = st.text_input("Base URL", value=config.get('base_url', 'https://api.deepseek.com/v1'))
        remember_api_key = st.checkbox("记住 API Key", value=config.get('remember_api_key', False))
        
        if st.button("保存配置", use_container_width=True):
            session_config = normalize_web_config({
                'api_key': api_key,
                'base_url': base_url,
                'language': language,
                'remember_api_key': remember_api_key,
            })
            persisted_config = dict(session_config)
            if not remember_api_key:
                persisted_config['api_key'] = ''

            if save_config_to_file(persisted_config):
                st.success("配置已保存")
                st.session_state.config = session_config
                apply_runtime_config(session_config)
                st.session_state.last_config_key = None
                st.session_state.last_applied_config_key = None
            else:
                st.error("保存失败")

    st.sidebar.markdown("<br>", unsafe_allow_html=True)

    # 2. 突出核心工作流：载入与重构
    if 'uploader_nonce' not in st.session_state:
        st.session_state.uploader_nonce = 0
    if 'sidebar_notice' in st.session_state and st.session_state.sidebar_notice:
        st.sidebar.info(st.session_state.sidebar_notice)
        st.session_state.sidebar_notice = ""

    pending_inputs, conflict_inputs = _classify_input_files()

    st.sidebar.markdown("### 载入文献数据")
    uploaded_files = st.sidebar.file_uploader(
        "Upload files",
        type=[ext.lstrip(".") for ext in SUPPORTED_INPUT_EXTS],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key=f"upload_files_{st.session_state.uploader_nonce}",
    )

    if uploaded_files:
        upload_result = _save_uploaded_inputs(uploaded_files)
        notices = []
        if upload_result["saved"]:
            notices.append(f"新增待处理 {len(upload_result['saved'])} 份")
        if upload_result["duplicates"]:
            notices.append(f"重复/冲突 {len(upload_result['duplicates'])} 份")
        if upload_result["deduped_existing"]:
            notices.append(f"已存在重复 {len(upload_result['deduped_existing'])} 份")
        st.session_state.sidebar_notice = "；".join(notices) if notices else "上传完成"
        st.session_state.uploader_nonce += 1
        st.rerun()

    # 主按钮：加上 type="primary" 激活 CSS 中的深色样式
    if st.sidebar.button("🚀 启动知识重构", use_container_width=True, type="primary"):
        if pending_inputs:
            st.session_state.pending_file_processing = True
        else:
            st.sidebar.warning("请先上传文献数据或确保 inputs 文件夹中有可处理文件")

    if pending_inputs:
        st.sidebar.markdown("<div style='margin-top: 1rem; color: #6b7280; font-size: 0.875rem;'>待处理队列:</div>", unsafe_allow_html=True)
        for info in pending_inputs:
            row = st.sidebar.columns([5, 1])
            row[0].markdown(f"<div style='font-size: 0.875rem; color: #374151; padding: 4px 0;'>📄 {info['name']}</div>", unsafe_allow_html=True)
            if row[1].button("删", key=f"delete_pending_{info['path']}"):
                _delete_sidebar_file(info["path"])
                st.session_state.sidebar_notice = f"已删除 {info['name']}"
                st.rerun()
    else:
        st.sidebar.caption("当前没有待处理文件。")

    if conflict_inputs:
        confirm_target = get_confirm_force_rerun_path()
        st.sidebar.markdown("<div style='margin-top: 1rem; color: #b45309; font-size: 0.875rem;'>重复 / 冲突:</div>", unsafe_allow_html=True)
        for info in conflict_inputs:
            st.sidebar.markdown(f"<div style='font-size: 0.85rem; color: #92400e; padding: 2px 0;'>⚠️ {info['name']}</div>", unsafe_allow_html=True)
            actions = st.sidebar.columns(2)
            if actions[0].button("删除", key=f"delete_conflict_{info['path']}"):
                _delete_sidebar_file(info["path"])
                st.session_state.sidebar_notice = f"已删除 {info['name']}"
                st.rerun()
            rerun_label = "确认重跑" if confirm_target == info["relpath"] else "强制重跑"
            if actions[1].button(rerun_label, key=f"rerun_conflict_{info['path']}"):
                if confirm_target == info["relpath"]:
                    new_path = _promote_duplicate_for_rerun(info["path"])
                    st.session_state.sidebar_notice = f"已加入强制重跑: {os.path.basename(new_path)}"
                else:
                    set_confirm_force_rerun(info["path"])
                    st.session_state.sidebar_notice = f"再次点击即可强制重跑: {info['name']}"
                st.rerun()

    # ==================== 知识库维护区域 ====================
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔄 知识库维护")
    if st.sidebar.button("🔨 重建知识库", use_container_width=True):
        with st.spinner("正在重建知识库（清理索引 + 重建向量）..."):
            orch = init_orchestrator()
            result = orch.workers["RunnerWorker"].rebuild_index_json()
            if result["status"] != "no_index":
                vector_store.rebuild_vector_store(force=True)
        if result["status"] == "no_index":
            st.sidebar.info("尚未创建 index.json")
        else:
            msg = f"✅ 重建完成：保留 {result['kept']} 条笔记"
            if result["removed"]:
                msg += f"，清理 {result['removed']} 条失效记录"
            if result["orphan_files"]:
                msg += f"，发现 {result['orphan_files']} 个未入库笔记"
            st.sidebar.success(msg)

    if st.sidebar.button("🔄 重建文件索引", use_container_width=True):
        with st.spinner("正在扫描 inputs/ 和 processed/ ..."):
            result = rebuild_hash_index(PROJECT_ROOT)
        dup_msg = f"，发现 {result['duplicates']} 组重复" if result['duplicates'] else ""
        st.sidebar.success(
            f"✅ 扫描 {result['scanned']} 个文件"
            f"（复用 {result['reused']}，重算 {result['recalculated']}，清理 {result['removed']}）{dup_msg}"
        )

    # ==================== 模型偏好设置 ====================
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🧠 模型偏好")

    analytical_pro = st.sidebar.toggle(
        "知识重构 Reduce 使用 Pro",
        value=get_analytical_reduce_pro(),
        help="开启后，analytical worker 的合并/Reduce 阶段使用 deepseek-v4-pro",
    )
    set_analytical_reduce_pro(analytical_pro)

    generative_pro = st.sidebar.toggle(
        "最终生成使用 Pro",
        value=get_generative_final_pro(),
        help="开启后，generative worker 的最终输出使用 deepseek-v4-pro",
    )
    set_generative_final_pro(generative_pro)

# ==================== 输出捕获器 ====================
class OutputCapture:
    def __init__(self):
        self.output = ""
        self.display_output = ""
        self.st_element = None
        self._buffer = ""
        self._display_lines = []
        self._max_display_lines = 120
        self._path_resolution_counts = {}
        self._batch_progress = {}
        self._map_progress = None
        self._note_fallback = {"attempts": 0, "success": 0, "local": 0}
        self._doc_list_hidden = False
        self._doc_list_hidden_count = 0
        self._semantic_match_count = 0
        self._phase2_progress = None

    def _append_display_line(self, line):
        if not line:
            return
        self._display_lines.append(line)
        if len(self._display_lines) > self._max_display_lines:
            self._display_lines = self._display_lines[-self._max_display_lines:]

    def _render(self):
        self.display_output = "\n".join(self._display_lines)
        if self.st_element:
            self.st_element.text(self.display_output or "✨ 正在启动...")

    def _flush_pending_summaries(self):
        if self._doc_list_hidden_count:
            self._append_display_line(f"   🗂️ [待筛选文献列表] 已隐藏 {self._doc_list_hidden_count} 条文献标题")
            self._doc_list_hidden_count = 0

        if self._semantic_match_count:
            self._append_display_line(f"   🧠 [语义匹配] 已隐藏 {self._semantic_match_count} 条候选文献")
            self._semantic_match_count = 0

        if self._path_resolution_counts:
            total = sum(self._path_resolution_counts.values())
            parts = ", ".join(f"{key}={value}" for key, value in sorted(self._path_resolution_counts.items()))
            self._append_display_line(f"   🔗 [路径解析] 已定位原始文件 {total} 条（{parts}）")
            self._path_resolution_counts = {}

        for stage_name, count in list(self._batch_progress.items()):
            if count > 0:
                self._append_display_line(f"   🔄 [{stage_name}] 已处理 {count} 批")
        self._batch_progress = {}

        if self._map_progress:
            current, total = self._map_progress
            self._append_display_line(f"   🧩 [Map 阶段] 已提交 {current}/{total} 箱（workers=4）")
            self._map_progress = None

        if self._phase2_progress:
            completed = self._phase2_progress.get("completed", 0)
            total = self._phase2_progress.get("total", 0)
            if total:
                self._append_display_line(f"   🔄 [多轮处理] 已完成 {completed}/{total} 批")
            self._phase2_progress = None

        if any(self._note_fallback.values()):
            self._append_display_line(
                "   ⚠️ [笔记回退] "
                f"局部定位 {self._note_fallback['local']} 次，"
                f"回退尝试 {self._note_fallback['attempts']} 次，"
                f"成功 {self._note_fallback['success']} 次"
            )
            self._note_fallback = {"attempts": 0, "success": 0, "local": 0}

    def _ingest_line(self, line):
        if not line or not line.strip():
            return

        stripped = line.rstrip()
        compact_line = stripped.strip()

        if "待筛选文献列表" in compact_line:
            self._flush_pending_summaries()
            self._append_display_line(stripped)
            self._doc_list_hidden = True
            return

        if self._doc_list_hidden:
            if re.match(r"^\[\d+\]\s+", compact_line):
                self._doc_list_hidden_count += 1
                return
            self._doc_list_hidden = False
            self._flush_pending_summaries()

        path_match = re.search(r"\[路径解析\]\s*已定位原始文件\(([^)]+)\)", compact_line)
        if path_match:
            source = path_match.group(1).strip()
            self._path_resolution_counts[source] = self._path_resolution_counts.get(source, 0) + 1
            return

        if "✓ 匹配:" in compact_line:
            self._semantic_match_count += 1
            return

        batch_match = re.search(r"\[(Coarse|Meso扫描|LLM筛选)\]\s*第\s*(\d+)\s*批", compact_line)
        if batch_match and "⚠️" not in compact_line and "❌" not in compact_line:
            stage_name = batch_match.group(1)
            batch_idx = int(batch_match.group(2))
            self._batch_progress[stage_name] = max(self._batch_progress.get(stage_name, 0), batch_idx)
            return

        phase_start_match = re.search(r"正在处理第\s*(\d+)/(\d+)\s*批", compact_line)
        if phase_start_match and "⚠️" not in compact_line and "❌" not in compact_line:
            current = int(phase_start_match.group(1))
            total = int(phase_start_match.group(2))
            self._phase2_progress = {
                "current": current,
                "completed": max(self._phase2_progress.get("completed", 0) if self._phase2_progress else 0, current - 1),
                "total": total
            }
            return

        phase_done_match = re.search(r"第\s*(\d+)\s*批处理完毕", compact_line)
        if phase_done_match and "⚠️" not in compact_line and "❌" not in compact_line:
            completed = int(phase_done_match.group(1))
            total = self._phase2_progress.get("total", completed) if self._phase2_progress else completed
            self._phase2_progress = {
                "current": completed,
                "completed": completed,
                "total": total
            }
            return

        map_match = re.search(r"处理第\s*(\d+)/(\d+)\s*箱", compact_line)
        if map_match:
            self._map_progress = (int(map_match.group(1)), int(map_match.group(2)))
            return

        if "尝试从笔记回退" in compact_line:
            self._note_fallback["attempts"] += 1
            return
        if "回退成功:" in compact_line:
            self._note_fallback["success"] += 1
            return
        if "尝试使用笔记局部上下文" in compact_line:
            self._note_fallback["local"] += 1
            return

        if "DEBUG" in compact_line:
            return

        self._flush_pending_summaries()
        self._append_display_line(stripped)

    def write(self, text):
        if text:
            self.output += text
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._ingest_line(line)
            self._render()

    def flush(self):
        if self._buffer:
            self._ingest_line(self._buffer)
            self._buffer = ""
        if self._doc_list_hidden:
            self._doc_list_hidden = False
        self._flush_pending_summaries()
        self._render()

    def isatty(self):
        return False

    def finalize(self):
        self.flush()
        return self.display_output or self.output


def persist_run_log(session_dir: str, raw_output: str, extra_text: str = "") -> str:
    """Persist the full raw execution stream into the current session directory."""
    if not session_dir:
        return ""

    logs_dir = os.path.join(session_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, "run.log")

    payload = (raw_output or "").rstrip()
    if extra_text:
        payload = f"{payload}\n\n{extra_text}".strip()

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(payload)

    return log_path


@st.fragment()
def chat_input_fragment():
    orchestrator = init_orchestrator()

    if 'process_output' not in st.session_state:
        st.session_state.process_output = []
    if 'is_running' not in st.session_state:
        st.session_state.is_running = False
    if 'retrieval_doc_cap' not in st.session_state:
        st.session_state.retrieval_doc_cap = 50

    # ===== 第1步：输入框（固定在最上面）=====
    prompt = st.chat_input("描述你想要进行的数据分析或检索...")
    st.number_input("自动检索文献上限", min_value=1, max_value=500, step=1, key="retrieval_doc_cap")
    st.caption("仅作用于自动检索文献；手动勾选的预选文献不会被这个上限裁剪。")

    # ===== 预选文献选择区域（输入框下方）=====
    import os
    import json

    # 加载知识库索引
    index_path = os.path.join(PROJECT_ROOT, "knowledge_base", "index.json")
    available_docs = []
    if os.path.exists(index_path):
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
                for item in index_data:
                    filepath = item.get('filepath')
                    # 使用 resolve_path 将相对路径解析为绝对路径后再验证
                    abs_filepath = resolve_path(filepath) if filepath else None
                    if abs_filepath and os.path.exists(abs_filepath):
                        title = item.get('title', os.path.basename(filepath))
                        available_docs.append({"title": title, "filepath": abs_filepath})
        except Exception as e:
            print(f"加载知识库索引失败: {e}")

    # 初始化选择状态
    if 'doc_selection' not in st.session_state:
        st.session_state.doc_selection = {}  # {filepath: bool}

    # 如果有新文档，更新选择状态（处理新增文档）
    for doc in available_docs:
        if doc['filepath'] not in st.session_state.doc_selection:
            st.session_state.doc_selection[doc['filepath']] = False

    # 清理已删除的文档选择状态
    current_filepaths = set(doc['filepath'] for doc in available_docs)
    st.session_state.doc_selection = {
        fp: selected for fp, selected in st.session_state.doc_selection.items()
        if fp in current_filepaths
    }

    # 渲染文件选择器（可收放）
    if available_docs:
        st.markdown("---")

        # 初始化widget状态：将doc_selection同步到widget key
        # 这样Streamlit checkbox能正确显示初始状态
        for doc in available_docs:
            fp = doc['filepath']
            cb_key = f"cb_{fp}"
            if cb_key not in st.session_state:
                st.session_state[cb_key] = st.session_state.doc_selection.get(fp, False)
            else:
                # widget状态可能存在，确保与doc_selection同步
                st.session_state.doc_selection[fp] = st.session_state[cb_key]

        selected_count = sum(1 for s in st.session_state.doc_selection.values() if s)

        # 保持 expander 展开状态
        if 'doc_expander_expanded' not in st.session_state:
            st.session_state.doc_expander_expanded = False

        # 全选/取消全选回调函数
        def toggle_select_all():
            """切换全选状态 - 同时更新doc_selection和widget状态"""
            all_selected = all(st.session_state.doc_selection.values())
            new_state = not all_selected

            for doc in available_docs:
                fp = doc['filepath']
                cb_key = f"cb_{fp}"
                # 更新真相源
                st.session_state.doc_selection[fp] = new_state
                # 关键：同时更新widget状态，确保UI同步
                st.session_state[cb_key] = new_state

            st.session_state.doc_expander_expanded = True

        # checkbox变更回调函数
        def on_checkbox_change(filepath):
            """checkbox状态变更时更新doc_selection"""
            cb_key = f"cb_{filepath}"
            st.session_state.doc_selection[filepath] = st.session_state[cb_key]
            st.session_state.doc_expander_expanded = True

        # 创建 expander，默认收起
        expander_label = f"📂 预选文献 ({selected_count}/{len(available_docs)})"
        with st.expander(expander_label, expanded=st.session_state.doc_expander_expanded):
            # 全选/取消全选按钮
            col1, col2 = st.columns([1, 4])
            with col1:
                all_selected = selected_count == len(available_docs) and selected_count > 0
                btn_label = "取消全选" if all_selected else "全选"
                st.button(btn_label, key="select_all_docs", on_click=toggle_select_all)

            with col2:
                if selected_count > 0:
                    st.caption(f"已选择 {selected_count} 篇文献")
                else:
                    st.caption("未选择任何文献")

            # 搜索框 - 过滤文献列表
            search_query = st.text_input(
                "🔍 搜索",
                key="doc_search_query",
                placeholder="输入关键词过滤文献...",
                label_visibility="collapsed"
            )

            # 过滤文献列表
            filtered_docs = available_docs
            if search_query.strip():
                query = search_query.strip().lower()
                filtered_docs = [d for d in available_docs
                               if query in d['title'].lower()]
                if len(filtered_docs) != len(available_docs):
                    st.caption(f"显示 {len(filtered_docs)}/{len(available_docs)} 篇文献")

            # 渲染过滤后的文件列表 - 不使用value参数，让Streamlit从key管理状态
            for doc in filtered_docs:
                fp = doc['filepath']
                st.checkbox(
                    doc['title'],
                    key=f"cb_{fp}",
                    on_change=on_checkbox_change,
                    args=(fp,)
                )

    # ===== 第2步：待处理文件（输入框下方显示输出）=====
    if st.session_state.get('pending_file_processing'):
        st.session_state.pending_file_processing = False
        user_msg = "🚀 启动知识重构"
        st.chat_message("user", avatar="✨").markdown(user_msg)
        st.session_state.chat_history.append({'role': 'user', 'content': user_msg})

        st.markdown("**✨ 进程输出**")
        process_output = st.empty()
        process_output.markdown("✨ 正在启动...")
        st.session_state.is_running = True

        try:
            import sys
            capture = OutputCapture()
            capture.st_element = process_output
            old_stdout = sys.stdout
            sys.stdout = capture

            try:
                orchestrator.create_session()
                shared = orchestrator.shared_workspace
                runner = orchestrator.workers["RunnerWorker"]
                task_params = {
                    "input_dir": SYSTEM_PATHS["inputs"],
                    "user_instruction": ""
                }
                result = runner.execute(task_params, shared)
            finally:
                sys.stdout = old_stdout

            output = capture.output
            compact_output = capture.finalize()
            session_dir = orchestrator.shared_workspace.get("session_dir", "")
            raw_log_path = persist_run_log(session_dir, output)
            if raw_log_path:
                compact_output = f"{compact_output}\n📝 详细日志已保存: {raw_log_path}".strip()

            st.session_state.is_running = False
            st.session_state.process_output.insert(0, compact_output)
            process_output.text(compact_output)
            st.success("✨ 知识库构建完毕！")
            # 清除已处理的文件列表，避免重复处理
            st.session_state.sidebar_notice = "知识重构已完成，侧栏文件状态已刷新。"
            st.rerun()
        except Exception as e:
            import traceback
            error_msg = f"核心错误: {str(e)}\n{traceback.format_exc()}"
            st.session_state.is_running = False
            raw_output = capture.output if 'capture' in locals() else ""
            compact_output = capture.finalize() if 'capture' in locals() else ""
            session_dir = orchestrator.shared_workspace.get("session_dir", "") if orchestrator.shared_workspace else ""
            raw_log_path = persist_run_log(session_dir, raw_output, error_msg)
            page_output = "\n".join(
                part for part in [
                    compact_output,
                    f"❌ {str(e)}",
                    f"📝 详细日志已保存: {raw_log_path}" if raw_log_path else "",
                ] if part
            ).strip()
            st.session_state.process_output.insert(0, page_output or error_msg)
            process_output.text(page_output or error_msg)
            st.error(f"核心错误: {str(e)}")
            st.session_state.sidebar_notice = "知识重构已结束，侧栏已按当前磁盘状态刷新。"
            st.rerun()

    # ===== 第3步：进程输出历史（输入框下方）=====
    if st.session_state.process_output or st.session_state.is_running:
        st.markdown("---")
        if st.session_state.is_running:
            st.markdown("**🔄 进程输出（进行中...）**")
        else:
            st.markdown("**✨ 进程输出历史**")
        # 渲染任务，最新任务在顶部（输入框下方），旧任务向下
        total_tasks = len(st.session_state.process_output)
        for i, output in enumerate(st.session_state.process_output):
            # 任务编号：最新的是1，旧的是2,3...
            task_num = i + 1
            # 最新任务默认展开，其他默认折叠
            is_expanded = (i == 0)
            with st.expander(f"📋 任务 {task_num}", expanded=is_expanded):
                st.text(output)

    # ===== 第4步：用户输入的prompt处理 =====
    if prompt:
        # 只保留最新任务：重置 chat_history，避免输入框上方累积
        st.session_state.chat_history = []
        st.chat_message("user", avatar="✨").markdown(prompt)
        st.session_state.chat_history.append({'role': 'user', 'content': prompt})

        st.markdown("**✨ 进程输出**")
        process_output = st.empty()
        process_output.markdown("✨ 正在启动...")

        # 获取选中的文件
        st.session_state.is_running = True

        selected_files = [
            fp for fp, selected in st.session_state.doc_selection.items()
            if selected
        ]

        if selected_files:
            st.caption(f"📂 将使用 {len(selected_files)} 篇预选文献")

        try:
            import sys
            capture = OutputCapture()
            capture.st_element = process_output
            old_stdout = sys.stdout
            sys.stdout = capture

            try:
                orchestrator.create_session()
                shared = orchestrator.shared_workspace
                gen = orchestrator.workers["GenerativeWorker"]
                task_params = {
                    "user_instruction": prompt,
                    "target_filenames": selected_files if selected_files else None,
                    "retrieval_doc_cap": st.session_state.retrieval_doc_cap
                }
                response = gen.execute(task_params, shared)
            finally:
                sys.stdout = old_stdout

            output = capture.output
            compact_output = capture.finalize()
            session_dir = orchestrator.shared_workspace.get("session_dir", "")
            raw_log_path = persist_run_log(session_dir, output)
            if raw_log_path:
                compact_output = f"{compact_output}\n📝 详细日志已保存: {raw_log_path}".strip()

            st.session_state.is_running = False
            st.session_state.process_output.insert(0, compact_output)
            process_output.text(compact_output)

            # 产物已保存到 outputs 目录，提示用户去 Outputs 标签页查看
            st.success("✅ 任务已完成！请切换到「产物 (Outputs)」标签页查看结果。")
            # 只保留最新任务的消息（chat_history 已在开始时重置）
            brief_msg = "✅ 产物已生成，请到「产物 (Outputs)」标签页查看。"
            st.session_state.chat_history.append({'role': 'assistant', 'content': brief_msg})

        except Exception as e:
            import traceback
            error_msg = f"系统异常: {str(e)}\n{traceback.format_exc()}"
            st.session_state.is_running = False
            st.session_state.process_output.insert(0, f"\n错误: {str(e)}")
            raw_output = capture.output if 'capture' in locals() else ""
            compact_output = capture.finalize() if 'capture' in locals() else ""
            session_dir = orchestrator.shared_workspace.get("session_dir", "") if orchestrator.shared_workspace else ""
            raw_log_path = persist_run_log(session_dir, raw_output, error_msg)
            page_output = "\n".join(
                part for part in [
                    compact_output,
                    f"❌ {str(e)}",
                    f"📝 详细日志已保存: {raw_log_path}" if raw_log_path else "",
                ] if part
            ).strip()
            st.session_state.process_output[0] = page_output or st.session_state.process_output[0]
            process_output.text(page_output or error_msg)
            st.error(error_msg)
            # 只保留最新任务的消息（chat_history 已在开始时重置）
            st.session_state.chat_history.append({'role': 'assistant', 'content': f"❌ 任务失败: {str(e)}"})


def render_chat():
    """渲染聊天界面"""
    # 动态大标题与使用指南：只有在没有历史记录时才显示
    if not st.session_state.get('chat_history'):
        st.markdown("<div class='manus-title'>CognArch 我能为你做什么？</div>", unsafe_allow_html=True)
        
        # 插入酷炫的 Memo Tips 卡片
        tips_html = """
        <div class="memo-tips">
            <div class="memo-title">💡 厂长使用指南</div>
            <ol>
                <li>点击左侧的“<b>启动知识重构</b>”，用专家思维模式批量生成笔记！让文献沉淀成可检索的知识哦【🧠】</li>
                <li>你可以通过“<b>对话 (Chat)</b>”来指示你的厂长根据你的知识库工作😈 暂时的功能只支持<b>回答问题、文献综述、头脑风暴以及根据你提供的大纲写作哦</b>~</li>
                <li>你可以在“<b>知识库 / 产物</b>”中看到你的笔记以及生成的成果！</li>
                <li>在工厂工作的时候，注意<b>不要在知识库或者产物中查看其他的文件哦</b>~否则会打断工程😵</li>
                <li>如果你在预选文献中<b>勾选</b>了文献，那么厂长就不会去检索筛选，而是<b>直接用这些文献来生成产物</b>哦</li>
                <li>引用格式：<b>PDF后面是页数；DOCX后面是段落；XSLX后面是表格名称；PPTX后面是页数哦</b>~</li>
            </ol>
        </div>
        """
        st.markdown(tips_html, unsafe_allow_html=True)
    else:
        # 当有聊天记录时，为了布局整洁，留一点顶部空间
        st.markdown("<div style='height: 2rem;'></div>", unsafe_allow_html=True)

    for msg in st.session_state.get('chat_history', []):
        role = msg['role']
        avatar = "✨" if role == "user" else "🤖"
        with st.chat_message(role, avatar=avatar):
            st.markdown(msg['content'])

    chat_input_fragment()


def render_notes_browser():
    """渲染知识库笔记浏览器"""
    # 修改这里的列占比，让按钮自然靠右对齐
    col_title, col_btn = st.columns([8.5, 1.5])
    with col_title:
        st.markdown("<h3 style='margin-top: 0; font-size: 1.25rem;'>📚 知识库笔记</h3>", unsafe_allow_html=True)
    with col_btn:
        if st.button("🔄 刷新", key="refresh_notes", use_container_width=True):
            st.rerun()

    notes = get_notes_list()

    if not notes:
        st.info("暂无笔记。请先上传并处理文档。")
        return

    search_keyword = st.text_input("搜索笔记", placeholder="输入关键词搜索...", label_visibility="collapsed", key="note_search")

    filtered_notes = [n for n in notes if search_keyword.lower() in n['name'].lower()] if search_keyword else notes

    if not filtered_notes:
        st.warning("没有找到匹配的笔记")
        return

    filtered_names = [n['name'] for n in filtered_notes]
    selected_note = st.selectbox("选择笔记", options=filtered_names, label_visibility="collapsed")

    selected_path = next((note['path'] for note in notes if note['name'] == selected_note), None)

    if selected_path and os.path.exists(selected_path):
        content = read_md_file(selected_path)
        st.markdown("<hr style='margin: 1.5rem 0; border-color: #f3f4f6;'>", unsafe_allow_html=True)
        st.markdown(content)


def render_sessions_browser():
    """渲染会话产物浏览器"""
    # 修改这里的列占比，让按钮自然靠右对齐
    col_title, col_btn = st.columns([8.5, 1.5])
    with col_title:
        st.markdown("<h3 style='margin-top: 0; font-size: 1.25rem;'>📝 会话产物</h3>", unsafe_allow_html=True)
    with col_btn:
        if st.button("🔄 刷新", key="refresh_sessions", use_container_width=True):
            st.rerun()

    all_outputs = get_all_outputs()

    if not all_outputs:
        st.info("暂无会话产物。")
        return

    search_keyword = st.text_input("搜索产物", placeholder="输入关键词搜索...", label_visibility="collapsed", key="session_search")

    filtered_outputs = [o for o in all_outputs if search_keyword.lower() in o['name'].lower()] if search_keyword else all_outputs

    if not filtered_outputs:
        st.warning("没有找到匹配的产物")
        return

    file_options = {f"{o['name']} ({o['session']})": o for o in filtered_outputs}
    selected_option = st.selectbox("选择产物", options=list(file_options.keys()), label_visibility="collapsed")

    if selected_option:
        selected_file = file_options[selected_option]
        content = read_md_file(selected_file['path'])
        st.markdown("<hr style='margin: 1.5rem 0; border-color: #f3f4f6;'>", unsafe_allow_html=True)
        st.markdown(f"**来源会话:** `{selected_file['session']}`")
        st.markdown(content)


def render_main_area():
    """渲染主展示区域"""
    tab1, tab2, tab3 = st.tabs(["对话 (Chat)", "知识库 (Knowledge)", "产物 (Outputs)"])

    with tab1:
        render_chat()
    with tab2:
        render_notes_browser()
    with tab3:
        render_sessions_browser()


# ==================== 4. 主程序 ====================
def main():
    inject_manus_minimalist_css()
    render_sidebar()
    render_main_area()


if __name__ == "__main__":
    main()
