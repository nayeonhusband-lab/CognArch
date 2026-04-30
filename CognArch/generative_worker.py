import os
import json
import re
import time
import yaml
import random
import hashlib
import concurrent.futures
from openai import OpenAI
from base_worker import BaseWorker

# ================= ⚙️ 配置从 config.py 导入 =================

from config import get_target_language
from config import get_api_key, get_base_url, get_reduce_model_name
from config import MAX_LENGTH, CHUNK_SIZE, OVERLAP, MAX_WORKERS  # 用于 Map-Reduce 分箱和并发
from utils import resolve_path  # 导入路径解析工具

# ================= 🔧 组件导入 =================

try:
    from components import page_anchor_parser
except ImportError:
    # Fallback if components not available
    page_anchor_parser = None


def get_openai_client():
    """动态获取 OpenAI client"""
    return OpenAI(api_key=get_api_key(), base_url=get_base_url())


client = get_openai_client()

def get_bundled_skills_path(skills_type):
    """获取打包后的技能目录路径"""
    import sys
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
        return os.path.join(base_path, 'skills', skills_type)
    return None

# ================= 🧠 核心架构：技能与知识路由层 =================

class GenerativeWorker(BaseWorker):
    # 🌟 修改点 1：接收厂长的全局路径地图
    def __init__(self, system_paths: dict, msg_queue=None):
        super().__init__(
            name="GenerativeWorker",
            description="学术研究员。擅长基于知识库进行跨文档检索，从而生成文献综述(Lit Review)、生成论文大纲、Powerpoint等任何生成性任务。因此，任何基于已有知识库，进行阅读笔记以外的生成任务，都要交给我。"
        )
        self.paths = system_paths # ⬅️ 厂长给的地图
        self.msg_queue = msg_queue  # GUI消息队列
        self.skills = {}
        self._active_retrieval_doc_cap = 50
        self._active_manual_preselected = False
        self._load_skills()

    def send_gui_msg(self, msg: str, tag: str = 'info'):
        """发送消息到GUI日志"""
        if self.msg_queue:
            try:
                self.msg_queue.put((msg, tag))
            except:
                pass

    def _load_skills(self):
        # 1. 精准锁定全局地图中的生成类目录
        skills_dir = self.paths.get("skills_generative", "skills/generative")

        # 2. 先尝试外部技能目录
        md_files = []
        if os.path.exists(skills_dir):
            md_files = [f for f in os.listdir(skills_dir) if f.endswith('.md')]

        # 如果外部目录为空，尝试从打包的技能加载
        if not md_files:
            bundled_dir = get_bundled_skills_path("generative")
            if bundled_dir and os.path.exists(bundled_dir):
                md_files = [f for f in os.listdir(bundled_dir) if f.endswith('.md')]
                if md_files:
                    skills_dir = bundled_dir
                    print(f"🎨 外部技能目录为空，从内置技能加载...")
        else:
            # 确保目录存在
            os.makedirs(skills_dir, exist_ok=True)

        # 3. 懒加载：只加载 metadata，不加载完整 prompt 内容
        print(f"🎨 正在加载生成类技能库 ({len(md_files)} 个技能)...")

        for filename in md_files:
            file_path = os.path.join(skills_dir, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                parts = content.split('---')
                if len(parts) >= 3:
                    meta_yaml = parts[1].strip()
                    meta_data = yaml.safe_load(meta_yaml)

                    # 懒加载：只存储 metadata 和文件路径，不加载完整 prompt
                    if meta_data:
                        skill_id = meta_data.get('name', filename)
                        skill_desc = meta_data.get('description', '无描述')
                        self.skills[skill_id] = {
                            "meta": meta_data,
                            "file_path": file_path,
                            "description": skill_desc
                        }
                        # 大声汇报！
                        print(f"   -> 已加载生成技能: {filename}")
            except Exception as e:
                print(f"   ❌ 加载技能失败 {filename}: {e}")

    def _load_skill_content(self, skill_id):
        """懒加载：按需加载技能的完整 prompt 内容"""
        if skill_id not in self.skills:
            return ""

        skill = self.skills[skill_id]

        # 如果已经有 raw_prompt，直接返回
        if 'raw_prompt' in skill:
            return skill['raw_prompt']

        # 否则从文件加载
        file_path = skill.get('file_path', '')
        if not file_path or not os.path.exists(file_path):
            return ""

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            parts = content.split('---')
            if len(parts) >= 3:
                raw_prompt = parts[2].strip()
                # 缓存内容
                skill['raw_prompt'] = raw_prompt
                return raw_prompt
            return content
        except Exception as e:
            print(f"   ⚠️ 加载技能内容失败 {skill_id}: {e}")
            return ""

# 🌟 新增：GenerativeWorker 的专属自我决策引擎
    def identify_best_skill(self, user_instruction: str) -> dict: # ⬅️ 注意这里返回值改成了 dict
        print("   ↳ 🧠 正在思考并匹配最佳生成技能...", end="", flush=True)
        
        menu = ""
        for skill_id, skill_data in self.skills.items():
            # 优先从 description 字段获取（旧格式），其次从 meta 中获取
            desc = skill_data.get('description') or skill_data['meta'].get('description', '无描述')
            menu += f"- Skill ID: '{skill_id}'\n  Function: {desc}\n\n"

        prompt = f"""You are a Skill Router for a Generative AI Agent.
        Your task is to select the BEST Skill to execute the user's request.

        === AVAILABLE SKILLS ===
        {menu}
        
        === USER INSTRUCTION ===
        {user_instruction}

        === INSTRUCTION ===
        Analyze the user's intent and match it against the descriptions of the available skills.
        You MUST output your response strictly as a JSON object. Do NOT output any markdown blocks or other text.
        Format requirement:
        {{
            "thought_process": "Explain step-by-step why you chose this skill and why you rejected others.",
            "skill_id": "The exact Skill ID you selected."
        }}
        """
        
        fallback_skill = list(self.skills.keys())[0]
        try:
            res = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}, # ⬅️ 强制 JSON 输出
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max"
            )
            result_dict = json.loads(res.choices[0].message.content)
            skill_id = result_dict.get("skill_id", fallback_skill)
            
            # 校验 ID
            if skill_id in self.skills:
                print(f" 🎯 匹配成功: {skill_id}")
                return result_dict
            else:
                print(f" ⚠️ 匹配结果无效，使用默认技能: {fallback_skill}")
                return {"thought_process": "大模型返回了不存在的ID", "skill_id": fallback_skill}
                
        except Exception as e:
            print(f" ❌ 思考失败({e})，使用默认技能: {fallback_skill}")
            return {"thought_process": f"API调用或解析失败: {str(e)}", "skill_id": fallback_skill}

    # 🌟 修改点 2：极速检索图书馆索引 + 强制读取黑板上的新书
    # 🌟 双语关键词检索：同时提取中英文关键词
    def _extract_keywords(self, user_instruction: str) -> list:
        """智能拆解用户指令为多个检索关键词（双语）"""
        if not user_instruction or user_instruction.lower() == 'all':
            return ['all']

        # 如果指令很短，直接返回
        if len(user_instruction) < 20:
            return [user_instruction]

        # 🌟 修改：同时提取中英文关键词，确保检索时两种语言都能命中
        prompt = f"""从以下用户指令中提取检索关键词，需要同时提供中英文翻译。
要求：
1. 每个概念同时提供中文和英文关键词
2. 共提取 3-5 个核心概念
3. 返回格式：{{"关键词概念": ["中文", "English"]}}
4. 每个概念用中英文表示，确保检索时两种语言都能命中

用户指令：{user_instruction}

返回格式示例：
{{
  "人工智能": ["人工智能", "AI"],
  "大语言模型": ["大语言模型", "LLM"],
  "亚马逊": ["亚马逊", "Amazon"]
}}"""

        try:
            res = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max"
            )
            import json
            keywords = json.loads(res.choices[0].message.content)
            if isinstance(keywords, dict):
                # 🌟 新格式：{{"概念": ["中文", "English"]}}
                MAX_KEYWORDS = 5
                items = list(keywords.items())
                if len(items) > MAX_KEYWORDS:
                    items = items[:MAX_KEYWORDS]
                # 转换为列表格式保持兼容
                keywords = [{k: v} for k, v in items]
                msg = f"   🔑 [双语关键词拆解] {keywords}"
                print(msg)
                self.send_gui_msg(msg, 'info')
                return keywords
            elif isinstance(keywords, list):
                # 🌟 兼容旧格式：["kw1", "kw2"]
                MAX_KEYWORDS = 5
                if len(keywords) > MAX_KEYWORDS:
                    keywords = keywords[:MAX_KEYWORDS]
                msg = f"   🔑 [关键词拆解] {keywords}"
                print(msg)
                self.send_gui_msg(msg, 'info')
                return keywords
        except Exception as e:
            print(f"   ⚠️ 关键词拆解失败: {e}")

        # 失败时回退到原句
        return [user_instruction]

    def _normalize_retrieval_doc_cap(self, value) -> int:
        """Normalize retrieval cap into a safe integer range."""
        default_cap = 50
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return default_cap
        return max(1, min(normalized, 500))

    def _clamp_relevance_score(self, value, default: int = 0) -> int:
        try:
            numeric = int(round(float(value)))
        except (TypeError, ValueError):
            numeric = default
        return max(0, min(numeric, 100))

    def _default_relevance_score_for_status(self, status: str) -> int:
        normalized = (status or "").upper()
        if normalized == "FOUND":
            return 75
        if normalized == "NEED_DEEP_SCAN":
            return 45
        if normalized == "IRRELEVANT":
            return 0
        return 60

    def _coerce_similarity_score(self, value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _infer_doc_relevance_score(self, doc: dict, default: int = 60) -> int:
        if isinstance(doc, dict) and 'relevance_score' in doc:
            return self._clamp_relevance_score(doc.get('relevance_score'), default=default)
        return default

    def _apply_retrieval_doc_cap(self, docs: list, label: str, cap=None, bypass=None) -> list:
        """Apply post-filter cap to auto-retrieved docs while preserving ranked order."""
        if not docs:
            return docs
        if bypass is None:
            bypass = self._active_manual_preselected
        if bypass:
            return docs

        normalized_cap = self._normalize_retrieval_doc_cap(
            self._active_retrieval_doc_cap if cap is None else cap
        )
        if len(docs) <= normalized_cap:
            return docs

        ranked = sorted(
            enumerate(docs),
            key=lambda item: (
                -self._infer_doc_relevance_score(item[1]),
                -self._coerce_similarity_score(item[1].get('score') if isinstance(item[1], dict) else 0),
                item[0]
            )
        )
        truncated = [doc for _, doc in ranked[:normalized_cap]]
        print(
            f"   📊 [Retrieval Cap] {label}: "
            f"保留 {len(truncated)}/{len(docs)} 篇（cap={normalized_cap}，按 relevance_score > score 排序）"
        )
        return truncated

    def _apply_meso_doc_cap(self, search_results: list, anchor_map: dict, cap=None, bypass=None) -> dict:
        """Apply post-meso cap to FOUND/NEED_DEEP_SCAN docs only."""
        if not anchor_map:
            return anchor_map
        if bypass is None:
            bypass = self._active_manual_preselected
        if bypass:
            return anchor_map

        normalized_cap = self._normalize_retrieval_doc_cap(
            self._active_retrieval_doc_cap if cap is None else cap
        )

        search_meta = {
            doc.get('filepath'): {
                'score': self._coerce_similarity_score(doc.get('score')),
                'order': idx
            }
            for idx, doc in enumerate(search_results or [])
            if isinstance(doc, dict) and doc.get('filepath')
        }

        retained_records = []
        for filepath, result in anchor_map.items():
            status = (result.get('status') or 'IRRELEVANT').upper()
            if status not in {'FOUND', 'NEED_DEEP_SCAN'}:
                continue
            meta = search_meta.get(filepath, {})
            retained_records.append({
                'filepath': filepath,
                'status': status,
                'priority': 0 if status == 'FOUND' else 1,
                'relevance_score': self._clamp_relevance_score(
                    result.get('relevance_score'),
                    default=self._default_relevance_score_for_status(status)
                ),
                'score': meta.get('score', 0.0),
                'order': meta.get('order', result.get('doc_index', 10**9)),
            })

        if len(retained_records) <= normalized_cap:
            return anchor_map

        retained_records.sort(
            key=lambda item: (
                item['priority'],
                -item['relevance_score'],
                -item['score'],
                item['order']
            )
        )
        kept_paths = {item['filepath'] for item in retained_records[:normalized_cap]}

        trimmed_map = {}
        for doc in (search_results or []):
            filepath = doc.get('filepath') if isinstance(doc, dict) else None
            if not filepath or filepath not in anchor_map:
                continue
            result = dict(anchor_map[filepath])
            status = (result.get('status') or 'IRRELEVANT').upper()
            if status in {'FOUND', 'NEED_DEEP_SCAN'} and filepath not in kept_paths:
                result['status'] = 'IRRELEVANT'
                result.pop('anchors', None)
                result.pop('suspected_range', None)
                result['dropped_by_cap'] = True
            trimmed_map[filepath] = result

        for filepath, result in anchor_map.items():
            if filepath not in trimmed_map:
                trimmed_map[filepath] = result

        print(
            f"   📊 [Meso Cap] 保留 {len(kept_paths)}/{len(retained_records)} 篇 "
            f"FOUND/NEED_DEEP_SCAN 文献（cap={normalized_cap}）"
        )
        return trimmed_map

    def _format_numeric_positions(self, positions: list) -> str:
        if not positions:
            return ""
        sorted_positions = sorted(set(int(p) for p in positions))
        ranges = []
        start = sorted_positions[0]
        prev = start
        for pos in sorted_positions[1:]:
            if pos == prev + 1:
                prev = pos
                continue
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = pos
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        return ", ".join(ranges)

    def _build_anchor_from_parsed(self, parsed: dict, positions: list) -> str:
        anchor_type = (parsed.get('type') or 'SOURCE').upper()
        if parsed.get('is_global'):
            return f"[[{anchor_type}: Global]]"

        spec = self._format_numeric_positions(positions or parsed.get('pages', []))
        if not spec:
            return ""

        if anchor_type == 'DOCX' and parsed.get('is_paragraph'):
            return f"[[DOCX: Para {spec}]]"
        if anchor_type == 'EXCEL':
            sheet = parsed.get('sheet')
            cell_range = parsed.get('range')
            if sheet and cell_range:
                return f"[[EXCEL: {sheet}|{cell_range}]]"
            if sheet:
                return f"[[EXCEL: {sheet}]]"
            return ""
        return f"[[{anchor_type}: {spec}]]"

    def _merge_numeric_anchor_entries(self, entries: list) -> list:
        merged_groups = []
        for entry in entries:
            pages = sorted(set(entry.get('pages', [])))
            merged = False
            for group in merged_groups:
                existing_pages = group['page_set']
                if any((page in existing_pages) or (page - 1 in existing_pages) or (page + 1 in existing_pages) for page in pages):
                    existing_pages.update(pages)
                    group['members'].append(entry)
                    merged = True
                    break
            if not merged:
                merged_groups.append({
                    'page_set': set(pages),
                    'members': [entry],
                    'parsed': entry['parsed']
                })

        merged_items = []
        for group in merged_groups:
            positions = sorted(group['page_set'])
            anchor = self._build_anchor_from_parsed(group['parsed'], positions)
            fallback_anchor = group['members'][0]['anchor']
            merged_items.append({
                'anchor': anchor or fallback_anchor,
                'fallback_anchor': fallback_anchor,
                'members': [member['anchor'] for member in group['members']]
            })
        return merged_items

    def _prepare_doc_anchors_for_extraction(self, anchors: list) -> list:
        """Deduplicate anchors and merge overlapping numeric ranges before extraction."""
        if not anchors:
            return []

        exact_seen = set()
        numeric_groups = {}
        passthrough = []

        for raw_anchor in anchors:
            anchor = (raw_anchor or "").strip()
            if not anchor or anchor in exact_seen:
                continue
            exact_seen.add(anchor)

            parsed = page_anchor_parser.parse_anchor(anchor) if page_anchor_parser else {'type': 'UNKNOWN', 'pages': [], 'is_global': False}
            anchor_type = (parsed.get('type') or 'UNKNOWN').upper()
            pages = parsed.get('pages') or []
            is_mergeable = (
                anchor_type in {'PDF', 'DOCX', 'PPTX', 'MD', 'HTML', 'WEB', 'SOURCE'}
                and not parsed.get('is_global')
                and bool(pages)
                and not parsed.get('quote_text')
                and not parsed.get('sheet')
            )

            if is_mergeable:
                key = (anchor_type, bool(parsed.get('is_paragraph')))
                numeric_groups.setdefault(key, []).append({
                    'anchor': anchor,
                    'parsed': parsed,
                    'pages': sorted(set(int(p) for p in pages))
                })
            else:
                passthrough.append({
                    'anchor': anchor,
                    'fallback_anchor': anchor,
                    'members': [anchor]
                })

        merged = []
        for entries in numeric_groups.values():
            merged.extend(self._merge_numeric_anchor_entries(entries))

        return passthrough + merged

    def _normalized_text_hash(self, text: str) -> str:
        normalized = re.sub(r'\s+', ' ', (text or '').strip()).lower()
        return hashlib.md5(normalized.encode('utf-8')).hexdigest() if normalized else ""

    def _filter_relevant_docs(self, docs: list, user_instruction: str) -> list:
        """两阶段筛选：用 Chat 模型快速过滤不相关文献

        用于 Literature Review 场景，类似于 Deep Retrieval 的 Step 2
        """
        if not docs:
            return []

        filtered_docs = []

        filter_prompt = """你是一个文献精筛助手。你的任务是判断每篇文献是否能为用户研究问题或当前写作阶段提供实质论证贡献。

用户研究问题：
{user_question}

文献列表（每篇包含可供判断的笔记内容）：
{docs}

请分析每篇文献，判断其是否真正支撑当前问题/阶段，而不是只共享泛泛主题词或停留在同域背景层面。

输出格式（严格 JSON）：
{{
  "relevant": [
    {"id": 1, "relevance_score": 92},
    {"id": 3, "relevance_score": 81}
  ],
  "irrelevant": [2,4]
}}

注意：
- 只有能为当前问题/阶段提供实质贡献的文献才标记为 relevant
- 允许的相关逻辑包括：包含关系、概念相交、支持关系、反对/批判关系、方法/框架贡献、有限相邻领域贡献
- 同属一个大领域并不自动 relevant；必须对当前论证提供直接支撑、可迁移机制、分析框架、治理工具、反例或方法
- 相邻领域文献只有在提供可迁移机制、分析框架、治理工具、反例或方法时才 relevant；如果只是同属一个大领域或共享热点词，应标记为 irrelevant
- 仅共享宽泛领域词、只提供远程背景、或需要大幅延伸才相关的文献应标记为 irrelevant
- 如果这是按大纲/章节/阶段检索，只有支撑该具体阶段目的的文献才 relevant；全局相关但不支撑本阶段的文献应标记为 irrelevant
- 为每篇 relevant 文献提供 0-100 的 relevance_score：
  90-100 = 可直接支撑当前问题/阶段的核心证据
  70-89 = 强相关，能明显推进论证
  40-69 = 有限但真实的论证贡献
- 不确定时倾向于标记为 irrelevant（保守筛选）
- 只需要输出 JSON，不要有其他文字

抽象示例：
- 用户问“如何治理 X 风险”，文献只介绍 X 的市场规模或产业趋势，没有风险机制或治理工具 → irrelevant
- 用户问“如何治理 X 风险”，文献提供 X 的风险分类、监管框架或问责机制 → relevant
- 用户问“A 场景中的 X 问题”，文献来自 B 场景但提供可迁移的评估框架或反面案例 → relevant
- 用户问“A 场景中的 X 问题”，文献来自 B 场景且只共享同一上位领域词，没有可迁移机制 → irrelevant
"""

        # 🌟 双重阈值动态分批（基于 DeepSeek-V4 1M 上下文窗口）
        MAX_DOCS_PER_BATCH = 30
        MAX_CHARS_PER_BATCH = 800000

        print(f"   📊 [LLM筛选] 双阈值: ≤{MAX_DOCS_PER_BATCH}篇/批, ≤{MAX_CHARS_PER_BATCH}字符/批")

        # 兼容新旧格式：如果是字典，提取 content
        def get_doc_content(doc):
            if isinstance(doc, dict):
                return doc.get('content', str(doc))
            return doc

        current_batch = []
        current_chars = 0
        batch_num = 0

        for doc in docs:
            doc_content = get_doc_content(doc)
            # 🌟 关键修改：传入完整笔记内容，不截断！
            doc_len = len(doc_content)

            # 触发封箱条件：篇数超限 或 字符数超限（且当前箱非空）
            if current_batch and (len(current_batch) >= MAX_DOCS_PER_BATCH or (current_chars + doc_len) > MAX_CHARS_PER_BATCH):
                # 封箱，发送给 LLM
                batch_num += 1
                print(f"   🔄 [LLM筛选] 第 {batch_num} 批 ({len(current_batch)} 篇, {current_chars} 字符)...")

                filtered_batch = self._process_filter_batch(current_batch, filter_prompt, user_instruction, batch_num)
                filtered_docs.extend(filtered_batch)
                # 清箱
                current_batch = []
                current_chars = 0

            # 装入当前文档
            current_batch.append(doc)
            current_chars += doc_len

        # 处理尾盘
        if current_batch:
            batch_num += 1
            print(f"   🔄 [LLM筛选] 第 {batch_num} 批 ({len(current_batch)} 篇, {current_chars} 字符)...")
            filtered_batch = self._process_filter_batch(current_batch, filter_prompt, user_instruction, batch_num)
            filtered_docs.extend(filtered_batch)

        filtered_docs = self._apply_retrieval_doc_cap(filtered_docs, label="Relevant Filtering")
        return filtered_docs

    def _process_filter_batch(self, batch: list, filter_prompt: str, user_instruction: str, batch_num: int) -> list:
        """处理单批次文献筛选"""
        filtered = []

        def render_filter_prompt(template: str, replacements: dict) -> str:
            """Replace only known placeholders and leave JSON braces untouched."""
            rendered = template
            sentinel_map = {}
            for key, value in replacements.items():
                sentinel = f"__CODEX_FILTER_{key.upper()}__"
                rendered = rendered.replace(f"{{{key}}}", sentinel)
                sentinel_map[sentinel] = str(value)
            for sentinel, value in sentinel_map.items():
                rendered = rendered.replace(sentinel, value)
            return rendered

        # 兼容新旧格式：提取 doc content
        def get_doc_content(doc):
            if isinstance(doc, dict):
                return doc.get('content', str(doc))
            return doc

        # 构建文档列表（带编号）
        docs_with_numbers = []
        for idx, doc in enumerate(batch):
            # 🌟 传入完整笔记内容，不截断
            doc_content = get_doc_content(doc)
            docs_with_numbers.append(f"[{idx+1}] {doc_content}")

        docs_text = "\n\n".join(docs_with_numbers)

        try:
            rendered_filter_prompt = render_filter_prompt(
                filter_prompt,
                {
                    "user_question": user_instruction,
                    "docs": docs_text,
                }
            )
        except Exception as e:
            print(f"   ⚠️ [LLM筛选] Prompt render failed: {e}，保留该批全部文献")
            filtered.extend(batch)
            return filtered

        # 调用 Chat 模型进行筛选
        messages = [
            {"role": "system", "content": "你是一个严谨的文献筛选助手，只输出 JSON 格式结果。"},
            {"role": "user", "content": rendered_filter_prompt}
        ]

        try:
            response = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=messages,
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max",
                timeout=60
            )
            result = response.choices[0].message.content.strip()

            # 解析 JSON 结果
            import json
            import re

            # 提取 JSON
            json_match = re.search(r'\{[\s\S]*\}', result)
            if json_match:
                try:
                    result_json = json.loads(json_match.group())
                    raw_relevant = result_json.get('relevant', '')

                    relevant_items = []
                    if isinstance(raw_relevant, list):
                        relevant_items = raw_relevant
                    elif raw_relevant:
                        relevant_items = [x.strip() for x in str(raw_relevant).split(',') if x.strip()]

                    if relevant_items:
                        parsed_items = []
                        for item in relevant_items:
                            if isinstance(item, dict):
                                raw_id = item.get('id', item.get('doc_id', item.get('index')))
                                if str(raw_id).isdigit():
                                    parsed_items.append({
                                        'id': int(raw_id),
                                        'relevance_score': self._clamp_relevance_score(
                                            item.get('relevance_score'),
                                            default=60
                                        )
                                    })
                            elif str(item).strip().isdigit():
                                parsed_items.append({
                                    'id': int(str(item).strip()),
                                    'relevance_score': 60
                                })

                        for item in parsed_items:
                            num = item['id']
                            if 0 <= num-1 < len(batch):
                                doc = batch[num-1]
                                if isinstance(doc, dict):
                                    doc = dict(doc)
                                    doc['relevance_score'] = self._clamp_relevance_score(
                                        item.get('relevance_score'),
                                        default=self._infer_doc_relevance_score(doc, default=60)
                                    )
                                filtered.append(doc)

                        print(f"   ✅ [LLM筛选] 第 {batch_num} 批: {len(parsed_items)}/{len(batch)} 篇相关")
                except json.JSONDecodeError as je:
                    # 解析失败，保留所有文献
                    print(f"   ⚠️ [LLM筛选] JSON 解析失败，保留该批全部文献")
                    filtered.extend(batch)
            else:
                # 无法解析，保留所有文献
                print(f"   ⚠️ [LLM筛选] 无法解析结果，保留该批全部文献")
                filtered.extend(batch)

        except Exception as e:
            print(f"   ⚠️ [LLM筛选] 出错: {e}，保留该批全部文献")
            filtered.extend(batch)

        return filtered

    def _search_global_kb(self, keywords: str, shared_workspace: dict) -> list:
        msg = f"🔍 [系统] 正在查阅知识库..."
        print(msg)
        self.send_gui_msg(msg, 'info')

        docs = []
        loaded_paths = set()

        # 🛡️ 1. 绝对强制：先拿走黑板上刚刚生成的新笔记 (防止没命中关键词而漏掉)
        new_notes = shared_workspace.get("current_session_new_notes", [])
        for note_path in new_notes:
            try:
                with open(note_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 提取 doc_id（从笔记文件名或内容中）
                    doc_id = os.path.basename(note_path).replace('.md', '')
                    docs.append({
                        'content': content,
                        'doc_id': doc_id,
                        'filepath': note_path,
                        'title': doc_id
                    })
                    loaded_paths.add(note_path)
            except Exception as e:
                print(f"   ⚠️ 无法读取新笔记 {note_path}: {e}")

        if new_notes:
            msg = f"   📎 已强制提取本次专案刚录入的新文献: {len(new_notes)} 篇"
            print(msg)
            self.send_gui_msg(msg, 'info')

        # 🛡️ 2. 智能拆解关键词（双语）
        keyword_list = self._extract_keywords(keywords)

        # 🌟 展平关键词列表：每个概念的中英文版本都要检索
        expanded_keywords = []
        display_keywords = []
        for kw_item in keyword_list:
            if isinstance(kw_item, dict):
                # 新格式：{"概念": ["中文", "English"]}
                for concept, translations in kw_item.items():
                    if isinstance(translations, list):
                        expanded_keywords.extend(translations)
                        display_keywords.append(f"{concept}: {translations}")
                    else:
                        expanded_keywords.append(translations)
                        display_keywords.append(translations)
            elif isinstance(kw_item, list):
                # 兼容旧格式
                expanded_keywords.extend(kw_item)
                display_keywords.extend(kw_item)
            else:
                expanded_keywords.append(kw_item)
                display_keywords.append(kw_item)

        all_keywords_str = " OR ".join(expanded_keywords)
        msg = f"   🔑 [双语检索式] {display_keywords}"
        print(msg)
        self.send_gui_msg(msg, 'info')

        # 🛡️ 3. 语义搜索：对每个关键词分别搜索，取并集
        if keywords.lower() != 'all':
            try:
                import vector_store
                all_results = {}  # {filepath: {'item': ..., 'score': ...}}

                for kw in expanded_keywords:
                    search_results = vector_store.search(kw, threshold=0.3)
                    for result in search_results:
                        item = result["item"]
                        filepath = item.get("filepath")
                        # 将相对路径解析为绝对路径
                        filepath = resolve_path(filepath) if filepath else None
                        score = result["score"]

                        if not filepath or filepath in loaded_paths:
                            continue

                        # 取最高分
                        if filepath not in all_results or score > all_results[filepath]['score']:
                            all_results[filepath] = result

                search_results = list(all_results.values())
                search_results.sort(key=lambda x: x["score"], reverse=True)

                if search_results:
                    print(f"   🧠 [语义匹配] 找到 {len(search_results)} 篇相关文档")

                    for result in search_results:
                        item = result["item"]
                        filepath = item.get("filepath")
                        # 将相对路径解析为绝对路径
                        filepath = resolve_path(filepath) if filepath else None
                        score = result["score"]

                        if not filepath or filepath in loaded_paths:
                            continue

                        try:
                            with open(filepath, 'r', encoding='utf-8') as f:
                                content = f.read()
                                # 提取 doc_id
                                doc_id = item.get('doc_id', os.path.basename(filepath).replace('.md', ''))
                                docs.append({
                                    'content': content,
                                    'doc_id': doc_id,
                                    'filepath': filepath,
                                    'title': item.get('title', os.path.basename(filepath))
                                })
                                loaded_paths.add(filepath)
                                msg = f"      ✓ 匹配: {item.get('title', 'N/A')} (相似度: {score:.2f})"
                                print(msg)
                                self.send_gui_msg(msg, 'info')
                        except Exception:
                            pass

                    if docs:
                        msg = f"   ✅ [语义搜索] 共提取 {len(docs)} 篇文档"
                        print(msg)
                        self.send_gui_msg(msg, 'success')
                        return docs
            except ImportError:
                msg = "   ℹ️ 向量库未安装，回退到关键词匹配..."
                print(msg)
                self.send_gui_msg(msg, 'info')
            except Exception as e:
                msg = f"   ⚠️ 语义搜索失败: {e}，回退到关键词匹配..."
                print(msg)
                self.send_gui_msg(msg, 'warning')

        # 🛡️ 3. 关键词匹配（后备方案）：使用拆解后的关键词，OR匹配
        base_dir = os.path.dirname(self.paths["kb_notes"])
        index_path = os.path.join(base_dir, "index.json")

        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)

                # 使用拆解后的关键词列表（已经是小写）
                search_keywords = [k.lower() for k in keyword_list if k.lower() != 'all']

                for item in index_data:
                    filepath = item.get("filepath")
                    # 将相对路径解析为绝对路径
                    filepath = resolve_path(filepath) if filepath else None
                    if not filepath or filepath in loaded_paths:
                        continue

                    item_str_lower = str(item).lower()

                    is_match = False
                    if 'all' in search_keywords or not search_keywords:
                        is_match = True
                    else:
                        # OR逻辑：命中任意一个关键词即可
                        if any(k in item_str_lower for k in search_keywords):
                            is_match = True

                    if is_match:
                        try:
                            with open(filepath, 'r', encoding='utf-8') as f:
                                content = f.read()
                                # 提取 doc_id
                                doc_id = item.get('doc_id', os.path.basename(filepath).replace('.md', ''))
                                docs.append({
                                    'content': content,
                                    'doc_id': doc_id,
                                    'filepath': filepath,
                                    'title': item.get('title', os.path.basename(filepath))
                                })
                                loaded_paths.add(filepath)
                        except Exception:
                            pass
            except Exception as e:
                print(f"   ⚠️ 读取 index.json 失败: {e}")
        else:
            print("   ⚠️ 尚未建立 index.json，仅使用本次新传入的文献。")

        return docs

    def _get_exact_files(self, target_filenames: list) -> list:
        # 原封不动保留你的精确读取功能，仅修正底层寻找目录
        docs = []
        target_dir = self.paths["kb_notes"]
        for filename in target_filenames:
            if not filename.endswith('.md'):
                filename += '.md'
            file_path = os.path.join(target_dir, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 提取 doc_id
                    doc_id = filename.replace('.md', '')
                    docs.append({
                        'content': content,
                        'doc_id': doc_id,
                        'filepath': file_path,
                        'title': doc_id
                    })
            except Exception as e:
                print(f"   ⚠️ 找不到精确指定的文件 {filename}: {e}")
        return docs

    # ================= 🚀 核心生成逻辑 (保持原有双引擎架构绝对不变) =================

    # 🌟 修改点 3：中间态备份写入专案车间 (session_dir) + 动态智能装箱
    def _engine_iterative_snowball(self, docs: list, user_instruction: str, system_prompt: str, session_dir: str, task_type: str = "内容生成") -> str:
        """通用多轮迭代引擎，处理海量文档的批量生成任务"""

        # 1. 嗅探 JSON Metadata 制作全局概览
        json_metadata_collection = []
        for content in docs:
            json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if json_match:
                try:
                    meta_dict = json.loads(json_match.group(1))
                    brief_meta = {k: v for k, v in meta_dict.items() if k not in ['authors', 'publication_info']}
                    json_metadata_collection.append(str(brief_meta))
                except:
                    pass

        print("\n🦅 [Phase 1] 正在构建分析框架...")
        # 使用更通用的框架提示，根据任务类型调整
        task_context = f"这是一个{task_type}任务" if task_type else "内容生成任务"
        meta_prompt = f"基于以下文档的元数据，为{task_context}构建分析框架。\n\n任务目标：{user_instruction}\n\n元数据：\n{chr(10).join(json_metadata_collection[:50])}"  # 限制元数据数量
        try:
            res_meta = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": meta_prompt}],
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max"
            )
            global_overview = res_meta.choices[0].message.content
        except Exception:
            global_overview = "按通用方式进行结构化处理。"

        # ==========================================
        # 🌟 核心升级：基于 Token/字符容量的动态智能装箱
        # ==========================================
        SAFE_CHAR_LIMIT = 1300000 # 单批次字符上限（与 CHUNK_SIZE 对齐，基于 DeepSeek-V4 1M 上下文窗口）
        batches = []
        current_batch = []
        current_batch_len = 0
        
        for doc in docs:
            doc_len = len(doc)
            # 1. 极端防爆机制：如果单篇笔记巨大无比，且当前批次已空，强行单独成批
            if doc_len > SAFE_CHAR_LIMIT and not current_batch:
                batches.append([doc])
                continue
                
            # 2. 正常装箱判定：如果加入这篇文档会撑爆箱子，则把当前箱子打包发车，换新箱子
            if current_batch_len + doc_len > SAFE_CHAR_LIMIT and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_batch_len = 0
                
            # 3. 塞入当前箱子
            current_batch.append(doc)
            current_batch_len += doc_len
            
        # 把最后没装满的一箱也收尾发车
        if current_batch:
            batches.append(current_batch)
            
        print(f"   📏 [智能装箱] 根据 128K 上下文安全容量，{len(docs)} 篇文献被动态重组为 {len(batches)} 个批次。")

        # ==========================================
        # 🌟 原有滚动融合逻辑 (100% 保留，完美对接你的防偷懒 Skill 铁律)
        # ==========================================
        current_draft = "" 
        
        # ⬇️ 写入专案目录，不再弄脏系统根目录
        intermediate_dir = os.path.join(session_dir, "intermediate_drafts")
        os.makedirs(intermediate_dir, exist_ok=True)
        
        print(f"\n⚙️ [Phase 2] 启动批量处理！共 {len(batches)} 个批次。")
        for idx, batch_texts in enumerate(batches):
            print(f"   🔄 正在处理第 {idx+1}/{len(batches)} 批文档...")

            # 移除所有暴露处理过程的表达
            batch_combined = "\n\n=== 待处理的文档 ===\n\n".join(batch_texts)

            # 中性化指令，根据任务类型和批次动态生成
            if idx == 0:
                action_instruction = f"请基于以下文档和全局分析框架，完成{task_type}任务，输出完整内容。"
            else:
                action_instruction = f"请将以下新增文档的内容整合进你的分析中，生成完整、统一的结果。"

            # 修复：提前准备好已有内容的字符串，避免在 f-string 大括号中使用反斜杠
            existing_content_str = f"【已有内容（请整合进最终输出）】：\n{current_draft}" if current_draft else ""

            iterative_prompt = f"""
            【任务类型】：{task_type}
            【用户指令】：{user_instruction}
            【全局分析框架】：\n{global_overview}

            {existing_content_str}

            【待处理文档】：\n{batch_combined}

            ⚠️ 重要：输出必须是一份完整的、独立的内容，不要提及"批次"、"迭代"、"版本"、"初稿"等处理过程信息。
            """
            
            try:
                res_iter = client.chat.completions.create(
                    model="deepseek-v4-flash",
                    messages=[
                        {"role": "system", "content": f"严格遵循以下技能架构指令：\n\n{system_prompt}"},
                        {"role": "user", "content": iterative_prompt}
                    ],
                    extra_body={"thinking": {"type": "enabled"}},
                    reasoning_effort="max"
                )
                current_draft = res_iter.choices[0].message.content

                checkpoint_path = os.path.join(intermediate_dir, f"draft_batch_{idx+1}.md")
                with open(checkpoint_path, 'w', encoding='utf-8') as f:
                    f.write(current_draft)
                    
                print(f"   ✅ 第 {idx+1} 批处理完毕！(当前内容长度: {len(current_draft)} 字符)")

            except Exception as e:
                print(f"   ❌ 第 {idx+1} 批处理发生错误: {e}")
                print(f"   ⚠️ 系统将保留已有内容并终止处理...")
                break # 或可选择不 break 继续执行，视容忍度而定

        return current_draft

    # ================= Deep Retrieval 辅助函数 =================

    def _extract_note_context(self, note_content: str, start_idx: int, anchor_len: int = 0,
                              before_chars: int = 250, after_chars: int = 350) -> str:
        """从笔记中提取锚点附近的局部上下文。"""
        if not note_content or start_idx < 0:
            return ""

        start = max(0, start_idx - before_chars)
        end = min(len(note_content), start_idx + anchor_len + after_chars)
        context = note_content[start:end].strip()
        if not context:
            return ""
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(note_content) else ""
        return f"{prefix}{context}{suffix}"

    def _build_note_anchor_variants(self, anchor: str) -> list:
        """为 note-local 查找生成一组兼容锚点变体。"""
        raw_anchor = (anchor or "").strip()
        if not raw_anchor:
            return []

        variants = [raw_anchor]

        if raw_anchor.startswith('[[') and raw_anchor.endswith(']]'):
            variants.append(raw_anchor[1:-1])
        elif raw_anchor.startswith('[[') and not raw_anchor.endswith(']]'):
            variants.append(raw_anchor.rstrip(']') + ']]')
        elif (
            raw_anchor.startswith('[')
            and not raw_anchor.startswith('[[')
            and raw_anchor.endswith(']')
            and not raw_anchor.endswith(']]')
        ):
            variants.append(f'[{raw_anchor}]')
        elif raw_anchor.startswith('[') and not raw_anchor.endswith(']'):
            variants.append(raw_anchor + ']')

        parsed = page_anchor_parser.parse_anchor(raw_anchor) if page_anchor_parser else {'type': 'UNKNOWN', 'pages': [], 'is_global': False}
        anchor_type = parsed.get('type', 'UNKNOWN')
        number_tokens = re.findall(r'(\d+(?:-\d+)?)', raw_anchor)

        if parsed.get('is_global') and anchor_type != 'UNKNOWN':
            variants.extend([
                f"[[{anchor_type}: Global]]",
                f"[{anchor_type}: Global]",
            ])

        if anchor_type != 'UNKNOWN':
            for token in number_tokens:
                variants.extend([
                    f"[[{anchor_type}: {token}]]",
                    f"[{anchor_type}: {token}]",
                ])
                if anchor_type == 'DOCX':
                    variants.extend([
                        f"[[DOCX: Para {token}]]",
                        f"[DOCX: Para {token}]",
                    ])

            if 'global' in raw_anchor.lower() and number_tokens:
                joined = ', '.join(number_tokens)
                variants.extend([
                    f"[[{anchor_type}: {joined}]]",
                    f"[{anchor_type}: {joined}]",
                    f"[[{anchor_type}: Global, {joined}]]",
                    f"[{anchor_type}: Global, {joined}]",
                    f"[[{anchor_type}: {joined}, Global]]",
                    f"[{anchor_type}: {joined}, Global]",
                ])

        deduped = []
        seen = set()
        for variant in variants:
            variant = variant.strip()
            if variant and variant not in seen:
                seen.add(variant)
                deduped.append(variant)
        return deduped

    def _extract_from_note_for_global(self, file_type: str, note_content: str) -> str:
        """
        从笔记内容中提取 Global 锚点对应的内容。
        查找 [[TYPE: Global]] 标记，获取该标记附近的内容作为摘要。
        """
        if not note_content:
            return ""

        # 查找笔记中的 Global 标记位置
        global_pattern = rf'\[{{1,2}}{re.escape(file_type)}:\s*Global\]{{1,2}}'
        match = re.search(global_pattern, note_content, re.IGNORECASE)

        if match:
            return self._extract_note_context(note_content, match.start(), len(match.group(0)), before_chars=500, after_chars=1500)

        return ""

    def _extract_from_note_for_quote(self, anchor: str, note_content: str) -> str:
        """
        从笔记内容中提取 QUOTE 锚点对应的内容。
        查找笔记中包含该 quote 的位置，获取上下文。
        """
        if not note_content:
            return ""

        # 提取引号内的文本
        quote_match = re.search(r'\[\[QUOTE:\s*"([^"]+)"\]\]', anchor, re.IGNORECASE)
        if not quote_match:
            return ""

        quote_text = quote_match.group(1)

        # 在笔记内容中查找这个文本
        idx = note_content.find(quote_text)
        if idx >= 0:
            # 提取上下文
            context_chars = 250
            start = max(0, idx - context_chars)
            end = min(len(note_content), idx + len(quote_text) + context_chars)
            context = note_content[start:end]
            return f"...{context}..."

        # 如果笔记中找不到，尝试从源文件提取
        return None

    def _extract_from_note_for_anchor(self, anchor: str, note_content: str) -> str:
        """
        从笔记内容中提取任意锚点对应的内容（通用回退函数）。
        查找笔记中包含该锚点标记的位置，获取上下文。
        """
        if not note_content or not anchor:
            return ""

        for pattern in self._build_note_anchor_variants(anchor):
            idx = note_content.find(pattern)
            if idx >= 0:
                return self._extract_note_context(note_content, idx, len(pattern))

        parsed = page_anchor_parser.parse_anchor(anchor) if page_anchor_parser else {'type': 'UNKNOWN', 'pages': [], 'is_global': False}
        anchor_type = parsed.get('type', 'UNKNOWN')
        number_tokens = re.findall(r'(\d+(?:-\d+)?)', anchor)
        if anchor_type != 'UNKNOWN' and number_tokens:
            first_token = re.escape(number_tokens[0])
            fuzzy_anchor_re = re.compile(
                rf'\[{{1,2}}{re.escape(anchor_type)}:\s*[^\]\n]*{first_token}[^\]\n]*\]{{1,2}}',
                re.IGNORECASE
            )
            fuzzy_match = fuzzy_anchor_re.search(note_content)
            if fuzzy_match:
                return self._extract_note_context(note_content, fuzzy_match.start(), len(fuzzy_match.group(0)))

        return ""

    # ================= 🔍 Coarse Filtering Layer =================

    def _load_index_map_for_coarse(self) -> dict:
        """
        加载 index.json 为 {basename: item} 字典，用于粗筛层快速查找
        """
        index_map = {}
        try:
            kb_notes = self.paths.get("kb_notes", "")
            if kb_notes:
                base_dir = os.path.dirname(os.path.dirname(kb_notes))
            else:
                base_dir = ""
            index_path = os.path.join(base_dir, "knowledge_base", "index.json") if base_dir else "knowledge_base/index.json"

            if os.path.exists(index_path):
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
                    for item in index_data:
                        filepath = item.get("filepath", "")
                        basename = os.path.basename(filepath)
                        if basename:
                            index_map[basename] = item
        except Exception as e:
            print(f"   ⚠️ [Coarse] 加载 index.json 失败: {e}")
        return index_map

    def _extract_doc_metadata_for_coarse(self, docs: list, index_map: dict) -> list:
        """
        从 docs 和 index_map 中提取粗筛所需的元数据 (doc_id + core_claim)
        具备鲁棒性：允许字段缺失、格式不规范
        """
        metadata_list = []

        for doc in docs:
            if isinstance(doc, dict):
                filepath = doc.get('filepath', '')
                basename = os.path.basename(filepath) if filepath else ''

                # 从 index_map 获取完整元数据
                if basename and basename in index_map:
                    index_item = index_map[basename]
                    doc_id = index_item.get('doc_id', basename.replace('.md', ''))
                    core_claim = index_item.get('core_claim', '')
                    title = index_item.get('title', doc_id)
                    tags = index_item.get('tags', [])
                    primary_field = index_item.get('primary_field', '')
                else:
                    # 回退：从 doc 中提取
                    doc_id = doc.get('doc_id', doc.get('title', basename.replace('.md', '')))
                    core_claim = doc.get('core_claim', '')
                    title = doc.get('title', doc_id)
                    tags = []
                    primary_field = ''

                # 如果没有 core_claim，用 title + tags + primary_field 作为回退
                if not core_claim:
                    fallback_parts = [title]
                    if primary_field:
                        fallback_parts.append(f"[{primary_field}]")
                    if tags:
                        fallback_parts.append(f"Tags: {', '.join(tags[:3])}")
                    core_claim = " | ".join(fallback_parts)

                metadata_list.append({
                    'doc_id': doc_id,
                    'core_claim': core_claim if core_claim else doc_id,  # 完整core_claim，不截断
                    'index': len(metadata_list)  # 保留原始索引用于后续映射
                })
            else:
                # 非字典格式，使用字符串表示
                metadata_list.append({
                    'doc_id': str(doc)[:50],
                    'core_claim': str(doc)[:500],
                    'index': len(metadata_list)
                })

        return metadata_list

    def _process_coarse_batch(self, batch_meta: list, user_instruction: str, batch_num: int, silent: bool = False) -> list:
        """
        处理单个粗筛批次，使用 deepseek-v4-flash 判断相关性
        返回应保留的文档索引列表（在 batch_meta 中的位置）

        Args:
            silent: 如果为 True，不输出 print（用于子线程）
        """
        # 构建 prompt
        docs_text = ""
        for i, meta in enumerate(batch_meta):
            docs_text += f"[{i+1}] Doc ID: {meta['doc_id']}\n"
            docs_text += f"    Core Claim: {meta['core_claim']}\n\n"

        prompt = f"""你是一个文献相关性初筛助手。任务：判断每篇文献是否与用户的研究问题存在可解释的初步关联。

用户研究问题：
{user_instruction}

文献列表（仅展示 Doc ID 和核心主张）：
{docs_text}

判断标准（满足任一即保留）：
1. 主题包含/被包含关系
2. 概念相交/重叠
3. 提供支持性证据
4. 提供反对/批判性观点
5. 提供与当前问题论证直接相关的背景/方法论参考
6. 存在可解释的潜在论证关联（不是纯关键词共现）

补充说明：
- 仅仅同属一个宽泛领域、共享热门术语或技术大类，并不足以保留
- 但只要存在可解释的初步论证价值，仍应保留到下一层精筛

**删除标准**（只有满足以下情况才排除）：
- 文献与用户问题**完全无关**（在不同领域、无交集、无参考价值）
- 例如：研究"人工智能伦理"时，一篇纯技术性的"钓鱼网站检测算法"论文应删除（虽然都是"计算机"大类，但无直接联系）

**重要原则**：倾向于保留而非排除（宁可错留，不要错杀）。不确定的文献请保留。

输出格式（严格 JSON）：
{{
  "retained": [保留的文献编号列表，如 [1,3,5]],
  "excluded": [排除的文献编号列表，如 [2,4]]
}}

注意：
- 只需输出 JSON，不要有其他文字
- 保留策略：宽松保留，严格删除明显无关的
"""

        try:
            response = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": "你是一个保守的文献筛选助手，只输出 JSON 格式结果。"},
                    {"role": "user", "content": prompt}
                ],
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max",
                timeout=60
            )

            result = response.choices[0].message.content.strip()
            if not silent:
                print(f"   🔍 [Coarse DEBUG] 第 {batch_num} 批 LLM 原始返回 (前500字符):\n{result[:500]}...")

            # 解析 JSON - 使用鲁棒的解析方法
            parsed = None

            # 策略1: 直接解析整个字符串（移除markdown代码块）
            try:
                result_clean = result.strip()
                if result_clean.startswith('```'):
                    lines = result_clean.split('\n')
                    start_idx = 0
                    for i, line in enumerate(lines):
                        if line.strip().startswith('```'):
                            start_idx = i + 1
                            break
                    end_idx = len(lines)
                    for i in range(len(lines) - 1, -1, -1):
                        if lines[i].strip().startswith('```'):
                            end_idx = i
                            break
                    if start_idx < end_idx:
                        result_clean = '\n'.join(lines[start_idx:end_idx])
                    else:
                        result_clean = '\n'.join(lines[start_idx:])
                parsed = json.loads(result_clean)
            except:
                pass

            # 策略2: 匹配花括号（正确处理嵌套）
            if not parsed:
                try:
                    start = result.find('{')
                    if start != -1:
                        brace_count = 0
                        end = start
                        for i in range(start, len(result)):
                            if result[i] == '{':
                                brace_count += 1
                            elif result[i] == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    end = i + 1
                                    break
                        if end > start:
                            json_str = result[start:end]
                            parsed = json.loads(json_str)
                except:
                    pass

            if parsed:
                raw_retained = parsed.get('retained', [])
                raw_excluded = parsed.get('excluded', [])
                reasoning = parsed.get('reasoning', '')

                if not silent:
                    print(f"   🔍 [Coarse DEBUG] 解析结果: retained={raw_retained}, excluded={raw_excluded}")

                # 兼容列表或字符串格式
                if isinstance(raw_retained, list):
                    retained_nums = [int(x) for x in raw_retained if str(x).isdigit()]
                else:
                    retained_nums = [int(x.strip()) for x in str(raw_retained).split(',')
                                    if x.strip().isdigit()]

                if not silent:
                    print(f"   🔍 [Coarse DEBUG] 保留编号: {retained_nums}, batch_meta长度: {len(batch_meta)}")

                # 转换为 batch_meta 中的索引
                retained_indices = []
                for num in retained_nums:
                    if 1 <= num <= len(batch_meta):
                        retained_indices.append(batch_meta[num-1]['index'])
                    elif not silent:
                        print(f"   ⚠️ [Coarse DEBUG] 编号 {num} 超出范围 (1-{len(batch_meta)})")

                if not silent:
                    print(f"      ✅ [Coarse] 第 {batch_num} 批: 保留 {len(retained_indices)}/{len(batch_meta)} 篇")
                return retained_indices
            else:
                if not silent:
                    print(f"   ⚠️ [Coarse DEBUG] 第 {batch_num} 批未找到 JSON 匹配")

        except Exception as e:
            error_msg = str(e) if str(e) else f"[{type(e).__name__}] {repr(e)}"
            if not silent:
                print(f"      ⚠️ [Coarse] 第 {batch_num} 批出错: {error_msg}，保留全部")
                import traceback
                print(f"      🔍 [Coarse DEBUG] 错误详情:\n{traceback.format_exc()}")

        # 出错时保留全部
        return [meta['index'] for meta in batch_meta]

    def _coarse_filter_documents(self, docs: list, user_instruction: str) -> list:
        """
        粗筛层：基于 index.json 中的 doc_id 和 core_claim 快速过滤明显不相关的文献

        Args:
            docs: 向量搜索返回的文档列表，每项为 dict 包含 content/doc_id/filepath/title
            user_instruction: 用户原始指令

        Returns:
            粗筛后保留的文档列表（保持原始格式）
        """
        if not docs:
            return []

        if len(docs) <= 5:
            # 文献太少，跳过粗筛
            return docs

        print(f"\n🔍 [Coarse Filtering] 启动粗筛层，待筛选 {len(docs)} 篇文献...")

        # 加载 index.json 映射
        index_map = self._load_index_map_for_coarse()

        # 提取元数据（鲁棒性处理）
        doc_metadata = self._extract_doc_metadata_for_coarse(docs, index_map)

        # 动态分箱参数（基于 DeepSeek-V4 1M 上下文窗口）
        MAX_DOCS_PER_BATCH = 30
        MAX_CHARS_PER_BATCH = 800000

        # 🌟 并发处理：先收集所有批次，然后并发执行
        batches = []  # [(batch_num, batch_meta), ...]
        current_batch = []
        current_chars = 0
        batch_num = 0

        for meta in doc_metadata:
            meta_str = str(meta)
            meta_len = len(meta_str)

            # 触发封箱条件
            if current_batch and (len(current_batch) >= MAX_DOCS_PER_BATCH or
                                  (current_chars + meta_len) > MAX_CHARS_PER_BATCH):
                batch_num += 1
                batches.append((batch_num, current_batch))
                # 重置批次
                current_batch = []
                current_chars = 0

            current_batch.append(meta)
            current_chars += meta_len

        # 处理尾盘
        if current_batch:
            batch_num += 1
            batches.append((batch_num, current_batch))

        print(f"   🔄 [Coarse] 共 {len(batches)} 批，启动并发处理 (max_workers={MAX_WORKERS})...")

        # 🌟 并发执行所有批次
        filtered_indices = []  # 保留的文档原始索引

        def process_batch_worker(batch_info):
            """线程工作函数：处理单个批次（子线程中禁用print避免NoSessionContext）"""
            b_num, b_meta = batch_info
            try:
                # 在子线程中使用 silent=True 避免 print 触发 NoSessionContext
                retained = self._process_coarse_batch(b_meta, user_instruction, b_num, silent=True)
                return ('ok', b_num, retained)
            except Exception as e:
                # 返回错误信息到主线程处理
                return ('error', b_num, [m['index'] for m in b_meta], str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # 提交所有批次
            future_to_batch = {executor.submit(process_batch_worker, batch): batch for batch in batches}

            # 收集结果（在主线程中输出日志）
            for future in concurrent.futures.as_completed(future_to_batch):
                batch_num, batch_meta = future_to_batch[future]
                try:
                    status, b_num, result = future.result()
                    if status == 'ok':
                        filtered_indices.extend(result)
                        print(f"      ✅ [Coarse] 第 {b_num} 批: 保留 {len(result)}/{len(batch_meta)} 篇")
                    else:
                        # 出错
                        _, b_num, fallback_indices, error_msg = status, b_num, result, future.result()[3]
                        print(f"      ⚠️ [Coarse] 第 {b_num} 批异常: {error_msg}，保留全部")
                        filtered_indices.extend(fallback_indices)
                except Exception as e:
                    print(f"      ❌ [Coarse] 第 {batch_num} 批获取结果失败: {e}")
                    # 失败时保留该批次全部
                    filtered_indices.extend([m['index'] for m in batch_meta])

        # 重建结果列表（保持原始顺序）
        filtered_indices_set = set(filtered_indices)
        filtered_docs = [docs[i] for i in range(len(docs)) if i in filtered_indices_set]

        print(f"   ✅ [Coarse Filtering] 粗筛完成: {len(filtered_docs)}/{len(docs)} 篇保留")
        return filtered_docs

    # ================= 🌊 Deep Retrieval Q&A Workflow =================

    def _workflow_deep_retrieval(self, user_instruction: str, system_prompt: str, shared_workspace: dict, args: dict) -> str:
        """
        4-Step Progressive RAG Workflow for precise Q&A with Zero-Hallucination.

        Step 1: Macro Search (VDB) - Find relevant documents
        Step 2: Meso Scanning (LLM) - Extract anchors from notes
        Step 3: Micro Extraction (Python) - Extract text from PDFs
        Step 4: Synthesis (LLM) - Generate answer with anchors
        """
        print("\n🎯 [Deep Retrieval] 启动 4 步精准问答工作流...")

        # ========== Step 0: 预选文件处理 (DEBUG: 修复预选模式) ==========
        search_results = None  # 默认使用向量搜索
        if 'target_filenames' in args and args.get('target_filenames'):
            target_files = args['target_filenames']
            if isinstance(target_files, str):
                target_files = [target_files]
            docs = self._get_exact_files(target_files)
            if docs:
                print(f"   📂 预选模式: 使用 {len(docs)} 篇指定文献")
                # 构造 search_results 格式供后续流程使用
                search_results = []
                target_dir = self.paths["kb_notes"]
                # 读取 index.json 获取 doc_id 到 original_file 的映射
                base_dir = os.path.dirname(target_dir)
                index_path = os.path.join(base_dir, "index.json")
                index_map = {}
                if os.path.exists(index_path):
                    with open(index_path, 'r', encoding='utf-8') as f:
                        index_data = json.load(f)
                        for item in index_data:
                            note_file = os.path.basename(item.get("filepath", ""))
                            index_map[note_file] = item

                for i, filename in enumerate(target_files):
                    # target_files 可能是完整路径，提取 basename
                    basename = os.path.basename(filename)
                    if not basename.endswith('.md'):
                        basename += '.md'
                    file_path = os.path.join(target_dir, basename)
                    # 从笔记内容中提取 doc_id，然后解析 original_file
                    original_file = ""
                    doc_id = ""
                    if i < len(docs) and docs[i]:
                        # 兼容新旧格式：docs[i] 可能是字符串（旧格式）或字典（新格式）
                        doc_content = docs[i].get('content', docs[i]) if isinstance(docs[i], dict) else docs[i]
                        # 提取 JSON 元数据中的 doc_id
                        json_match = re.search(r'```json\s*(.*?)\s*```', doc_content, re.DOTALL)
                        if json_match:
                            try:
                                meta = json.loads(json_match.group(1))
                                doc_id = meta.get("doc_id", "")
                            except:
                                pass
                        # 如果字典中已有 doc_id，使用它
                        if isinstance(docs[i], dict) and docs[i].get('doc_id'):
                            doc_id = docs[i].get('doc_id', '')
                    # 从 index_map 中获取原始文件路径信息（使用 basename 匹配）
                    if basename in index_map:
                        index_item = index_map[basename]
                        orig = index_item.get("paths", {}).get("original_file", "")
                        original_file = self._resolve_original_file_path(orig, doc_id, index_item)
                    # 兼容新旧格式获取 content
                    doc_content = docs[i].get('content', '') if isinstance(docs[i], dict) else (docs[i] if i < len(docs) else "")
                    search_results.append({
                        'filepath': file_path,
                        'content': doc_content,
                        'original_file': original_file,
                        'title': basename
                    })
            else:
                print(f"   ⚠️ 预选文件未找到，回退到向量搜索")

        # ========== Step 1: Macro Search ==========
        # 确保 keywords 变量始终有值（无论是预选模式还是向量搜索）
        keywords = args.get('search_keywords', user_instruction)
        if search_results is None:  # 只有没有预选文件时才执行向量搜索
            print(f"   📍 Step 1: 宏观语义检索 - 关键词: {keywords}")

            # Search KB and get results with metadata
            search_results = self._search_kb_with_metadata(keywords, shared_workspace)

            if not search_results:
                print("   ⚠️ 知识库中未找到相关内容")
                return "知识库中未检索到相关文献。"

            # 🚫 移除截断：保留所有召回文档，让粗筛层处理
            print(f"   ✅ [Deep Retrieval] 召回 {len(search_results)} 篇文档，全部进入粗筛层")

        print(f"   ✅ 找到 {len(search_results)} 篇相关文档")

        # 🌟 新增：粗筛层 - 基于 index.json 元数据快速过滤
        search_results = self._coarse_filter_documents(search_results, user_instruction)

        if not search_results:
            print("   ⚠️ 粗筛后无相关文档剩余")
            return "知识库中未检索到相关文献。"

        # ========== 构建 doc_id 映射表 (Step 1.5) ==========
        # 为每个文档建立 笔记文件名 -> doc_id 的映射，供后续提取使用
        doc_id_map = {}  # filepath -> doc_id (备用)
        filepath_to_note = {}  # 笔记文件名(不含路径) -> doc_id (主要使用)
        try:
            # kb_notes = "knowledge_base/notes"，需要获取其父目录的父目录 = 项目根目录
            kb_notes = self.paths.get("kb_notes", "")
            if kb_notes:
                # os.path.dirname("knowledge_base/notes") = "knowledge_base"
                # 再取父目录 = 项目根目录
                base_dir = os.path.dirname(os.path.dirname(kb_notes))
            else:
                base_dir = ""
            index_path = os.path.join(base_dir, "knowledge_base", "index.json") if base_dir else "knowledge_base/index.json"

            if os.path.exists(index_path):
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
                    for item in index_data:
                        doc_id = item.get("doc_id", "")
                        filepath = item.get("filepath", "")  # 相对路径如 "knowledge_base/notes/xxx.md"

                        # 用笔记文件名作为 key
                        note_filename = os.path.basename(filepath)
                        if note_filename and doc_id:
                            filepath_to_note[note_filename] = doc_id
        except Exception as e:
            print(f"   ⚠️ 加载 doc_id 映射失败: {e}")

        # ========== Step 2: Meso Scanning ==========
        print(f"   📍 Step 2: 中观扫描 - 读取笔记，提取物理锚点...")
        anchor_map = self._meso_scan_notes(search_results, user_instruction)
        anchor_map = self._apply_meso_doc_cap(search_results, anchor_map)

        # Check if all documents are irrelevant
        all_irrelevant = all(v.get('status') == 'IRRELEVANT' for v in anchor_map.values())
        if all_irrelevant:
            print("   ⚠️ 所有文档经分析后与问题无关")
            return "文献库中未找到能回答此问题的具体段落。"

        # ========== Step 2.5: Deep Scan Fallback ==========
        # Handle NEED_DEEP_SCAN cases
        deep_scan_texts = []
        for doc_path, result in anchor_map.items():
            if result.get('status') == 'NEED_DEEP_SCAN':
                print(f"   🔄 [Deep Scan] 笔记指示需深度扫描: {doc_path}")
                suspected_range = result.get('suspected_range', '')

                # 提取锚点类型和范围，支持所有格式（PDF/DOCX/EXCEL/PPTX）
                file_type = 'PDF'  # 默认
                range_str = ''
                if suspected_range:
                    # 匹配 [[TYPE: range]] 格式，支持 PDF, DOCX, EXCEL, PPTX
                    match = re.search(r'\[{1,2}(\w+):\s*([\d,\s\-]+)\]{1,2}', suspected_range)
                    if match:
                        file_type = match.group(1).upper()  # PDF/DOCX/EXCEL/PPTX
                        range_str = re.sub(r'\s+', '', match.group(2))

                original_file = result.get('original_file', '')
                note_content = result.get('note_content', '')
                doc_index = result.get('doc_index', '?')
                doc_title = os.path.basename(doc_path)

                if not original_file or not os.path.exists(original_file):
                    print(f"   ⚠️ [Doc {doc_index}] 原始文件不存在: {doc_title} (路径: {original_file})")
                    raw_text = self._extract_from_note_for_anchor(suspected_range, note_content) if suspected_range else ""
                    if raw_text:
                        doc_id = doc_id_map.get(doc_path, doc_title)
                        deep_scan_texts.append(f"[source: {doc_id}]\n{raw_text}")
                elif not range_str:
                    print(f"   📝 [Doc {doc_index}] Deep Scan 范围为空，尝试使用锚点附近的笔记上下文: {doc_title}")
                    raw_text = self._extract_from_note_for_anchor(suspected_range, note_content) if suspected_range else ""
                    if raw_text:
                        doc_id = doc_id_map.get(doc_path, doc_title)
                        deep_scan_texts.append(f"[source: {doc_id}]\n{raw_text}")
                else:
                    # 根据文件类型选择正确的提取函数
                    if file_type == 'PDF':
                        raw_text = page_anchor_parser.deep_scan_file(original_file, range_str)
                    elif file_type == 'DOCX':
                        anchor_str = f"[[DOCX: {range_str}]]"
                        raw_text = page_anchor_parser.extract_text_by_anchor(original_file, anchor_str, context_chars=250)
                    elif file_type == 'EXCEL':
                        anchor_str = f"[[EXCEL: {range_str}]]"
                        raw_text = page_anchor_parser.extract_text_by_anchor(original_file, anchor_str, context_chars=250)
                    elif file_type == 'PPTX':
                        anchor_str = f"[[PPTX: {range_str}]]"
                        raw_text = page_anchor_parser.extract_text_by_anchor(original_file, anchor_str, context_chars=250)
                    elif file_type in ['MD', 'HTML', 'WEB']:
                        anchor_str = f"[[{file_type}: {range_str}]]"
                        raw_text = self._extract_from_note_for_anchor(anchor_str, note_content)
                    else:
                        raw_text = page_anchor_parser.deep_scan_file(original_file, range_str)

                    print(f"   🔗 [Deep Scan] 类型: {file_type}, 范围: {range_str}")
                    doc_id = doc_id_map.get(doc_path, os.path.basename(original_file))
                    deep_scan_texts.append(f"[source: {doc_id}]\n{raw_text}")

        # ========== Step 3: Micro Extraction ==========
        # Handle FOUND cases
        extracted_texts = []
        anchor_read_stats = {
            'original_file_extract': 0,
            'note_local': 0,
            'global_local': 0,
            'unresolved': 0
        }
        for doc_path, result in anchor_map.items():
            if result.get('status') == 'FOUND':
                anchors = result.get('anchors', [])
                original_file = result.get('original_file', '')
                note_content = result.get('note_content', '')  # 笔记内容

                # 获取 doc_id
                doc_id = doc_id_map.get(doc_path, os.path.basename(original_file) if original_file else "")
                has_original_file = bool(original_file and os.path.exists(original_file))

                if not has_original_file:
                    doc_index = result.get('doc_index', '?')
                    doc_title = os.path.basename(doc_path)
                    print(f"   ⚠️ [Doc {doc_index}] 原始文件不存在: {doc_title} (路径: {original_file})")

                extraction_items = self._prepare_doc_anchors_for_extraction(anchors)
                seen_text_hashes = set()

                for item in extraction_items:
                    anchor = item.get('anchor', '')
                    fallback_anchor = item.get('fallback_anchor', anchor)
                    parsed = page_anchor_parser.parse_anchor(anchor) if page_anchor_parser else {'type': 'UNKNOWN', 'pages': [], 'is_global': False}
                    anchor_type = parsed.get('type', 'UNKNOWN')
                    text = ""
                    read_mode = "unresolved"

                    if parsed.get('is_global'):
                        if anchor_type not in ['UNKNOWN', 'SOURCE']:
                            text = self._extract_from_note_for_global(anchor_type, note_content)
                        if not text:
                            text = self._extract_from_note_for_anchor(fallback_anchor, note_content)
                        read_mode = "global_local" if text else "unresolved"
                    elif anchor.startswith('[[QUOTE:') or anchor.startswith('[QUOTE:'):
                        # ★ QUOTE 锚点：先从笔记提取，找不到再从源文件提取
                        text = self._extract_from_note_for_quote(fallback_anchor, note_content)
                        if text:
                            read_mode = "note_local"
                        elif has_original_file:
                            # 笔记中找不到，回退到从源文件提取
                            text = page_anchor_parser.extract_text_by_anchor_any(original_file, anchor, context_chars=250)
                            read_mode = "original_file_extract" if text and not text.startswith("[") else "unresolved"
                    else:
                        # HTML/WEB note-local anchors or missing originals should stay in note-local extraction.
                        if anchor_type in ['MD', 'HTML', 'WEB'] or not has_original_file:
                            text = self._extract_from_note_for_anchor(fallback_anchor, note_content)
                            read_mode = "note_local" if text else "unresolved"
                        elif not parsed.get('pages') and anchor_type != 'EXCEL':
                            print(f"      [📝] 锚点 '{anchor}' 缺少可读定位，尝试使用笔记局部上下文...")
                            text = self._extract_from_note_for_anchor(fallback_anchor, note_content)
                            read_mode = "note_local" if text else "unresolved"
                        else:
                            text = page_anchor_parser.extract_text_by_anchor_any(original_file, anchor, context_chars=250)
                            read_mode = "original_file_extract" if text and not text.startswith("[") else "unresolved"

                        # ★ 统一异常回退：Error / Warning / 超长 / Global标记 都使用笔记
                        is_abnormal = (
                            not text or
                            text.startswith("[Error") or
                            text.startswith("[Warning") or
                            text.startswith("[Global anchor:") or
                            len(text) > 120000
                        )
                        if is_abnormal:
                            reason = "未知异常"
                            if not text:
                                reason = "未提取到内容"
                            elif text.startswith("[Error"):
                                reason = "文件提取错误"
                            elif text.startswith("[Warning"):
                                reason = "内容未找到"
                            elif text.startswith("[Global anchor:"):
                                reason = "全文档请求被拦截"
                            elif len(text) > 120000:
                                reason = f"提取结果超长 ({len(text)} 字符)"
                            print(f"      [⚠️] {reason}，尝试从笔记回退...")
                            fallback_text = self._extract_from_note_for_anchor(fallback_anchor, note_content)
                            if fallback_text:
                                text = fallback_text
                                read_mode = "note_local"
                                print(f"      [✅] 回退成功: {len(text)} 字符")
                            else:
                                text = ""
                                read_mode = "unresolved"

                        if text:
                            text_hash = self._normalized_text_hash(text)
                            dedup_key = (doc_path, text_hash)
                            if text_hash and dedup_key in seen_text_hashes:
                                continue
                            if text_hash:
                                seen_text_hashes.add(dedup_key)
                            anchor_read_stats[read_mode] = anchor_read_stats.get(read_mode, 0) + 1
                            extracted_texts.append(f"[source: {doc_id}]\n{text}")
                        else:
                            anchor_read_stats['unresolved'] = anchor_read_stats.get('unresolved', 0) + 1

        # 统计提取结果 (moved OUTSIDE outer loop to fix infinite repetition bug)
        total_anchors = sum(len(v.get('anchors', [])) for v in anchor_map.values() if v.get('status') == 'FOUND')
        found_docs = sum(1 for v in anchor_map.values() if v.get('status') == 'FOUND')
        print(f"   ✅ 微观提取: 从 {found_docs} 篇文档提取 {total_anchors} 个锚点")
        print(
            "   📊 [Anchor Read] "
            f"original_file_extract={anchor_read_stats.get('original_file_extract', 0)}, "
            f"note_local={anchor_read_stats.get('note_local', 0)}, "
            f"global_local={anchor_read_stats.get('global_local', 0)}, "
            f"unresolved={anchor_read_stats.get('unresolved', 0)}"
        )

        # Combine all extracted text
        final_extracted = ""
        if deep_scan_texts:
            final_extracted += "\n\n=== DEEP SCAN RESULTS ===\n\n" + "\n\n".join(deep_scan_texts)
        if extracted_texts:
            final_extracted += "\n\n=== MICRO EXTRACTION RESULTS ===\n\n" + "\n\n".join(extracted_texts)

        if not final_extracted:
            print(" 未能从任何文档中提取有效内容")
            return "未能从相关文档中提取到有效内容。"

        # ========== Step 4: Map-Reduce Synthesis ==========
        # 使用 config 中的 MAX_LENGTH 作为分箱大小，OVERLAP 作为箱间重叠
        print(f"   📍 Step 4: Map-Reduce 合成 - 按 {MAX_LENGTH} 字符分箱...")

        # 将提取的文本按 MAX_LENGTH 分箱
        def chunk_texts_by_size(texts: list, max_size: int, overlap: int = OVERLAP) -> list:
            """按最大字符数分箱，支持重叠"""
            chunks = []
            current_chunk = ""
            for text in texts:
                # 如果加上新文本超过上限，保存当前箱，开始新箱
                if len(current_chunk) + len(text) > max_size:
                    if current_chunk:
                        chunks.append(current_chunk)
                    # 新箱从当前文本开始
                    current_chunk = text
                else:
                    if current_chunk:
                        current_chunk += "\n\n" + text
                    else:
                        current_chunk = text
            # 保存最后一个箱
            if current_chunk:
                chunks.append(current_chunk)
            return chunks

        # 分箱 - 只有超过 MAX_LENGTH 才分箱
        text_list = extracted_texts if isinstance(extracted_texts, list) else [extracted_texts]
        if deep_scan_texts:
            text_list = deep_scan_texts + text_list

        total_len = sum(len(t) for t in text_list if t)

        # ========== 无需分箱时：直接用 reasoner + skill 分析 ==========
        if total_len <= MAX_LENGTH:
            single_text = "\n\n".join(text_list)
            print(f"   📦 无需分箱 (总字符: {total_len})，直接用 reasoner + skill 分析...")

            # 直接使用 skill prompt + reasoner
            prompt = f"""基于以下文档内容，回答用户问题。

用户问题：{user_instruction}

文档内容：
{single_text}

请给出答案："""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]

            try:
                response = client.chat.completions.create(
                    model=get_reduce_model_name("generative"),
                    messages=messages,
                    extra_body={"thinking": {"type": "enabled"}},
                    reasoning_effort="max",
                    timeout=180
                )
                result = response.choices[0].message.content.strip()
                print("   ✅ [Deep Retrieval] 直接推理完成！")

                # 自动保存结果到 session 目录
                session_dir = shared_workspace.get('session_dir', '')
                if session_dir:
                    outputs_dir = os.path.join(session_dir, "outputs")
                    os.makedirs(outputs_dir, exist_ok=True)
                    safe_keywords = re.sub(r'[\\/*?:"<>|，。、（）\[\]{}《》]', '', keywords)[:30]
                    file_path = os.path.join(outputs_dir, f"DeepRetrieval_{safe_keywords}_{time.strftime('%Y%m%d_%H%M')}.md")
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(result)
                    print(f"   💾 [Deep Retrieval] 产物已保存: {file_path}")

                return result
            except Exception as e:
                print(f"   ❌ LLM 推理失败: {e}")
                return f"生成答案时出错: {str(e)}"

        # ========== 需要分箱时：走 Map → Reduce 两步流程 ==========
        chunks = chunk_texts_by_size(text_list, MAX_LENGTH, OVERLAP)
        print(f"   📦 需要分箱: {len(chunks)} 箱 (总字符: {total_len})")

        # ========== Step 4a: Map 阶段 - 每箱用 chat 模型处理 ==========
        print(f"   📍 Step 4a: Map 阶段 - 用 deepseek-v4-flash 处理每箱...")

        def process_map_chunk(chunk_info):
            idx, chunk = chunk_info
            map_prompt = f"""基于以下文档片段，回答用户问题。如果片段中没有相关信息，请明确说明"本片段未找到相关信息"。

用户问题：{user_instruction}

文档片段：
{chunk}

请给出基于本片段的答案："""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": map_prompt}
            ]

            response = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=messages,
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max",
                timeout=120
            )
            return idx, response.choices[0].message.content.strip(), None

        map_results = ["" for _ in chunks]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_chunk = {}
            for i, chunk in enumerate(chunks):
                print(f"      处理第 {i+1}/{len(chunks)} 箱 ({len(chunk)} 字符)...")
                future = executor.submit(process_map_chunk, (i, chunk))
                future_to_chunk[future] = i

            for future in concurrent.futures.as_completed(future_to_chunk):
                chunk_idx = future_to_chunk[future]
                try:
                    idx, content, error = future.result()
                    map_results[idx] = content if not error else f"[处理失败: {error}]"
                except Exception as e:
                    map_results[chunk_idx] = f"[处理失败: {e}]"
                    print(f"      ⚠️ 第 {chunk_idx+1} 箱处理失败: {e}")

        # ========== Step 4b: Reduce 阶段 - 用 Reduce 模型合并（UI 可调 pro/flash）==========
        reduce_model = get_reduce_model_name("generative")
        print(f"   📍 Step 4b: Reduce 阶段 - 用 {reduce_model} 合并 {len(map_results)} 个结果...")

        # 构建 reduce prompt - 使用 skill 的 system_prompt
        map_answers = "\n\n".join([f"【片段{i+1}】\n{ans}" for i, ans in enumerate(map_results)])

        reduce_prompt = f"""基于以下多个文档片段的答案，合并成一个完整、连贯的最终答案。

要求：
1. 如果某个片段表示"未找到相关信息"，请忽略该片段
2. 如果多个片段有矛盾信息，请标注出来
3. 保持答案的完整性和连贯性

用户问题：{user_instruction}

各片段答案：
{map_answers}

请给出最终答案："""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": reduce_prompt}
        ]

        try:
            response = client.chat.completions.create(
                model=get_reduce_model_name("generative"),  # Reduce 阶段，UI可调 pro/flash
                messages=messages,
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max",
                timeout=180
            )
            result = response.choices[0].message.content.strip()
            print("   ✅ [Deep Retrieval] Map-Reduce 精准问答完成！")

            # 自动保存结果到 session 目录
            session_dir = shared_workspace.get('session_dir', '')
            if session_dir:
                outputs_dir = os.path.join(session_dir, "outputs")
                os.makedirs(outputs_dir, exist_ok=True)
                safe_keywords = re.sub(r'[\\/*?:"<>|，。、（）\[\]{}《》]', '', keywords)[:30]
                file_path = os.path.join(outputs_dir, f"DeepRetrieval_{safe_keywords}_{time.strftime('%Y%m%d_%H%M')}.md")
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(result)
                print(f"   💾 [Deep Retrieval] 产物已保存: {file_path}")

            return result
        except Exception as e:
            print(f"   ❌ LLM Reduce 失败: {e}")
            return f"生成答案时出错: {str(e)}"

    def _resolve_original_file_path(self, original_file: str, doc_id: str, index_item: dict) -> str:
        """解析原始文件路径

        处理三种情况：
        1. original_file = "[auto-resolved from doc_id]" -> 用 doc_id 在 processed 文件夹查找
        2. original_file = "filename.pdf" -> 在 processed 文件夹查找
        3. original_file = "folder/filename.pdf" -> 已经是完整相对路径
        """
        kb_notes_path = self.paths.get("kb_notes", "")
        kb_base_dir = os.path.dirname(kb_notes_path) if kb_notes_path else ""
        repo_base_dir = os.path.dirname(kb_base_dir) if kb_base_dir else ""
        processed_dir = self.paths.get("processed", "")
        processed_files = []
        if processed_dir and os.path.exists(processed_dir):
            processed_files = [
                filename for filename in os.listdir(processed_dir)
                if os.path.isfile(os.path.join(processed_dir, filename))
            ]

        original_value = (original_file or "").strip()
        if original_value.lower().startswith("path_to_original_file/") or original_value.lower().startswith("path_to_original_file\\"):
            original_value = os.path.basename(original_value)

        is_placeholder = original_value in {
            "[auto-resolved from doc_id]",
            "auto-resolved from doc_id",
            "auto-resolved",
        }

        pure_doc_id = (doc_id or "").strip()
        if pure_doc_id and os.path.isabs(pure_doc_id):
            pure_doc_id = os.path.splitext(os.path.basename(pure_doc_id))[0]
        elif pure_doc_id.endswith('.md'):
            pure_doc_id = os.path.splitext(pure_doc_id)[0]

        note_basename = os.path.basename(index_item.get("filepath", "")) if isinstance(index_item, dict) else ""
        note_stem = os.path.splitext(note_basename)[0]

        def normalize_token(value: str) -> str:
            value = os.path.basename((value or "").strip())
            value = re.sub(r'[?<>:"/\\|*]', '', value)
            stem = os.path.splitext(value)[0]
            return re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]+', '', stem).lower()

        def unique_nonempty(values: list) -> list:
            deduped = []
            seen = set()
            for value in values:
                value = (value or "").strip()
                if value and value not in seen:
                    seen.add(value)
                    deduped.append(value)
            return deduped

        def log_hit(source: str, path: str) -> str:
            print(f"   🔗 [路径解析] 已定位原始文件({source}): {os.path.basename(path)}")
            return path

        def try_direct_path(path_value: str) -> str:
            if not path_value or is_placeholder:
                return ""
            if os.path.isabs(path_value) and os.path.exists(path_value):
                return log_hit("direct", path_value)
            for base_dir in unique_nonempty([kb_base_dir, repo_base_dir]):
                candidate = os.path.join(base_dir, path_value)
                if os.path.exists(candidate):
                    return log_hit("direct", candidate)
            return ""

        def try_processed_exact(candidates: list, source: str) -> str:
            if not processed_files:
                return ""
            processed_map = {filename.lower(): os.path.join(processed_dir, filename) for filename in processed_files}
            common_exts = ['.pdf', '.docx', '.pptx', '.xlsx', '.doc']

            for candidate in unique_nonempty(candidates):
                basename = os.path.basename(candidate)
                stem, ext = os.path.splitext(basename)
                variants = [basename]
                if basename:
                    cleaned_basename = re.sub(r'[?<>:"/\\|*]', '', basename)
                    if cleaned_basename != basename:
                        variants.append(cleaned_basename)
                if not ext:
                    variants.extend([basename + ext_name for ext_name in common_exts])
                    cleaned_stem = re.sub(r'[?<>:"/\\|*]', '', stem or basename)
                    if cleaned_stem:
                        variants.extend([cleaned_stem + ext_name for ext_name in common_exts])

                for variant in unique_nonempty(variants):
                    hit = processed_map.get(variant.lower())
                    if hit:
                        return log_hit(source, hit)

                candidate_norm = normalize_token(basename)
                if candidate_norm:
                    for filename in processed_files:
                        if normalize_token(filename) == candidate_norm:
                            return log_hit(source, os.path.join(processed_dir, filename))
            return ""

        def try_processed_fuzzy(candidates: list) -> str:
            if not processed_files:
                return ""
            needles = []
            for candidate in unique_nonempty(candidates):
                basename = os.path.basename(candidate)
                stem = os.path.splitext(basename)[0]
                norm_stem = normalize_token(stem)
                if len(norm_stem) >= 4:
                    needles.append(norm_stem)

            for needle in unique_nonempty(needles):
                for filename in processed_files:
                    filename_norm = normalize_token(filename)
                    if filename_norm and (needle in filename_norm or filename_norm in needle):
                        return log_hit("fuzzy", os.path.join(processed_dir, filename))
            return ""

        direct_path = try_direct_path(original_value)
        if direct_path:
            return direct_path

        original_candidates = []
        if original_value and not is_placeholder:
            original_candidates.extend([original_value, os.path.basename(original_value)])

        doc_id_candidates = [pure_doc_id]
        note_candidates = [note_basename, note_stem]

        for source, candidates in [
            ("original_file_basename", original_candidates),
            ("doc_id", doc_id_candidates),
            ("note_basename", note_candidates),
        ]:
            resolved = try_processed_exact(candidates, source)
            if resolved:
                return resolved

        base_path = try_direct_path(os.path.basename(original_value)) if original_value else ""
        if base_path:
            return base_path

        return try_processed_fuzzy(doc_id_candidates + note_candidates + original_candidates)

    def _search_kb_with_metadata(self, keywords: str, shared_workspace: dict) -> list:
        """
        Search knowledge base and return results with metadata.

        Returns:
            List of dicts: [{filepath, content, original_file, title}, ...]
        """
        # Reuse existing _search_global_kb logic but return metadata
        results = []

        # First, get new notes from workspace
        new_notes = shared_workspace.get("current_session_new_notes", [])
        for note_path in new_notes:
            try:
                with open(note_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                results.append({
                    'filepath': note_path,
                    'content': content,
                    'original_file': '',
                    'title': os.path.basename(note_path)
                })
            except Exception:
                pass

        # Then search the KB (双语)
        keyword_list = self._extract_keywords(keywords)

        # 🌟 展平关键词列表：每个概念的中英文版本都要检索
        expanded_keywords = []
        for kw_item in keyword_list:
            if isinstance(kw_item, dict):
                # 新格式：{"概念": ["中文", "English"]}
                for concept, translations in kw_item.items():
                    if isinstance(translations, list):
                        expanded_keywords.extend(translations)
                    else:
                        expanded_keywords.append(translations)
            elif isinstance(kw_item, list):
                # 兼容旧格式
                expanded_keywords.extend(kw_item)
            else:
                expanded_keywords.append(kw_item)

        all_keywords_str = " OR ".join(expanded_keywords)

        # Try vector store search
        try:
            import vector_store
            all_results = {}

            for kw in expanded_keywords:
                search_results = vector_store.search(kw, threshold=0.3)
                for result in search_results:
                    item = result["item"]
                    filepath = item.get("filepath")
                    # 将相对路径解析为绝对路径
                    filepath = resolve_path(filepath) if filepath else None
                    score = result["score"]

                    if not filepath:
                        continue

                    if filepath not in all_results or score > all_results[filepath]['score']:
                        all_results[filepath] = result

            search_results = list(all_results.values())
            search_results.sort(key=lambda x: x["score"], reverse=True)

            # Load content and original_file info
            base_dir = os.path.dirname(self.paths["kb_notes"])
            index_path = os.path.join(base_dir, "index.json")
            index_map = {}

            if os.path.exists(index_path):
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
                    for item in index_data:
                        note_file = os.path.basename(item.get("filepath", ""))
                        index_map[note_file] = item

            for result in search_results:
                item = result["item"]
                filepath = item.get("filepath")
                # 将相对路径解析为绝对路径
                filepath = resolve_path(filepath) if filepath else None

                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()

                    # Get original file from index
                    note_basename = os.path.basename(filepath)
                    original_file = ""

                    if note_basename in index_map:
                        index_item = index_map[note_basename]
                        # 修复：original_file 在 paths 对象里面
                        orig = index_item.get("paths", {}).get("original_file", "")
                        doc_id = index_item.get("doc_id", "")
                        # 使用新函数解析路径
                        original_file = self._resolve_original_file_path(orig, doc_id, index_item)

                    if not original_file:
                        print(f"   ⚠️ [Deep Retrieval] 笔记 '{note_basename}' 缺少原始文件路径，将使用笔记内容回退")

                    results.append({
                        'filepath': filepath,
                        'content': content,
                        'original_file': original_file,
                        'title': item.get('title', note_basename),
                        'score': result["score"]
                    })
                except Exception as e:
                    pass

        except ImportError:
            # Fallback to keyword matching
            base_dir = os.path.dirname(self.paths["kb_notes"])
            index_path = os.path.join(base_dir, "index.json")

            if os.path.exists(index_path):
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)

                search_keywords = [k.lower() for k in keyword_list if k.lower() != 'all']

                for item in index_data:
                    filepath = item.get("filepath")
                    # 将相对路径解析为绝对路径
                    filepath = resolve_path(filepath) if filepath else None
                    item_str_lower = str(item).lower()

                    is_match = False
                    if 'all' in search_keywords or not search_keywords:
                        is_match = True
                    else:
                        if any(k in item_str_lower for k in search_keywords):
                            is_match = True

                    if is_match:
                        try:
                            with open(filepath, 'r', encoding='utf-8') as f:
                                content = f.read()

                            original_file = ""
                            # 修复：original_file 在 paths 对象里面
                            orig = item.get("paths", {}).get("original_file", "")
                            doc_id = item.get("doc_id", "")
                            # 使用新函数解析路径
                            original_file = self._resolve_original_file_path(orig, doc_id, item)

                            results.append({
                                'filepath': filepath,
                                'content': content,
                                'original_file': original_file,
                                'title': item.get('title', os.path.basename(filepath))
                            })
                        except Exception:
                            pass

        return results

    def _process_meso_batch(self, batch: list, user_question: str, batch_num: int, start_doc_index: int) -> list:
        """
        处理单批次 Meso 扫描。使用 dict key 强制绑定文档，防止 LLM 漏答或错位。
        返回: [{'filepath': ..., 'result': {...}}, ...]
        """
        if not batch:
            return []

        # 1. 构建带有明确 ID 的 Prompt
        notes_text = ""
        for i, doc in enumerate(batch):
            notes_text += f"--- [doc_{i}] ---\n{doc['content']}\n\n"

        meso_prompt_batch = f"""You are a precise research assistant analyzing document notes.
Your task is to identify which parts of each note contain anchored evidence that can materially support the user's question.

User Question: {user_question}

Note Content:
{notes_text}

Analyze EACH document and output a SINGLE JSON DICTIONARY.
The keys MUST be the document IDs provided (e.g., "doc_0", "doc_1").
The values must be the analysis result for that document.

Use "FOUND" only when the note contains visible anchors that directly support the user's question through at least ONE substantive relation:
- Direct coverage: the document directly discusses an object, mechanism, risk, institution, case, evidence, or solution in the user's question.
- Core concept intersection: the document's concepts clearly intersect with the user's core concepts and can answer part of the question, not merely a broad umbrella term.
- Supporting evidence: the document provides facts, cases, empirical data, legal/normative basis, theoretical propositions, or mechanism explanations.
- Counterpoint / critique: the document provides counterexamples, limitations, risks, critiques, competing explanations, or alternative positions.
- Method / framework contribution: the document provides a taxonomy, evaluation framework, governance framework, method, or analytical dimension directly usable for the answer.

Use "IRRELEVANT" when a document only shares broad field terms, only provides remote background, requires large extrapolation to be useful, lacks visible evidence that can be anchored to the user's question, or is merely in the same broad field but cannot answer any sub-question. Being in a neighboring domain is not enough unless the note already exposes a directly transferable mechanism or framework with anchors.
Use "NEED_DEEP_SCAN" when the note indicates likely relevant material but the visible note does not expose a precise anchor; include "suspected_range" only if the range/anchor is visible in the note.

Relevance score rules:
- 90-100: directly answer-ready anchored evidence for the user's question.
- 70-89: strong contribution with clear anchored support.
- 40-69: plausible contribution but incomplete anchoring or narrower scope.
- 0-39: weak, remote, or irrelevant contribution.

Anchor rules:
- Return only anchors that actually appear in the provided note content.
- Do NOT invent, normalize, or convert page/paragraph/line numbers.
- Global anchors are valid only for document-level scope, overall thesis, or whole-document frameworks. If the note already shows a more local page/paragraph/line anchor for the same point, prefer the local anchor.
- For markdown notes, use existing MD anchors and do not create new HTML anchors.
- Do not create mixed Global anchors such as "[[PDF: 2, Global]]" or "[[PDF: Global, 28]]".
- If no precise anchor is visible, use "NEED_DEEP_SCAN" instead of fabricating an anchor.

Abstract examples:
- User asks "how should risk X be governed"; a document only describes the market size or industry trend of X -> IRRELEVANT.
- User asks "how should risk X be governed"; a document gives a risk taxonomy, oversight mechanism, or accountability framework for X with anchors -> FOUND.
- User asks about X in context A; a document from context B only shares the same broad field label and offers no directly transferable mechanism or framework in the visible note -> IRRELEVANT.
- User asks "how should X be evaluated"; a document provides a directly usable evaluation framework with anchors -> FOUND.

Format MUST be EXACTLY like this (NO markdown blocks, ONLY valid JSON):
{{
  "doc_0": {{
    "status": "FOUND" | "IRRELEVANT" | "NEED_DEEP_SCAN",
    "relevance_score": 92,
    "anchors": ["[[PDF: 5]]", "[[DOCX: Para 10]]"] // ONLY if FOUND
  }},
  "doc_1": {{
    "status": "IRRELEVANT",
    "relevance_score": 18
  }},
  "doc_2": {{
    "status": "NEED_DEEP_SCAN",
    "relevance_score": 54,
    "suspected_range": "[[PDF: 12-14]]"
  }}
}}"""

        # 2. 调用 LLM
        try:
            messages = [
                {"role": "system", "content": "You are a precise JSON generator. Output ONLY valid JSON."},
                {"role": "user", "content": meso_prompt_batch}
            ]

            response = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=messages,
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max",
                timeout=120
            )
            raw_response = response.choices[0].message.content.strip()

            # 3. 提取并解析 JSON（多策略鲁棒匹配）
            parsed_json = {}
            raw_response = response.choices[0].message.content.strip()

            # 策略1: 清理 markdown 后直接解析
            try:
                result_clean = raw_response.strip()
                if result_clean.startswith('```'):
                    lines = result_clean.split('\n')
                    start_idx = 0
                    for i, line in enumerate(lines):
                        if line.strip().startswith('```'):
                            start_idx = i + 1
                            break
                    end_idx = len(lines)
                    for i in range(len(lines) - 1, -1, -1):
                        if lines[i].strip().startswith('```'):
                            end_idx = i
                            break
                    if start_idx < end_idx:
                        result_clean = '\n'.join(lines[start_idx:end_idx])
                    else:
                        result_clean = '\n'.join(lines[start_idx:])
                parsed_json = json.loads(result_clean)
            except:
                pass

            # 策略2: 使用花括号匹配（正确处理嵌套）
            if not parsed_json:
                try:
                    start = raw_response.find('{')
                    if start != -1:
                        brace_count = 0
                        end = start
                        for i in range(start, len(raw_response)):
                            if raw_response[i] == '{':
                                brace_count += 1
                            elif raw_response[i] == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    end = i + 1
                                    break
                        if end > start:
                            json_str = raw_response[start:end]
                            parsed_json = json.loads(json_str)
                except Exception as e:
                    print(f"   ⚠️ [Meso扫描] 第 {batch_num} 批 JSON 解析失败: {e}")

            if not parsed_json:
                print(f"   ⚠️ [Meso扫描] 第 {batch_num} 批未找到有效 JSON，将全部标记为 IRRELEVANT")

        except Exception as e:
            print(f"   ⚠️ [Meso扫描] 第 {batch_num} 批 API 调用出错: {e}")
            parsed_json = {}

        # 4. 严格按照输入 batch 顺序重建列表（Key-Value 硬绑定 + 容错兜底）
        batch_results = []
        for i, doc in enumerate(batch):
            doc_key = f"doc_{i}"

            # LLM 漏答某篇时默认 IRRELEVANT，防止错位污染
            llm_result = parsed_json.get(doc_key, {"status": "IRRELEVANT"})
            if not isinstance(llm_result, dict):
                llm_result = {"status": "IRRELEVANT"}

            status = (llm_result.get('status') or 'IRRELEVANT').upper()
            llm_result['status'] = status
            llm_result['relevance_score'] = self._clamp_relevance_score(
                llm_result.get('relevance_score'),
                default=self._default_relevance_score_for_status(status)
            )

            # 注入元数据，保证上下游格式兼容
            llm_result['original_file'] = doc.get('original_file', '')
            llm_result['note_content'] = doc['content']
            llm_result['doc_index'] = start_doc_index + i + 1

            batch_results.append({
                'filepath': doc['filepath'],
                'result': llm_result
            })

        return batch_results

    def _meso_scan_notes(self, search_results: list, user_question: str) -> dict:
        """
        Use LLM to scan notes and identify relevant anchors.

        Returns:
            Dict mapping filepath to:
            - {"status": "FOUND", "anchors": [...], "original_file": ...}
            - {"status": "IRRELEVANT"}
            - {"status": "NEED_DEEP_SCAN", "suspected_range": "...", "original_file": ...}
        """
        results = {}

        # 收集所有文档标题用于最后输出
        doc_titles = []
        for idx, doc in enumerate(search_results):
            filepath = doc['filepath']
            title = os.path.basename(filepath)
            doc_titles.append(f"[{idx+1}] {title}")

        print(f"   📚 待筛选文献列表 ({len(search_results)} 篇):")
        for title in doc_titles:
            print(f"      {title}")

        # 🌟 双重阈值动态分批（基于 DeepSeek-V4 1M 上下文窗口）
        MAX_DOCS_PER_BATCH = 30
        MAX_CHARS_PER_BATCH = 800000

        print(f"   📊 [Meso扫描] 双阈值: ≤{MAX_DOCS_PER_BATCH}篇/批, ≤{MAX_CHARS_PER_BATCH}字符/批")

        current_batch = []
        current_chars = 0
        batch_num = 0
        overall_doc_idx = 0  # 全局 doc_index 贯穿所有 batch

        for doc in search_results:
            content = doc['content']
            doc_len = len(content)

            # 触发封箱条件
            if current_batch and (len(current_batch) >= MAX_DOCS_PER_BATCH or (current_chars + doc_len) > MAX_CHARS_PER_BATCH):
                batch_num += 1
                print(f"   🔄 [Meso扫描] 第 {batch_num} 批 ({len(current_batch)} 篇, {current_chars} 字符)...")

                batch_results = self._process_meso_batch(current_batch, user_question, batch_num, start_doc_index=overall_doc_idx)
                for r in batch_results:
                    results[r['filepath']] = r['result']
                overall_doc_idx += len(current_batch)

                current_batch = []
                current_chars = 0

            current_batch.append(doc)
            current_chars += doc_len

        # 处理尾盘
        if current_batch:
            batch_num += 1
            print(f"   🔄 [Meso扫描] 第 {batch_num} 批 ({len(current_batch)} 篇, {current_chars} 字符)...")
            batch_results = self._process_meso_batch(current_batch, user_question, batch_num, start_doc_index=overall_doc_idx)
            for r in batch_results:
                results[r['filepath']] = r['result']

        # 统计并输出筛选结果
        found_indices = []
        irrelevant_indices = []
        deep_scan_indices = []

        for filepath, result in results.items():
            idx = result.get('doc_index', 0)
            status = result.get('status', 'IRRELEVANT')
            if status == 'FOUND':
                found_indices.append(str(idx))
            elif status == 'NEED_DEEP_SCAN':
                deep_scan_indices.append(str(idx))
            else:
                irrelevant_indices.append(str(idx))

        print(f"   ✅ [Meso扫描] 筛选结果:")
        if found_indices:
            print(f"      相关 (保留): {','.join(found_indices)}")
        if deep_scan_indices:
            print(f"      需深度扫描: {','.join(deep_scan_indices)}")
        if irrelevant_indices:
            print(f"      不相关 (筛除): {','.join(irrelevant_indices)}")
        print(f"      总计: 相关 {len(found_indices)} 篇 | 需深度扫描 {len(deep_scan_indices)} 篇 | 不相关 {len(irrelevant_indices)} 篇")

        return results

    def _extract_stages_from_instruction(self, instruction: str, stage_config: dict) -> list:
        """通用阶段提取器：根据skill定义的配置从指令中提取多个阶段

        Args:
            instruction: 用户指令（可能包含大纲、多个任务描述等）
            stage_config: 技能定义的阶段配置，包含：
                - stage_extraction_prompt: 如何从指令中提取阶段的prompt模板
                - keyword_per_stage: 每个阶段需要的关键词数量

        Returns:
            stages: [{"name": "阶段名", "keywords": ["kw1", "kw2"]}, ...]
        """
        # 获取 prompt 模板，然后用 instruction 替换
        prompt_template = stage_config.get("stage_extraction_prompt", """从以下用户指令中提取多个工作阶段。

要求：
1. 识别指令中的各个独立阶段/部分
2. 为每个阶段提取核心检索关键词
3. 返回JSON数组格式

指令内容：
{instruction}

返回格式示例：
[
  {"name": "阶段1", "keywords": ["关键词1", "关键词2"], "purpose": "阶段目的"},
  {"name": "阶段2", "keywords": ["关键词3", "关键词4"], "purpose": "阶段目的"}
]""")

        # 手动替换，避免 JSON 示例中的大括号被误解
        extraction_prompt = prompt_template.replace("{instruction}", instruction)

        try:
            res = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": extraction_prompt}],
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max"
            )
            raw_result_str = res.choices[0].message.content
            print(f"   🔍 [调试] 阶段提取原始返回(前500字符): {raw_result_str[:500]}...")
            raw_result = json.loads(raw_result_str)

            # 防御性处理：确保返回的是正确的格式
            stages = []

            if isinstance(raw_result, list):
                # 直接是数组
                stages = raw_result
            elif isinstance(raw_result, dict):
                # 检查常见的外层键
                for key in ['sections', 'stages', 'outline', 'chapters', 'parts', 'outline_keywords']:
                    if key in raw_result and isinstance(raw_result[key], list):
                        stages = raw_result[key]
                        break
                else:
                    # 如果没有常见键，尝试转换为数组格式
                    stages = [{"name": k, "keywords": v} for k, v in raw_result.items()]

            # 确保每个阶段都有 name 和 keywords
            normalized_stages = []
            for i, s in enumerate(stages):
                if isinstance(s, dict):
                    normalized_stages.append({
                        "name": s.get("name", s.get("title", f"阶段{i+1}")),
                        "keywords": s.get("keywords", s.get("tags", [])),
                        "purpose": s.get("purpose", s.get("goal", ""))
                    })
                elif isinstance(s, str):
                    normalized_stages.append({
                        "name": s,
                        "keywords": [],
                        "purpose": ""
                    })

            stages = normalized_stages

            msg = f"   🔑 [阶段提取] 共提取 {len(stages)} 个工作阶段"
            print(msg)
            self.send_gui_msg(msg, 'info')
            return stages
        except Exception as e:
            import traceback
            print(f"   ⚠️ 阶段提取失败: {e}")
            print(f"   🔍 [调试] 详细错误: {traceback.format_exc()}")
            return []

    def _search_for_stage(self, stage_keywords: list, shared_workspace: dict, stage_purpose: str = "", stage_name: str = "") -> list:
        """通用阶段检索器：为特定阶段检索高度相关的文档

        Args:
            stage_keywords: 阶段关键词列表
            shared_workspace: 共享工作空间
            stage_purpose: 阶段目的，用于更精准的语义检索
            stage_name: 阶段名称
        """
        docs = []
        loaded_paths = set()

        # 🌟 展平双语关键词列表
        expanded_keywords = []
        for kw_item in stage_keywords[:4] if stage_keywords else []:
            if isinstance(kw_item, dict):
                # 新格式：{"概念": ["中文", "English"]}
                for concept, translations in kw_item.items():
                    if isinstance(translations, list):
                        expanded_keywords.extend(translations)
                    else:
                        expanded_keywords.append(translations)
            elif isinstance(kw_item, list):
                # 兼容旧格式
                expanded_keywords.extend(kw_item)
            else:
                expanded_keywords.append(kw_item)

        search_keywords = expanded_keywords

        if not search_keywords:
            print(f"   ⚠️ 阶段 '{stage_name}' 没有关键词，跳过检索")
            return [], []

        print(f"   🔑 [阶段 '{stage_name}'] 使用双语关键词: {search_keywords}")

        # 强制提取黑板上的新笔记（优先使用）
        new_notes = shared_workspace.get("current_session_new_notes", [])
        for note_path in new_notes:
            try:
                with open(note_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    docs.append({
                        "content": content,
                        "source": note_path,
                        "score": 1.0  # 新笔记最高相关性
                    })
                    loaded_paths.add(note_path)
            except:
                pass

        # 对每个关键词分别搜索，使用更高的相关性阈值
        for kw in search_keywords:
            try:
                import vector_store
                # 阈值0.4，只保留相关文档
                search_results = vector_store.search(kw, threshold=0.35)
                for result in search_results:
                    item = result["item"]
                    filepath = item.get("filepath")
                    # 将相对路径解析为绝对路径
                    filepath = resolve_path(filepath) if filepath else None
                    score = result["score"]
                    if not filepath or filepath in loaded_paths:
                        continue
                    # 只保留相关性>=0.4的文档
                    if score >= 0.4:
                        try:
                            with open(filepath, 'r', encoding='utf-8') as f:
                                content = f.read()
                                docs.append({
                                    "content": content,
                                    "source": filepath,
                                    "score": score,
                                    "keyword": kw
                                })
                                loaded_paths.add(filepath)
                        except:
                            pass
            except:
                pass

        # 按相关性排序，高相关性的文档排在前面
        docs.sort(key=lambda x: x.get("score", 0), reverse=True)

        # 返回两个列表：文档内容和文档信息（包含相关性分数）
        # 宽进：检索所有相关文档，由生成阶段"严出"决定使用哪些
        return [d["content"] for d in docs], docs

    def _generate_stage_content(self, stage_info: dict, all_stages_context: list, retrieved_docs: list,
                                retrieved_docs_info: list, system_prompt: str, user_instruction: str,
                                session_dir: str = None) -> str:
        """通用阶段内容生成器：生成单个阶段的内容

        Args:
            stage_info: {"name": "阶段名", "keywords": [...], "purpose": "阶段目的"}
            all_stages_context: 所有阶段的列表，用于理解整体流程
            retrieved_docs: 该阶段检索到的文档内容列表
            retrieved_docs_info: 该阶段检索到的文档信息列表，包含相关性分数
            system_prompt: 技能定义的系统prompt
            user_instruction: 原始用户指令
            session_dir: 会话目录，用于保存中间稿
        """
        stage_name = stage_info.get("name", "未命名阶段")
        stage_purpose = stage_info.get("purpose", "")

        # 检查文档数量，决定是否需要分批处理
        doc_count = len(retrieved_docs_info) if retrieved_docs_info else 0

        if doc_count > 30:
            print(f"   ⚠️ 阶段 '{stage_name}' 检索到 {doc_count} 篇文档，触发分批处理...")
            return self._generate_stage_content_batched(
                stage_info, all_stages_context, retrieved_docs_info,
                system_prompt, user_instruction, session_dir
            )

        # 文档数量在安全范围内，使用直接生成
        return self._generate_stage_content_direct(
            stage_info, all_stages_context, retrieved_docs_info,
            system_prompt, user_instruction
        )

    def _generate_stage_content_direct(self, stage_info: dict, all_stages_context: list,
                                       retrieved_docs_info: list, system_prompt: str,
                                       user_instruction: str) -> str:
        """直接生成阶段内容（文档数量在安全范围内）"""
        stage_name = stage_info.get("name", "未命名阶段")
        stage_purpose = stage_info.get("purpose", "")

        # 构建带相关性分数的文档列表
        docs_text = ""
        if retrieved_docs_info:
            docs_with_scores = []
            for i, doc_info in enumerate(retrieved_docs_info):
                score = doc_info.get("score", 0)
                content = doc_info.get("content", "")[:600]
                docs_with_scores.append(f"[Doc {i+1} 相关度:{score:.2f}]: {content}")
            docs_text = chr(10).join(docs_with_scores)
        else:
            docs_text = "无特定参考文献"

        prompt = f"""【任务】：完成以下工作阶段的内容。要求：只使用高度相关的文献进行论证。

【阶段名称】：{stage_name}
【阶段写作目的】：{stage_purpose}

【所有阶段概览】：
{chr(10).join([f"- {s['name']}: {s.get('purpose', '')}" for s in all_stages_context])}

【原始用户指令】：
{user_instruction}

【该阶段检索到的参考文献】（按相关性从高到低排序）：
{docs_text}

【提示】：文档已按相关性从高到低排序，请根据实际论证需要合理使用。

【写作要求】：
1. 此阶段是整体任务的一部分，需要与整体逻辑连贯
2. 只引用高度相关的文献进行论证
3. 使用 citations 格式：([Doc X])
4. 写作风格要专业、学术
5. 输出完整的该阶段内容，不要使用占位符
6. **必须写成自然段落**，不要使用 bullet points 或列表！用流畅的段落文字把各个观点逻辑地衔接、融合起来
7. 每个论点的引用要自然融入段落中，不要堆砌

请直接输出该阶段的内容（必须是段落形式）："""

        try:
            res = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": f"严格遵循以下技能架构指令：\n\n{system_prompt}"},
                    {"role": "user", "content": prompt}
                ],
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max"
            )
            return res.choices[0].message.content
        except Exception as e:
            return f"[生成失败: {str(e)}]"

    def _generate_stage_content_batched(self, stage_info: dict, all_stages_context: list,
                                        retrieved_docs_info: list, system_prompt: str,
                                        user_instruction: str, session_dir: str = None) -> str:
        """分批生成阶段内容（文档太多时使用）"""
        stage_name = stage_info.get("name", "未命名阶段")
        stage_purpose = stage_info.get("purpose", "")

        # 准备中间目录
        if session_dir:
            intermediate_dir = os.path.join(session_dir, "stage_drafts")
            os.makedirs(intermediate_dir, exist_ok=True)

        # 构建带相关性分数的元数据
        json_metadata_collection = []
        for doc_info in retrieved_docs_info:
            content = doc_info.get("content", "")
            score = doc_info.get("score", 0)
            # 尝试提取JSON元数据
            json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if json_match:
                try:
                    meta_dict = json.loads(json_match.group(1))
                    brief_meta = {k: v for k, v in meta_dict.items() if k not in ['authors', 'publication_info']}
                    brief_meta['score'] = score
                    json_metadata_collection.append(str(brief_meta))
                except:
                    pass

        # Phase 1: 构建分析框架
        print(f"   🦅 [Phase 1] 正在构建 '{stage_name}' 的分析框架...")
        meta_prompt = f"""基于以下文档的元数据，为阶段 '{stage_name}' 构建分析框架。

阶段目标：{stage_purpose}
任务目标：{user_instruction}

元数据（共 {len(json_metadata_collection)} 篇，按相关性排序）：
{chr(10).join(json_metadata_collection[:30])}"""

        try:
            res_meta = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": meta_prompt}],
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max"
            )
            global_overview = res_meta.choices[0].message.content
        except Exception:
            global_overview = "按通用方式进行结构化处理。"

        # Phase 2: 智能分箱
        SAFE_CHAR_LIMIT = 80000
        batches = []
        current_batch = []
        current_batch_len = 0

        for doc_info in retrieved_docs_info:
            content = doc_info.get("content", "")
            doc_len = len(content)

            # 极端防爆机制
            if doc_len > SAFE_CHAR_LIMIT and not current_batch:
                batches.append([doc_info])
                continue

            # 正常装箱
            if current_batch_len + doc_len > SAFE_CHAR_LIMIT and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_batch_len = 0

            current_batch.append(doc_info)
            current_batch_len += doc_len

        if current_batch:
            batches.append(current_batch)

        print(f"   📏 [智能装箱] {len(retrieved_docs_info)} 篇文献被重组为 {len(batches)} 个批次")

        # Phase 3: 逐批处理
        current_draft = ""
        print(f"   ⚙️ [Phase 2] 启动批量处理！共 {len(batches)} 个批次。")

        for idx, batch_docs in enumerate(batches):
            print(f"   🔄 正在处理第 {idx+1}/{len(batches)} 批...")

            # 构建带相关性分数的文档文本
            batch_texts = []
            for i, doc_info in enumerate(batch_docs):
                score = doc_info.get("score", 0)
                content = doc_info.get("content", "")[:800]
                batch_texts.append(f"[Doc {i+1} 相关度:{score:.2f}]: {content}")

            docs_text = chr(10).join(batch_texts)

            if idx == 0:
                action_instruction = f"请基于以下文档和全局分析框架，完成阶段 '{stage_name}' 的写作任务。"
            else:
                action_instruction = f"请将以下新增文档的内容整合进已有分析中，生成完整、统一的结果。"

            iterative_prompt = f"""
【阶段名称】：{stage_name}
【阶段写作目的】：{stage_purpose}

【所有阶段概览】：
{chr(10).join([f"- {s['name']}: {s.get('purpose', '')}" for s in all_stages_context])}

【原始用户指令】：
{user_instruction}

【全局分析框架】：
{global_overview}

{"【已有内容（请整合进最终输出）】:\n" + current_draft if current_draft else ""}

【待处理文档】：
{docs_text}

{action_instruction}

⚠️ 重要：
1. 输出必须是该阶段的完整内容，不要提及"批次"、"迭代"等处理过程信息
2. **必须写成自然段落**，不要使用 bullet points 或列表
3. 用流畅的段落文字把各个观点逻辑地衔接、融合起来
"""

            try:
                res_iter = client.chat.completions.create(
                    model="deepseek-v4-flash",
                    messages=[
                        {"role": "system", "content": f"严格遵循以下技能架构指令：\n\n{system_prompt}"},
                        {"role": "user", "content": iterative_prompt}
                    ],
                    extra_body={"thinking": {"type": "enabled"}},
                    reasoning_effort="max"
                )
                current_draft = res_iter.choices[0].message.content

                # 保存检查点
                if session_dir:
                    checkpoint_path = os.path.join(intermediate_dir, f"stage_{stage_name[:10]}_batch_{idx+1}.md")
                    with open(checkpoint_path, 'w', encoding='utf-8') as f:
                        f.write(current_draft)

                print(f"   ✅ 第 {idx+1} 批处理完毕！")

            except Exception as e:
                print(f"   ❌ 第 {idx+1} 批处理发生错误: {e}")
                break

        return current_draft

    def _integrate_stages(self, stages_content: list, integration_config: dict,
                          system_prompt: str, user_instruction: str) -> str:
        """通用阶段整合器：将多个阶段的内容整合为最终输出

        Args:
            stages_content: [{"name": "阶段名", "content": "内容"}, ...]
            integration_config: 整合配置，包含整合prompt模板
            system_prompt: 技能的系统prompt
            user_instruction: 原始用户指令
        """
        integration_prompt_template = integration_config.get("prompt_template", """【任务】：将以下各阶段的内容整合为完整文章。

【核心要求】：
1. **保留所有原始引用** - 不要修改任何 [Doc X] 引用
2. **只做逻辑衔接** - 添加过渡句使文章流畅
3. **润色文字** - 使表达更专业流畅
4. **参考文献按章节分类** - 在文章末尾按以下格式生成参考文献：
   - 一、问题提出与研究意义：列出本章引用的所有文献
   - 二、数据跨境流动的制度与权利基础：列出本章引用的所有文献
   - 依此类推...

【用户原始指令】：
{user_instruction}

【各阶段内容】：
{stages}

【你的任务】：
1. 整合各阶段内容，保持结构完整
2. 检查各章节过渡是否自然，添加过渡句
3. 保留所有原始引用，只做润色
4. 在末尾按章节分类生成参考文献

请直接输出完整文章：""")

        stages_text = chr(10).join([f"## {s['name']}\n{s['content']}" for s in stages_content])
        integration_prompt = integration_prompt_template.format(
            user_instruction=user_instruction,
            stages=stages_text
        )

        try:
            res = client.chat.completions.create(
                model=get_reduce_model_name("generative"),
                messages=[
                    {"role": "system", "content": f"严格遵循以下技能架构指令：\n\n{system_prompt}"},
                    {"role": "user", "content": integration_prompt}
                ],
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max"
            )
            return res.choices[0].message.content
        except Exception as e:
            # 整合失败，回退到直接拼接
            print(f"   ⚠️ 整合失败，使用直接拼接: {e}")
            return chr(10).join([f"## {s['name']}\n{s['content']}" for s in stages_content])

    def _engine_multi_stage(self, user_instruction: str, system_prompt: str, session_dir: str,
                           shared_workspace: dict, stage_config: dict) -> str:
        """通用多阶段引擎：可被多种skill调用的分段检索与生成机制

        Args:
            user_instruction: 用户指令
            system_prompt: 技能的系统prompt
            session_dir: 会话目录
            shared_workspace: 共享工作空间
            stage_config: 阶段配置，来自skill定义：
                {
                    "stage_extraction_prompt": "...",
                    "per_stage_prompt_addition": "...",
                    "integration_prompt_template": "..."
                }
        """
        print("\n🎯 [多阶段模式] 正在解析任务阶段...")

        # Step 1: 从指令中提取多个阶段
        stages = self._extract_stages_from_instruction(user_instruction, stage_config)

        if not stages:
            print("   ⚠️ 无法提取阶段，回退到标准生成模式")
            return None

        print(f"   ✅ 阶段解析完成，共 {len(stages)} 个阶段")

        # 创建中间目录
        intermediate_dir = os.path.join(session_dir, "stage_drafts")
        os.makedirs(intermediate_dir, exist_ok=True)

        # Step 2: 并行处理所有阶段（各阶段独立，可并行）
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def process_single_stage(args_tuple):
            """处理单个阶段（供并行调用）"""
            idx, stage, all_stages_context, sys_prompt, user_instr, sess_dir, intermediate_dir = args_tuple
            stage_name = stage.get("name", f"阶段{idx+1}")
            print(f"\n📦 [阶段 {idx+1}/{len(stages)}] 正在处理: {stage_name}")

            # 🌟 改用 _search_global_kb（LitReview/Ideation 同款检索引擎）
            stage_keywords_raw = stage.get("keywords", [])
            if isinstance(stage_keywords_raw, list):
                stage_kw = ", ".join(str(k) for k in stage_keywords_raw[:5])
            else:
                stage_kw = str(stage_keywords_raw) if stage_keywords_raw else stage.get("purpose", "")
            stage_docs_info = self._search_global_kb(stage_kw, shared_workspace) or []
            stage_docs = [d['content'] for d in stage_docs_info]  # 兼容生成器的字符串列表格式

            if stage_docs:
                print(f"   🔍 [{stage_name}] 检索到 {len(stage_docs)} 篇相关文档")

                # 🌟 两阶段筛选：过滤不相关文献（与 LitReview/Ideation 同一套筛选器）
                # 传 stage_docs_info（dict列表）而非 stage_docs（字符串列表），
                # _filter_relevant_docs 内部用 content 构建 LLM prompt，但返回原始 dict 对象
                if len(stage_docs) > 5:
                    print(f"\n🔍 [系统] 启动【两阶段筛选】，用 Chat 模型快速过滤不相关文献...")
                    filtered_dicts = self._filter_relevant_docs(stage_docs_info, user_instr)
                    stage_docs = [d['content'] for d in filtered_dicts]  # 提取 content 字符串供生成用
                    print(f"✅ [系统] LLM 筛选完毕，保留 {len(stage_docs)} 篇相关文献！")

            else:
                print(f"   ⚠️ [{stage_name}] 未检索到相关文档")

            # 生成该阶段内容
            stage_content = self._generate_stage_content(
                stage, all_stages_context, stage_docs, stage_docs_info,
                sys_prompt, user_instr, sess_dir
            )

            # 保存中间稿
            stage_path = os.path.join(intermediate_dir, f"stage_{idx+1}_{stage_name[:20]}.md")
            with open(stage_path, 'w', encoding='utf-8') as f:
                f.write(f"## {stage_name}\n\n{stage_content}")

            return {
                "idx": idx,
                "name": stage_name,
                "content": stage_content,
                "keywords": stage.get("keywords", []),
                "doc_count": len(stage_docs)
            }

        # 串行执行所有阶段（避免 ThreadPoolExecutor 在 Streamlit 环境下触发 NoSessionContext）
        print(f"\n🚀 启动串行处理 {len(stages)} 个阶段...")
        all_stages_content = []
        for idx, stage in enumerate(stages):
            task = (idx, stage, stages, system_prompt, user_instruction, session_dir, intermediate_dir)
            result = process_single_stage(task)
            all_stages_content.append(result)
            print(f"   ✅ 阶段 '{result['name']}' 完成")

        # 按原始顺序排序
        all_stages_content.sort(key=lambda x: x["idx"])

        # Step 3: 整合所有阶段
        print("\n🔗 [多阶段模式] 正在整合各阶段...")

        final_content = self._integrate_stages(
            all_stages_content, stage_config, system_prompt, user_instruction
        )

        # 保存最终稿
        final_path = os.path.join(intermediate_dir, "final_output.md")
        with open(final_path, 'w', encoding='utf-8') as f:
            f.write(final_content)

        print(f"   ✅ 整合完成")
        return final_content

    def execute_skill(self, function_name: str, args: dict, shared_workspace: dict) -> str:
        skill_info = self.skills.get(function_name)
        if not skill_info: 
            error_msg = f"❌ [严重错误] 未找到技能 '{function_name}'！已加载的技能有: {list(self.skills.keys())}"
            print(error_msg)
            return error_msg
        
        meta = skill_info['meta']
        # 旧格式：直接从 meta 取 name
        skill_display_name = meta.get('name', function_name)
        print(f"\n⚙️ [系统] 正在装载技能: {skill_display_name}")
        
        docs = []
        if 'target_filenames' in args and args.get('target_filenames'):
            target_files = args['target_filenames']
            if isinstance(target_files, str): target_files = [target_files]
            
            print(f"🎯 [系统] 技能要求精确读取特定文件: {target_files}")
        user_instruction = args.get('user_instruction', '无')
        system_prompt = self._load_skill_content(function_name)

        # 语言替换：{{language}} 占位符
        system_prompt = system_prompt.replace("{{language}}", get_target_language())

        # 检测执行模式 - 旧格式直接在 meta 中
        execution_mode = meta.get('input_schema', {}).get('execution_mode', 'standard')

        # 🌟 Deep Retrieval Q&A 路由检测
        if 'Deep Retrieval' in skill_display_name or execution_mode == 'deep_retrieval':
            print(f"\n🎯 [系统] 检测到【Deep Retrieval Q&A】任务，启动精准问答引擎...")
            result_text = self._workflow_deep_retrieval(
                user_instruction, system_prompt,
                shared_workspace, args
            )
            # 保存输出
            if result_text:
                out_schema = meta.get('output_schema', {})
                if out_schema.get('save_to_disk', False):
                    session_dir = shared_workspace['session_dir']
                    outputs_dir = os.path.join(session_dir, "outputs")
                    os.makedirs(outputs_dir, exist_ok=True)
                    template = out_schema.get('filename_template', '{timestamp}.md')
                    file_path = os.path.join(outputs_dir, template.replace('{timestamp}', time.strftime("%Y%m%d_%H%M")))
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(result_text)
                    print(f"💾 [系统] 产物已保存: {file_path}")
            return result_text if result_text else "任务执行完成但无内容产出"

        # 可复用多轮检索工作流：由 skill metadata 显式声明才触发
        if execution_mode == 'multi_round_retrieval':
            print(f"\n🎯 [系统] 检测到【多轮检索工作流】，启动分阶段检索、筛选、生成与整合...")
            from generative_multi_round_workflow import run_multi_round_retrieval_workflow

            workflow_config = meta.get('multi_round_workflow', {})
            result_text = run_multi_round_retrieval_workflow(
                self,
                user_instruction,
                system_prompt,
                shared_workspace['session_dir'],
                shared_workspace,
                workflow_config
            )

            if result_text:
                out_schema = meta.get('output_schema', {})
                if out_schema.get('save_to_disk', False):
                    session_dir = shared_workspace['session_dir']
                    outputs_dir = os.path.join(session_dir, "outputs")
                    os.makedirs(outputs_dir, exist_ok=True)
                    template = out_schema.get('filename_template', '{timestamp}.md')
                    keywords = args.get('search_keywords', 'General')
                    keywords = re.sub(r'[\\/*?:"<>|，。、（）\[\]{}《》]', '', keywords)
                    keywords = keywords[:30] if len(keywords) > 30 else keywords
                    file_name = template.replace('{search_keywords}', keywords)
                    file_name = file_name.replace('{timestamp}', time.strftime("%Y%m%d_%H%M"))
                    file_name = re.sub(r'[\\/*?:"<>|]，', '', file_name)
                    file_path = os.path.join(outputs_dir, file_name)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(result_text)
                    print(f"💾 [系统] 产物已保存: {file_path}")
            return result_text if result_text else "任务执行完成但无内容产出"

        # 多阶段模式：先跳过全局检索，直接进入分段检索
        if execution_mode == 'multi_stage':
            stage_config = meta.get('stage_config', {})
            print(f"\n🎯 [系统] 检测到【多阶段模式】，跳过全局检索，启动分段检索与生成...")
            result_text = self._engine_multi_stage(
                user_instruction, system_prompt,
                shared_workspace['session_dir'],
                shared_workspace, stage_config
            )
            # 后续处理...
            if result_text:
                # 保存结果等操作
                out_schema = meta.get('output_schema', {})
                if out_schema.get('save_to_disk', False):
                    session_dir = shared_workspace['session_dir']
                    outputs_dir = os.path.join(session_dir, "outputs")
                    os.makedirs(outputs_dir, exist_ok=True)
                    template = out_schema.get('filename_template', '{timestamp}.md')
                    keywords = args.get('search_keywords', 'General')
                    keywords = re.sub(r'[\\/*?:"<>|，。、（）\[\]{}《》]', '', keywords)
                    keywords = keywords[:30] if len(keywords) > 30 else keywords
                    file_name = template.replace('{search_keywords}', keywords)
                    file_name = file_name.replace('{timestamp}', time.strftime("%Y%m%d_%H%M"))
                    file_name = re.sub(r'[\\/*?:"<>|]，', '', file_name)
                    file_path = os.path.join(outputs_dir, file_name)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(result_text)
                    print(f"💾 [系统] 产物已保存: {file_path}")
            return result_text if result_text else "任务执行完成但无内容产出"

        # 其他模式（standard, auto等）才需要全局检索
        docs = []
        if 'target_filenames' in args and args.get('target_filenames'):
            target_files = args['target_filenames']
            if isinstance(target_files, str): target_files = [target_files]
            print(f"🎯 [系统] 技能要求精确读取特定文件: {target_files}")
            docs = self._get_exact_files(target_files)
            if docs:
                print(f"✅ [系统] 已精准提取 {len(docs)} 篇指定文献。")
            else:
                print(f"⚠️ [系统] 未找到指定文件，将仅根据指令生成。")

        elif meta.get('input_schema', {}).get('requires_knowledge_base', False) or 'Literature Review' in skill_display_name:
            keywords = args.get('search_keywords', user_instruction)
            print(f"🔍 [系统] 正在检索知识库，关键词: {keywords}")
            # ⬇️ 传黑板进去
            docs = self._search_global_kb(keywords, shared_workspace)
            if docs:
                print(f"✅ [系统] 数据库检索完毕，共提取 {len(docs)} 篇文献！")

                # 🌟 新增：粗筛层 - 基于 index.json 元数据快速过滤明显不相关文献
                docs = self._coarse_filter_documents(docs, user_instruction)

                # 🌟 两阶段筛选：对 Literature Review 或标记了 requires_relevant_doc_filtering 的技能启用
                if (meta.get('input_schema', {}).get('requires_relevant_doc_filtering', False) or 'Literature Review' in skill_display_name) and len(docs) > 5:
                    print(f"\n🔍 [系统] 启动【两阶段筛选】，用 Chat 模型快速过滤不相关文献...")
                    docs = self._filter_relevant_docs(docs, user_instruction)
                    print(f"✅ [系统] LLM 筛选完毕，保留 {len(docs)} 篇相关文献！")
            else:
                print(f"⚠️ [系统] 数据库未找到强相关内容，将仅根据指令生成。")

        # 🔍 [DEBUG] 路由前最终确认：过滤后 doc 数量和 execution_mode
        print(f"   🔍 [DEBUG-ROUTING] docs_after_filter={len(docs)}, execution_mode={execution_mode}, skill={skill_display_name}")

        # 🌟 转换 docs 为带来源标注的字符串列表（兼容新旧格式）
        def convert_docs_to_text(doc_list):
            """将 docs 转换为带来源标注的字符串列表"""
            if not doc_list:
                return []
            # 检查是否是字典列表（新格式）
            if isinstance(doc_list[0], dict):
                result = []
                for d in doc_list:
                    doc_id = d.get('doc_id', d.get('title', 'Unknown'))
                    content = d.get('content', str(d))
                    result.append(f"[Source: {doc_id}]\n{content}")
                return result
            else:
                # 旧格式，直接返回字符串列表
                return doc_list

        docs_text = convert_docs_to_text(docs)

        # 标准模式和其他模式的海量文档处理
        if len(docs) > 30 or execution_mode == 'iterative_snowball':
            print(f"\n🌊 [系统] 侦测到海量文档 ({len(docs)} 篇)。自动触发【多轮处理引擎】...")
            # ⬇️ 传专案目录和任务类型进去
            result_text = self._engine_iterative_snowball(
                docs_text, user_instruction, system_prompt,
                shared_workspace['session_dir'],
                task_type=skill_display_name
            )
        else:
            print(f"\n⚡ [系统] 文献量在安全区间，启动【标准生成引擎】...")
            context_text = "\n\n".join(docs_text) if docs_text else ""
            messages = [
                {"role": "system", "content": f"严格遵循以下技能架构指令：\n\n{system_prompt}"},
                {"role": "user", "content": f"【用户指令】\n{user_instruction}\n\n【全局数据库提供的上下文】\n{context_text}"}
            ]
            print(f"🧠 [系统] 大模型思考与生成中...")
            response = client.chat.completions.create(
                model=get_reduce_model_name("generative"),
                messages=messages,
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max"
            )
            result_text = response.choices[0].message.content

        # 🌟 自动保存输出到 session 目录（无论是否有 output_schema 配置）
        session_dir = shared_workspace.get('session_dir', '')
        if session_dir and result_text:
            outputs_dir = os.path.join(session_dir, "outputs")
            os.makedirs(outputs_dir, exist_ok=True)

            # 获取文件名模板
            out_schema = meta.get('output_schema', {})
            if out_schema.get('save_to_disk', False):
                template = out_schema.get('filename_template', '{timestamp}.md')
            else:
                # 默认模板
                template = 'output_{timestamp}.md'

            # 获取关键词并处理
            keywords = args.get('search_keywords', 'General')
            keywords = re.sub(r'[\\/*?:"<>|，。、（）\[\]{}《》]', '', keywords)
            keywords = keywords[:30] if len(keywords) > 30 else keywords

            file_name = template.replace('{search_keywords}', keywords)
            file_name = file_name.replace('{timestamp}', time.strftime("%Y%m%d_%H%M"))
            file_name = re.sub(r'[\\/*?:"<>|]，', '', file_name)

            save_path = os.path.join(outputs_dir, file_name)

            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(result_text)
            print(f"💾 [系统] 产物已保存: {save_path}")

        return result_text

    def execute(self, task_params: dict, shared_workspace: dict) -> dict:
        skill_name = task_params.get("skill_name")
        user_instruction = task_params.get("user_instruction", "")
        self._active_manual_preselected = bool(task_params.get("target_filenames"))
        self._active_retrieval_doc_cap = self._normalize_retrieval_doc_cap(
            task_params.get("retrieval_doc_cap", 50)
        )
        
        if not skill_name or skill_name not in self.skills:
            # ⬇️ 接收字典
            routing_decision = self.identify_best_skill(user_instruction)
            skill_name = routing_decision.get("skill_id")
            thought_process = routing_decision.get("thought_process", "无思考记录")
            
            # ⬇️ 呼叫基类的记录器
            self.log_routing_decision(
                session_dir=shared_workspace.get("session_dir", ""),
                target_name=task_params.get("search_keywords", "Global Generate Task"),
                skill_id=skill_name,
                reason=thought_process
            )
            
        if "search_keywords" not in task_params:
            task_params["search_keywords"] = user_instruction

        # 🌟 强制拦截器：防范厂长瞎提取局部关键词！
        # 只要用户指令里暗示了全局总结，强行覆盖为 'all'
        global_keywords = ["所有文献", "全部文献", "所有文章", "全部文章", "总结所有",
                          "all documents", "all files", "all literature", "all papers",
                          "summary of all", "summarize all", "review all"]
        if any(kw in user_instruction.lower() for kw in global_keywords):
            print("   🚨 [拦截机制] 侦测到全局总结指令，强制将检索关键词重置为 'all'！")
            task_params["search_keywords"] = "all"
                
        print(f"👨‍🔬 [{self.name}] 最终决定执行技能: {skill_name}")
        
        try:
            # 这里去调用你刚刚贴出来的那段 execute_skill 核心干活代码
            result_msg = self.execute_skill(skill_name, task_params, shared_workspace)
            return {"status": "success", "message": result_msg}
        except Exception as e:
            import traceback
            print(f"❌ 技能执行失败: {e}")
            print(f"🔍 [详细错误] {traceback.format_exc()}")
            return {"status": "error", "message": str(e)}
