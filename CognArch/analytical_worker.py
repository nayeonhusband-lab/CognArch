import os
import shutil
import subprocess  # 用于调用外部工具如 antiword
import fitz  # PyMuPDF
from docx import Document
from openai import OpenAI
import re
import time
import glob
import json
from rapidocr_onnxruntime import RapidOCR # 新增：OCR 库
import pypandoc # 新增：Word 转换库
import yaml  # 新增：YAML 解析库
from base_worker import BaseWorker # ⬅️ 引入基类
import concurrent.futures # ⬅️ 添加到文件顶部的 import 区域
# 初始化 OCR 引擎 (全局只加载一次，速度更快)
ocr_engine = RapidOCR()
# ================= ⚙️ 配置从 config.py 导入 =================
from config import get_target_language, MAX_WORKERS, CHUNK_SIZE, OVERLAP, MAX_LENGTH
from config import get_api_key, get_base_url, get_reduce_model_name
from utils import find_antiword, make_relative  # 导入路径处理工具
from file_hash_cache import (
    build_hash_lookup,
    clear_force_rerun_for_missing_files,
    get_or_compute_file_hash,
    is_force_rerun,
    next_available_path,
    remove_hash_entry,
    unmark_force_rerun,
)
# ================= 🧠 动态逻辑核心 =================
def get_openai_client():
    """动态获取 OpenAI client，从配置文件读取"""
    api_key = get_api_key()
    base_url = get_base_url()
    return OpenAI(api_key=api_key, base_url=base_url)

client = get_openai_client()  # 保留全局 client 供向后兼容

def get_bundled_skills_path(skills_type):
    """获取打包后的技能目录路径"""
    import sys
    if getattr(sys, 'frozen', False):
        # 打包后的exe环境，从_MEIPASS获取打包资源路径
        base_path = sys._MEIPASS
        return os.path.join(base_path, 'skills', skills_type)
    return None
class RunnerWorker(BaseWorker):
    # 🌟 修改点 1：接收厂长分配的全局路径 (SYSTEM_PATHS)
    def __init__(self, system_paths: dict, msg_queue=None):
        super().__init__(
            name="RunnerWorker",
            description="数据工程师 / 原料车间。任何需要读取本地的 PDF 或 Word 生肉文件，使用 OCR 和文本解析提取内容，并调用底层分析技能(Analytical Skills)生成结构化的 Markdown 笔记和报告，这种任务都必须交给RunnerWorker。"
        )
        self.paths = system_paths # 保存全局路径
        self.msg_queue = msg_queue  # GUI消息队列
        self.setup_folders()
        self.registry = self.SkillRegistry(self.paths["skills_analytical"], "analytical")
    def send_gui_msg(self, msg: str, tag: str = 'info'):
        """发送消息到GUI日志"""
        if self.msg_queue:
            try:
                self.msg_queue.put((msg, tag))
            except:
                pass
    class SkillRegistry:
        """技能注册表：负责读取 skills 文件夹下的所有技能说明"""
        def __init__(self, skills_dir, skills_type="analytical"):
            self.skills_dir = skills_dir
            self.skills_type = skills_type
            # 懒加载：只存储元数据和文件路径，不加载完整内容
            # {filename: {name, description, file_path, _content_cache}}
            self.skills = {}
            self.load_skills()

        def _load_skill_content(self, filename):
            """懒加载：按需加载技能的完整内容"""
            if filename not in self.skills:
                return ""

            skill = self.skills[filename]

            # 如果已经有缓存，直接返回
            if '_content_cache' in skill:
                return skill['_content_cache']

            # 否则从文件加载
            file_path = skill.get('file_path', '')
            if not file_path or not os.path.exists(file_path):
                return skill.get('content', '')

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # 提取 prompt 内容
                role_def = ""
                if "# Instructions" in content:
                    parts = content.split("---")
                    if len(parts) >= 3:
                        role_def = parts[2].strip()
                    else:
                        role_def = content
                else:
                    role_def = content

                # 缓存内容
                skill['_content_cache'] = role_def if role_def.strip() else content
                return skill['_content_cache']
            except Exception as e:
                print(f"   ⚠️ 加载技能内容失败 {filename}: {e}")
                return skill.get('content', '')

        def load_skills(self):
            """扫描并加载所有 markdown 文件的 metadata (支持纯 YAML 格式) - 懒加载模式"""
            # 先尝试外部技能目录
            files = []
            external_dir = self.skills_dir
            if os.path.exists(external_dir):
                for root, dirs, filenames in os.walk(external_dir):
                    for filename in filenames:
                        if filename.endswith('.md'):
                            files.append(os.path.join(root, filename))
            # 如果外部目录为空，尝试从打包的技能加载
            if not files:
                bundled_dir = get_bundled_skills_path(self.skills_type)
                if bundled_dir and os.path.exists(bundled_dir):
                    for root, dirs, filenames in os.walk(bundled_dir):
                        for filename in filenames:
                            if filename.endswith('.md'):
                                files.append(os.path.join(root, filename))
                    if files:
                        print(f"📚 外部技能目录为空，从内置技能加载...")
            print(f"📚 正在加载分析类技能库 ({len(files)} 个技能)...")

            for file_path in files:
                filename = os.path.basename(file_path)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()

                        try:
                            # 1. 物理隔离：只截取第一个 --- 之前的纯 YAML 部分进行解析
                            yaml_text = content.split("---")[1] if "---" in content else content
                            skill_config = yaml.safe_load(yaml_text)
                            # 2. 提取给分拣员看的纯净 Description
                            name = skill_config.get("name", filename)
                            description = skill_config.get("description", "No description provided.")
                            # 3. 懒加载：只存储文件路径，不加载完整内容
                            self.skills[filename] = {
                                "name": name,
                                "description": description.strip(),
                                "file_path": file_path,
                                "interim_profile": skill_config.get("interim_profile")
                            }
                        except yaml.YAMLError as yaml_err:
                            print(f"   ⚠️ {filename} YAML解析失败: {yaml_err}")
                            # 退回保底读取模式
                            self.skills[filename] = {
                                "name": filename,
                                "description": "No description",
                                "file_path": file_path,
                                "content": content
                            }

                        print(f"   -> 已加载技能: {filename}")
                except Exception as e:
                    print(f"❌ 加载技能失败 {filename}: {e}")
        def get_router_prompt(self):
            """生成给分拣员看的'技能菜单'"""
            menu = ""
            for filename, data in self.skills.items():
                menu += f"- Skill File: '{filename}'\n"
                menu += f"  Function: {data['description']}\n\n"
            return menu
    def setup_folders(self):
        """初始化基础文件夹"""
        for p in self.paths.values():
            if not os.path.exists(p):
                os.makedirs(p)
    # 🌟 修改点 2：新增图书管理员的索引登记功能
    def _update_index(self, metadata: dict, filepath: str):
        # 将 index.json 存在 knowledge_base 的根目录下
        base_dir = os.path.dirname(self.paths["kb_notes"])
        index_path = os.path.join(base_dir, "index.json")
        index_data = []

        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
            except Exception:
                pass

        # 🌟 核心修改：将绝对路径转换为相对路径写入 JSON
        # 这样无论项目搬到哪里，只要文件在 CognArch 目录内就能找到
        relative_filepath = make_relative(filepath)

        metadata["filepath"] = relative_filepath
        metadata["add_time"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # 防止重复登记：如果路径已存在，先剔除旧记录（比较相对路径）
        index_data = [item for item in index_data if item.get("filepath") != relative_filepath]
        index_data.append(metadata)
        
        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
        print(f"   📇 [图书馆] 已将该笔记元数据登记入册: index.json")
        # 🌟 自动更新向量库（增量添加）
        try:
            import vector_store
            vector_store.add_documents([metadata])
        except Exception as e:
            print(f"   ⚠️ 向量库更新跳过: {e}")

    def rebuild_index_json(self) -> dict:
        """
        轻量重建 index.json：
        1. 扫描 knowledge_base/notes/ 下所有 .md 文件
        2. 加载现有 index.json
        3. 保留 filepath 仍指向存在文件的条目
        4. 移除文件已不存在的失效条目
        5. 报告 notes/ 中存在但 index.json 中缺失的新文件（仅报告，不自动处理）
        6. 写回清理后的 index.json
        """
        from utils import resolve_path, make_relative

        base_dir = os.path.dirname(self.paths["kb_notes"])
        index_path = os.path.join(base_dir, "index.json")

        if not os.path.exists(index_path):
            return {"status": "no_index", "kept": 0, "removed": 0, "orphan_files": 0, "removed_entries": [], "orphan_paths": []}

        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
        except Exception:
            return {"status": "corrupted", "kept": 0, "removed": 0, "orphan_files": 0, "removed_entries": [], "orphan_paths": []}

        valid_entries = []
        removed_entries = []
        for entry in index_data:
            filepath = entry.get("filepath", "")
            abs_path = resolve_path(filepath)
            if os.path.exists(abs_path):
                valid_entries.append(entry)
            else:
                removed_entries.append({"doc_id": entry.get("doc_id"), "filepath": filepath})

        indexed_paths = {entry.get("filepath", "") for entry in valid_entries}
        orphan_paths = []
        for root, _, files in os.walk(self.paths["kb_notes"]):
            for filename in files:
                if filename.lower().endswith(".md"):
                    abs_path = os.path.join(root, filename)
                    rel_path = make_relative(abs_path)
                    if rel_path not in indexed_paths:
                        orphan_paths.append(rel_path)

        if removed_entries:
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(valid_entries, f, ensure_ascii=False, indent=2)
            print(f"   📇 [图书馆] index.json 清理完成：保留 {len(valid_entries)} 条，移除 {len(removed_entries)} 条失效记录")
        else:
            print(f"   📇 [图书馆] index.json 健康：{len(valid_entries)} 条记录")

        return {
            "status": "cleaned" if removed_entries else "ok",
            "kept": len(valid_entries),
            "removed": len(removed_entries),
            "orphan_files": len(orphan_paths),
            "removed_entries": removed_entries,
            "orphan_paths": orphan_paths,
        }
    # ================= 🛡️ 核心解析与并发逻辑（原封不动） =================
    def read_file(self, file_path):
        try:
            if file_path.lower().endswith('.pdf'):
                doc = fitz.open(file_path)
                full_text = ""
                for page_num, page in enumerate(doc):
                    abs_page = page_num + 1
                    
                    text = page.get_text("text").strip()
                    
                    if len(text) < 50:
                        print(f"\n      👁️ [第{abs_page}页] 检测到扫描件，启动图像识别(OCR)...", end="")
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                        img_bytes = pix.tobytes("png")
                        
                        result, _ = ocr_engine(img_bytes)
                        if result:
                            text = "\n".join([line[1] for line in result])
                            print(" 完成！")
                        else:
                            print(" 识别失败(可能是纯白页)。")
                            text = "" 
                    
                    printed_page = page.get_label()
                    
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    header_context = " | ".join(lines[:4]) if lines else ""
                    footer_context = " | ".join(lines[-4:]) if lines else ""
                    
                    header_context = header_context.replace('<', '').replace('>', '')
                    footer_context = footer_context.replace('<', '').replace('>', '')
                    full_text += f"\n\n<page absolute=\"{abs_page}\">\n"
                    full_text += f"  <header_context>{header_context}</header_context>\n"
                    full_text += f"  <footer_context>{footer_context}</footer_context>\n"
                    full_text += f"</page>\n\n"
                    
                    full_text += text + "\n"
                    
                    full_text += f"\n\n<page_anchor absolute=\"{abs_page}\" printed=\"{printed_page}\" />\n\n"
                    full_text += text + "\n"
                    
                text = full_text
                
            elif file_path.lower().endswith('.docx'):
                text = ""
                try:
                    doc = Document(file_path)
                    # 🌟 添加段落编号标签，使 LLM 能知道段落位置
                    para_tags = []
                    for para_idx, para in enumerate(doc.paragraphs, start=1):
                        para_text = para.text.strip()
                        if para_text:
                            para_tags.append(f'<para id="{para_idx}">\n{para_text}\n</para>')
                    text = "\n".join(para_tags)
                    print(f"\n      📄 [RunnerWorker] 已为 DOCX 添加段落标签 (共 {len(para_tags)} 段)")
                except Exception as e:
                    print(f"\n      ⚠️ [RunnerWorker] 侦测到非标准/损坏的 DOCX 格式 ({e})。启动二重火力 pypandoc...")

                if not text.strip():
                    try:
                        text = pypandoc.convert_text(file_path, 'plain', format='docx')
                    except Exception as e2:
                        print(f"\n      ⚠️ [RunnerWorker] pypandoc 解析也失败了 ({e2})。启动终极火力 PyMuPDF...")

                if not text.strip():
                    try:
                        with fitz.open(file_path) as fallback_doc:
                            for page_idx, page in enumerate(fallback_doc, start=1):
                                page_text = page.get_text().strip()
                                if page_text:
                                    text += f'<page id="{page_idx}">\n{page_text}\n</page>\n'
                    except Exception as e3:
                        print(f"\n      ❌ [RunnerWorker] 终极抢救失败。该文件已被彻底加密或损坏，无法读取！")
                        text = ""

            elif file_path.lower().endswith('.doc'):
                # 🌟 专门处理旧版 .doc 格式 (OLE Compound File)
                text = ""
                try:
                    antiword_path = find_antiword()

                    if antiword_path:
                        result = subprocess.run([antiword_path, file_path], capture_output=True)
                        if result.returncode == 0 and result.stdout:
                            raw_text = result.stdout.decode('utf-8', errors='ignore')
                            # 按空行分段，模拟段落结构
                            paragraphs = [p.strip() for p in raw_text.split('\n\n') if p.strip()]
                            para_tags = []
                            for para_idx, para in enumerate(paragraphs, start=1):
                                para_tags.append(f'<para id="{para_idx}">\n{para}\n</para>')
                            text = "\n".join(para_tags)
                            print(f"\n      📄 [RunnerWorker] 已为 DOC (旧版) 添加段落标签 (共 {len(para_tags)} 段)")
                        else:
                            raise Exception(f"antiword exit code: {result.returncode}")
                    else:
                        raise Exception("antiword not found; install it on PATH or set COGNARCH_ANTIWORD")
                except Exception as e:
                    print(f"\n      ⚠️ [RunnerWorker] antiword 读取 DOC 失败 ({e})。尝试 PyMuPDF...")

                if not text.strip():
                    try:
                        # 尝试用 PyMuPDF 读取（某些 .doc 可以被识别）
                        with fitz.open(file_path) as fallback_doc:
                            for page_idx, page in enumerate(fallback_doc, start=1):
                                page_text = page.get_text().strip()
                                if page_text:
                                    text += f'<page id="{page_idx}">\n{page_text}\n</page>\n'
                        if text.strip():
                            print(f"\n      📄 [RunnerWorker] PyMuPDF 成功读取 DOC (按页模式)")
                    except Exception as e2:
                        print(f"\n      ❌ [RunnerWorker] 无法读取该 DOC 文件: {e2}")
                        text = ""
                        
            elif file_path.lower().endswith(('.md', '.txt', '.markdown')):
                # 处理文本文件（interim 文件或普通文本）
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        raw_text = f.read()

                    # 🌟 为 MD 文件添加行号标签，支持行级锚点引用
                    if file_path.lower().endswith('.md'):
                        lines = raw_text.split('\n')
                        # 🌟 阈值机制：超过100行的MD文件不添加行号标签，直接使用原笔记
                        MAX_MD_LINES = 100
                        if len(lines) <= MAX_MD_LINES:
                            tagged_lines = []
                            for line_idx, line in enumerate(lines, start=1):
                                # 即使空行也保留，保持行号对应关系
                                tagged_lines.append(f'<line id="{line_idx}">{line}</line>')
                            text = '\n'.join(tagged_lines)
                            print(f"\n      📄 [RunnerWorker] 已为 MD 文件添加行号标签 (共 {len(tagged_lines)} 行)")
                        else:
                            # 超过阈值，保持原样（使用Global引用）
                            text = raw_text
                            print(f"\n      📄 [RunnerWorker] MD 文件行数超过 {MAX_MD_LINES} 行 ({len(lines)} 行)，使用原笔记模式")
                    else:
                        # 普通文本文件保持原样
                        text = raw_text
                except Exception as e:
                    print(f"\n      ⚠️ 读取文本文件失败: {e}")
                    text = ""

            # 1. 完美处理 .pptx
            elif file_path.lower().endswith('.pptx'):
                try:
                    from pptx import Presentation
                    prs = Presentation(file_path)
                    slide_tags = []

                    for slide_num, slide in enumerate(prs.slides, start=1):
                        texts = []
                        for shape in slide.shapes:
                            if hasattr(shape, "text") and shape.text.strip():
                                texts.append(shape.text.strip())
                            elif shape.has_table:
                                for row in shape.table.rows:
                                    row_data = [cell.text_frame.text.replace('\n', ' ').strip() for cell in row.cells]
                                    texts.append(" | ".join(filter(None, row_data)))

                        if texts:
                            slide_content = "\n".join(texts)
                            slide_tags.append(f'<slide id="{slide_num}">\n{slide_content}\n</slide>')

                    text = "\n\n".join(slide_tags)
                    print(f"\n      📊 [RunnerWorker] 已为 PPTX 提取结构化标签 (共 {len(slide_tags)} 张幻灯片)")
                except Exception as e:
                    import traceback
                    print(f"\n      ⚠️ [RunnerWorker] PPTX 解析失败: {e}\n{traceback.format_exc()}")
                    text = ""

            # 3. 完美处理 .xlsx (使用 openpyxl 只读模式，内存优化)
            elif file_path.lower().endswith('.xlsx'):
                try:
                    from openpyxl import load_workbook
                    # 使用 read_only 模式实现流式读取，内存优化
                    wb = load_workbook(file_path, read_only=True, data_only=True)
                    sheet_tags = []

                    for sheet_name in wb.sheetnames:
                        ws = wb[sheet_name]
                        sheet_content = []
                        row_count = 0

                        # 流式读取每一行
                        for row in ws.iter_rows(values_only=True):
                            # 过滤空行
                            if row and any(cell is not None and str(cell).strip() for cell in row):
                                # 将每行转换为 Markdown 表格格式
                                row_data = [str(cell) if cell is not None else "" for cell in row]
                                sheet_content.append("| " + " | ".join(row_data) + " |")
                                row_count += 1

                        if sheet_content:
                            # 添加表头（第一行作为表头）
                            header = sheet_content[0] if sheet_content else ""
                            body = "\n".join(sheet_content[1:]) if len(sheet_content) > 1 else ""
                            sheet_tags.append(f'<sheet name="{sheet_name}" rows="{row_count}">\n{header}\n{body}\n</sheet>')

                    text = "\n\n".join(sheet_tags)
                    wb.close()
                    print(f"\n      📊 [RunnerWorker] 已为 Excel 提取结构化标签 (共 {len(sheet_tags)} 个工作表)")
                except Exception as e:
                    import traceback
                    print(f"\n      ⚠️ [RunnerWorker] Excel 解析失败: {e}\n{traceback.format_exc()}")
                    text = ""

            # 4. 优雅拦截并拒绝旧版 .xls
            elif file_path.lower().endswith('.xls'):
                error_msg = f"⚠️ [RunnerWorker] 暂不支持旧版 .xls 格式，请将 {os.path.basename(file_path)} 在 Office 或 WPS 中另存为 .xlsx 后重新上传。"
                print(f"\n      {error_msg}")
                text = f"[{os.path.basename(file_path)} 读取失败：不支持的旧版 xls 格式，请用户转换为 xlsx]"

            # 5. 优雅拦截并拒绝旧版 .ppt
            elif file_path.lower().endswith('.ppt'):
                error_msg = f"⚠️ [RunnerWorker] 暂不支持旧版 .ppt 格式，请将 {os.path.basename(file_path)} 在 Office 中另存为 .pptx 后重新上传。"
                print(f"\n      {error_msg}")
                text = f"[{os.path.basename(file_path)} 读取失败：不支持的旧版 ppt 格式，请用户转换为 pptx]"

            else:
                return ""
            return re.sub(r'\n{3,}', '\n\n', text)
        except Exception as e:
            print(f"❌ 读取文件失败: {e}")
            return ""

    def _filter_appendix_content(self, text):
        """过滤 Appendix、Notes、Bibliography 等非正文内容"""
        # 常见的附录标记（按优先级排序）
        appendix_patterns = [
            r'#{1,4}\s+(Appendix|APPENDIX)\s*[A-Z]?[\s\n]',
            r'\n(Appendix|APPENDIX)\s+[A-Z]?\s*[\n\r]',
            r'#{1,4}\s+(Notes|NOTES)\s*[\n\r]',
            r'#{1,4}\s+(Bibliography|BIBLIOGRAPHY)\s*[\n\r]',
            r'#{1,4}\s+(References|REFERENCES)\s*[\n\r]',
            r'#{1,4}\s+(Index|INDEX)\s*[\n\r]',
            r'#{1,4}\s+(Acknowledgments?|ACKNOWLEDGMENTS?)\s*[\n\r]',
            r'#{1,4}\s+(Footnotes|FOOTNOTES)\s*[\n\r]',
        ]

        # 找到第一个附录标记的位置
        first_appendix_pos = len(text)
        for pattern in appendix_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                first_appendix_pos = min(first_appendix_pos, match.start())

        if first_appendix_pos < len(text):
            print(f"   ✂️ 检测到附录/参考文献内容，截断至位置 {first_appendix_pos} ({first_appendix_pos/len(text)*100:.1f}%)")
            return text[:first_appendix_pos]

        return text

    def _get_skill_interim_profile(self, skill_id):
        if not skill_id or not hasattr(self, "registry"):
            return None
        skill = self.registry.skills.get(skill_id, {})
        return skill.get("interim_profile")

    def _get_interim_context_path(self, interim_path):
        base, _ = os.path.splitext(interim_path)
        return f"{base}.context.json"

    def _write_interim_context(self, interim_path, context):
        if not interim_path:
            return
        context_path = self._get_interim_context_path(interim_path)
        try:
            with open(context_path, "w", encoding="utf-8") as f:
                json.dump(context, f, ensure_ascii=False, indent=2)
            print(f"   🧭 已保存 interim 上下文: {context_path}")
        except Exception as e:
            print(f"   ⚠️ interim 上下文保存失败: {e}")

    def _infer_interim_profile_from_text(self, text):
        if len(self._extract_visible_interim_chapters(text)) >= 2:
            return "book_chapter"
        if re.search(r'"document_type"\s*:\s*"[^"]*(Monograph|Book)[^"]*"', text or "", re.IGNORECASE):
            return "book_chapter"
        if re.search(r'"doc_id"\s*:\s*"[^"]*_BOOK"', text or "", re.IGNORECASE):
            return "book_chapter"
        return "generic"

    def _load_interim_context(self, interim_path, text):
        context_path = self._get_interim_context_path(interim_path)
        context = {}
        if os.path.exists(context_path):
            try:
                with open(context_path, "r", encoding="utf-8") as f:
                    context = json.load(f)
                print(f"   🧭 读取 interim 上下文: {context_path}")
            except Exception as e:
                print(f"   ⚠️ interim 上下文读取失败，启用回退识别: {e}")

        if not context.get("interim_profile") and context.get("origin_skill_id"):
            context["interim_profile"] = self._get_skill_interim_profile(context.get("origin_skill_id"))

        if not context.get("interim_profile"):
            context["interim_profile"] = self._infer_interim_profile_from_text(text)
            context["profile_source"] = "fallback_inference"
        return context

    def _build_source_ctx(self, file_path=None, source_ctx=None):
        ctx = dict(source_ctx or {})
        if file_path and not ctx.get("original_input_path"):
            abs_path = os.path.abspath(file_path)
            ctx["original_input_path"] = abs_path
            ctx["original_input_ext"] = os.path.splitext(abs_path)[1]
            ctx["force_rerun"] = bool(ctx.get("force_rerun")) or is_force_rerun(abs_path)
            if os.path.exists(abs_path):
                ctx["source_hash"] = ctx.get("source_hash") or get_or_compute_file_hash(abs_path)

        if ctx.get("original_input_path"):
            ctx["original_input_path"] = os.path.abspath(ctx["original_input_path"])
        if not ctx.get("original_input_ext") and ctx.get("original_input_path"):
            ctx["original_input_ext"] = os.path.splitext(ctx["original_input_path"])[1]
        ctx.setdefault("original_input_ext", ".pdf")
        ctx.setdefault("force_rerun", False)
        return ctx

    def _normalize_original_base_name(self, filename):
        original_base_name = os.path.splitext(filename)[0]
        if original_base_name.startswith("interim_"):
            original_base_name = original_base_name[len("interim_"):]

        while True:
            new_name = re.sub(r'^\[[A-Z]{2}\]_', '', original_base_name)
            if new_name == original_base_name:
                break
            original_base_name = new_name

        original_base_name = re.sub(r'^_+', '', original_base_name)
        original_base_name = re.sub(r'_+', '_', original_base_name)
        return original_base_name

    def _archive_original_input(self, source_ctx, final_base_name, processed_lookup=None):
        source_path = os.path.abspath(source_ctx.get("original_input_path") or "") if source_ctx else ""
        original_input_ext = (source_ctx or {}).get("original_input_ext") or (os.path.splitext(source_path)[1] if source_path else ".pdf")
        force_rerun = bool((source_ctx or {}).get("force_rerun"))

        if not source_path or not os.path.exists(source_path):
            if source_path:
                unmark_force_rerun(source_path)
            return {
                "status": "missing",
                "source_path": source_path,
                "target_path": "",
            }

        source_hash = (source_ctx or {}).get("source_hash") or get_or_compute_file_hash(source_path)
        if source_ctx is not None:
            source_ctx["source_hash"] = source_hash

        if processed_lookup is None:
            processed_lookup = build_hash_lookup(self.paths["processed"], recursive=False)

        existing_entries = processed_lookup.get(source_hash, []) if source_hash else []
        if existing_entries and not force_rerun:
            existing_path = existing_entries[0].get("path", "")
            try:
                os.remove(source_path)
            except OSError:
                pass
            remove_hash_entry(source_path)
            unmark_force_rerun(source_path)
            return {
                "status": "deduped_existing",
                "source_path": source_path,
                "target_path": existing_path,
            }

        raw_folder = self.paths["processed"]
        os.makedirs(raw_folder, exist_ok=True)
        if force_rerun:
            suffix = time.strftime("%Y%m%d_%H%M%S")
            target_filename = f"{final_base_name}__rerun_{suffix}{original_input_ext}"
        else:
            target_filename = f"{final_base_name}{original_input_ext}"
        target_path = next_available_path(os.path.join(raw_folder, target_filename))

        shutil.move(source_path, target_path)
        remove_hash_entry(source_path)
        target_hash = get_or_compute_file_hash(target_path) or source_hash
        if target_hash:
            processed_lookup.setdefault(target_hash, []).append({
                "path": target_path,
                "name": os.path.basename(target_path),
                "sha256": target_hash,
            })
        unmark_force_rerun(source_path)
        return {
            "status": "force_rerun_archived" if force_rerun else "moved",
            "source_path": source_path,
            "target_path": target_path,
        }

    def _finalize_note_output(self, filename, result, md_folder, source_ctx, shared_workspace=None):
        original_base_name = self._normalize_original_base_name(filename)
        new_core_name = original_base_name

        meta_dict = {}
        json_match_extract = re.search(r'```json\s*(\{.*?\})\s*```', result, re.DOTALL)
        if json_match_extract:
            try:
                meta_dict = json.loads(json_match_extract.group(1))
            except Exception:
                pass

        extracted_doc_id = ""
        if isinstance(meta_dict, dict):
            extracted_doc_id = str(meta_dict.get("doc_id", "")).strip()
        if not extracted_doc_id:
            doc_id_match = re.search(r'"doc_id"\s*:\s*"([^"]+)"', result, re.IGNORECASE)
            if doc_id_match and doc_id_match.group(1):
                extracted_doc_id = doc_id_match.group(1).strip()

        raw_name = original_base_name
        used_doc_id_for_name = False
        if extracted_doc_id:
            raw_name = extracted_doc_id
            used_doc_id_for_name = True

        safe_name = re.sub(r'[\\/*?:""<>|\n\r]', '', raw_name).strip()[:150]
        if safe_name and safe_name != original_base_name:
            new_core_name = safe_name
            if used_doc_id_for_name:
                print(f"   🎆 使用 doc_id 命名: {new_core_name}")
            else:
                print(f"   🎆 成功提取到智能文件名: {new_core_name}")
        else:
            print(f"   ⚠️ 未提取到有效的新文件名，使用原名: {new_core_name}")

        final_base_name = new_core_name
        if isinstance(meta_dict, dict) and final_base_name:
            meta_dict["doc_id"] = final_base_name
            normalized_paths = meta_dict.get("paths", {})
            if not isinstance(normalized_paths, dict):
                normalized_paths = {}
            normalized_paths["md_note"] = f"knowledge_base/notes/{final_base_name}.md"
            original_ext_for_meta = source_ctx.get("original_input_ext") if source_ctx else ""
            if not original_ext_for_meta:
                original_ext_for_meta = os.path.splitext(filename)[1] or ".pdf"
            normalized_paths["original_file"] = f"{final_base_name}{original_ext_for_meta}"
            meta_dict["paths"] = normalized_paths

            if json_match_extract:
                updated_json_block = "```json\n" + json.dumps(meta_dict, ensure_ascii=False, indent=2) + "\n```"
                result = result[:json_match_extract.start()] + updated_json_block + result[json_match_extract.end():]
        md_path = os.path.join(md_folder, f"{final_base_name}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(result)

        msg = f"   ✅ 数据底座已储备至图书馆 -> {md_path}"
        print(msg)
        self.send_gui_msg(msg, 'success')
        self._update_index(meta_dict, md_path)

        processed_lookup = shared_workspace.get("_runner_processed_hash_lookup") if shared_workspace else None
        archive_result = self._archive_original_input(source_ctx, final_base_name, processed_lookup=processed_lookup)
        status = archive_result.get("status")
        target_path = archive_result.get("target_path", "")
        source_path = archive_result.get("source_path", "")

        if status == "moved":
            msg = f"   📦 原始文件已归档至: {target_path}"
            print(msg)
            self.send_gui_msg(msg, 'success')
        elif status == "force_rerun_archived":
            msg = f"   📦 强制重跑源文件已归档至: {target_path}"
            print(msg)
            self.send_gui_msg(msg, 'success')
        elif status == "deduped_existing":
            msg = f"   ℹ️ 检测到 processed 中已有同内容原始文件，已去重: {target_path}"
            print(msg)
            self.send_gui_msg(msg, 'info')
        else:
            msg = f"   ⚠️ 原始文件不存在或已被处理，跳过归档: {source_path}"
            print(msg)
            self.send_gui_msg(msg, 'warning')

        return md_path

    def _load_origin_skill_prompt_for_interim(self, origin_skill_id, user_instruction):
        if origin_skill_id and hasattr(self, "registry") and origin_skill_id in self.registry.skills:
            system_prompt = self.registry._load_skill_content(origin_skill_id)
            system_prompt = system_prompt.replace("{{language}}", get_target_language())
            instruction_to_inject = user_instruction if user_instruction else "None"
            return system_prompt.replace("{{user_instruction}}", instruction_to_inject)

        return f"""You are a structural document synthesizer.
You must output a fenced JSON metadata block followed by a <content_layer>.
Preserve the source document's visible heading hierarchy and do NOT convert it into a book/chapter structure.
Use `document_type`: "Structured Document" unless the input metadata clearly indicates a better type.
Every factual claim should preserve existing citation anchors.
Output language: {get_target_language()}."""

    def _get_structural_interim_guidance(self, interim_profile):
        if interim_profile == "whitepaper_recursive":
            return """Profile: WHITEPAPER / REPORT
- Preserve the original TOC reconstruction hierarchy (`H1`, `H2`, `H3` and deeper headings).
- At leaf nodes, preserve the exact Trifecta skeleton: `Heading Definition`, `Core Claim`, and `Logical Unfolding`.
- Do NOT create `Chapter N` headings and do NOT use `Book Overview`.
- Final reduce should conform to the original whitepaper skill template, especially Structural Logic Pyramid / TOC Reconstruction."""

        if interim_profile == "gov_recursive":
            return """Profile: PRIMARY GOVERNMENT DOCUMENT
- Preserve the government document's recursive H1/Hn structure.
- Preserve `Macro Framework Map` and `Dynamic Logical Deconstruction`.
- Structural nodes should keep `Core Idea` and `Logical Breakdown`; atomic nodes should keep `Upward Relationship`, `Core Idea`, and `Paragraph Synthesis`.
- Do NOT create `Chapter N` headings and do NOT use `Book Overview`.
- Final reduce should conform to the original government document skill template."""

        return """Profile: GENERIC STRUCTURED DOCUMENT
- Preserve visible headings and the source note's existing structure.
- If stable headings exist, keep them; if not, use a small number of thematic sections.
- Do NOT create a book/chapter structure unless the input itself clearly uses chapters."""

    def _process_structural_interim_text(self, filename, text, interim_profile, origin_skill_id, user_instruction, shared_workspace):
        print(f"   🧭 [Interim Profile] 使用结构继承模式: {interim_profile}")
        text = self._filter_appendix_content(text)
        if len(text) > MAX_LENGTH:
            raw_chunks = self.split_interim_dynamic_packing(
                text,
                max_chunk_size=1300000,
                max_raw_blocks_per_bin=12
            )
            chunks = [self._prepare_interim_chunk_for_map(chunk) for chunk in raw_chunks]
        else:
            chunks = [self._prepare_interim_chunk_for_map(text)]

        print(f"   📊 [结构型切分统计] 文本总长: {len(text)} 字符, 生成 {len(chunks)} 个 chunks")
        chunk_sizes = [f"#{i}:{len(c)}字" for i, c in enumerate(chunks[:5])]
        suffix = "..." if len(chunks) > 5 else ""
        print(f"      Chunk 分布: {', '.join(chunk_sizes)}{suffix}")

        origin_system_prompt = self._load_origin_skill_prompt_for_interim(origin_skill_id, user_instruction)
        profile_guidance = self._get_structural_interim_guidance(interim_profile)
        checkpoint_session_dir = shared_workspace.get("session_dir") if shared_workspace else None
        checkpoint_dir = self._get_checkpoint_dir(checkpoint_session_dir, filename) if checkpoint_session_dir else None

        interim_map_system_prompt = f"""You are an Interim Structural Condenser.
Your job is to refine a chunk of already-generated interim notes while preserving the original analytical skill's structure.

CRITICAL: This is NOT a book/chapter task. Do not convert the document into `Chapter N` unless the source skill and source headings already require it.

{profile_guidance}

Map-stage output rules:
1. Do NOT output JSON metadata in map chunks.
2. Preserve visible headings and heading depth from the input summaries.
3. Preserve the original skill's required per-heading skeleton and anchor/citation style.
4. Compress repeated text, but do not skip any explicit heading or leaf node.
5. Output only Markdown fragments that can be merged later.
6. Output entirely in {get_target_language()}.

=== ORIGINAL SKILL FRAMEWORK TO PRESERVE ===
{origin_system_prompt}
"""

        def fetch_structural_chunk(i, chunk_text):
            user_prompt = f"""Refine this interim chunk into a compact structural fragment.

Before summarizing, produce a short STRUCTURE_INVENTORY listing visible top-level headings in this chunk.
Then output the condensed content using the original skill's hierarchy and skeleton.

TEXT CHUNK (cleaned for map only; on-disk interim file is unchanged):
{chunk_text}"""

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    res = get_openai_client().chat.completions.create(
                        model="deepseek-v4-flash",
                        messages=[
                            {"role": "system", "content": interim_map_system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.3,
                        timeout=180
                    )
                    content = res.choices[0].message.content
                    if checkpoint_dir:
                        self._save_checkpoint(checkpoint_session_dir, filename, i, content, len(chunks))
                    return i, content
                except Exception:
                    if attempt < max_retries - 1:
                        time.sleep((attempt + 1) * 2)
                    else:
                        return i, None
            return i, None

        unordered_results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(fetch_structural_chunk, i, chunk) for i, chunk in enumerate(chunks)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    idx, content = future.result()
                    if content:
                        unordered_results[idx] = content
                        headings = re.findall(r'(?m)^#{2,6}\s+(.+)$', content)
                        status = f"结构标题 {len(headings)} 个" if headings else "结构片段"
                        print(f"      ✅ Chunk {idx} 完成，检测到: {status}")
                    else:
                        print(f"      ⚠️ Chunk {idx} 返回空结果")
                except Exception as e:
                    import traceback
                    print(f"      ❌ 获取结果失败: {e}")
                    print(f"      📋 详细错误: {traceback.format_exc()}")

        chunk_results = [unordered_results[i] for i in sorted(unordered_results.keys())]
        if len(chunk_results) != len(chunks):
            missing = set(range(len(chunks))) - set(unordered_results.keys())
            print(f"   ⚠️ [完整性警告] 预期 {len(chunks)} 个 chunks, 实际获得 {len(chunk_results)} 个")
            print(f"      缺失 chunks: {sorted(missing)}")
        else:
            print(f"   ✅ [完整性检查] 所有 {len(chunks)} 个 chunks 已处理")

        if not chunk_results:
            return None

        combined_text = "\n\n=== NEXT STRUCTURAL CHUNK SUMMARY ===\n\n".join(chunk_results)
        reduce_system_prompt = f"""You are a Master Structural Document Synthesizer.
Reconstruct ONE final note from interim structural fragments.

CRITICAL: The final output MUST conform to the original analytical skill's JSON + Markdown output format.
Do NOT convert the document into a book/chapter note unless the original skill requires it.

{profile_guidance}

Merge rules:
1. Preserve the original skill's top-level sections, heading hierarchy, and per-heading skeleton.
2. Remove duplicated headings and overlapping summaries.
3. Do not omit any explicit heading or leaf node present in the interim fragments.
4. Preserve anchors/citations.
5. Output entirely in {get_target_language()}.

=== ORIGINAL SKILL FRAMEWORK & OUTPUT TEMPLATE ===
{origin_system_prompt}
"""
        reduce_user_prompt = f"Merge these interim structural fragments into the final note:\n\n{combined_text}"
        reduce_messages = [
            {"role": "system", "content": reduce_system_prompt},
            {"role": "user", "content": reduce_user_prompt}
        ]

        final_raw_text = ""
        try:
            while True:
                response = client.chat.completions.create(
                    model=get_reduce_model_name("analytical"),
                    messages=reduce_messages,
                    extra_body={"thinking": {"type": "enabled"}},
                    reasoning_effort="max",
                    timeout=180
                )
                piece = response.choices[0].message.content
                final_raw_text += piece

                if response.choices[0].finish_reason == 'length':
                    print("   ⏳ [自动续写] Reasoner 触发单次输出极值，正在继续结构型 interim...")
                    reduce_messages.append({"role": "assistant", "content": piece})
                    reduce_messages.append({"role": "user", "content": "Continue exactly from where you stopped. Do not repeat previous text, do not add conversational commentary, and keep the original skill's structure."})
                else:
                    break

            result = re.sub(r'<think>.*?</think>', '', final_raw_text, flags=re.DOTALL).strip()
            print("   ✅ [结构型 Reduce] 缝合完成！")
            print(f"   📝 [结构型 Reduce 输出] 最终文本长度: {len(result)} 字符")
            return result
        except Exception as e:
            import traceback
            print(f"   ❌ 结构型 Reduce 请求失败: {e}")
            print(f"   📋 详细错误: {traceback.format_exc()}")
            return None

    def split_text_with_overlap(self, text, chunk_size=1300000, overlap=5000):
        chunks = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(start + chunk_size, text_len)
            chunks.append(text[start:end])
            if end == text_len:
                break
            start += chunk_size - overlap
        return chunks

    def _extract_content_layer_body(self, text):
        match = re.search(r'<content_layer>(.*?)</content_layer>', text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text.strip()

    def _prepare_interim_chunk_for_map(self, chunk_text):
        separator = "=== NEXT CHUNK EXTRACTION ==="
        raw_chunks = [c.strip() for c in chunk_text.split(separator) if c.strip()]
        cleaned_chunks = []

        for raw_chunk in raw_chunks:
            cleaned_chunk = re.sub(r'```json\s*\{.*?\}\s*```', '', raw_chunk, flags=re.DOTALL | re.IGNORECASE)
            cleaned_chunk = self._extract_content_layer_body(cleaned_chunk)
            cleaned_chunk = re.sub(r'\n{3,}', '\n\n', cleaned_chunk).strip()
            if cleaned_chunk:
                cleaned_chunks.append(cleaned_chunk)

        return f"\n\n{separator}\n\n".join(cleaned_chunks).strip()

    def _extract_visible_interim_chapters(self, text):
        return sorted({
            int(num)
            for num in re.findall(r'(?im)^#{2,4}\s+Chapter\s+(\d+)\s*:', text or "")
        })

    def _get_missing_visible_chapters(self, source_text, output_text):
        visible_input_chapters = self._extract_visible_interim_chapters(source_text)
        if not visible_input_chapters:
            return []
        visible_output_chapters = set(self._extract_visible_interim_chapters(output_text))
        return [chapter for chapter in visible_input_chapters if chapter not in visible_output_chapters]

    def _clean_generated_interim_sections(self, text):
        cleaned = re.sub(r'<think>.*?</think>', '', text or "", flags=re.DOTALL).strip()
        cleaned = re.sub(r'^\s*```(?:markdown)?\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*```\s*$', '', cleaned, flags=re.IGNORECASE)
        cleaned = self._extract_content_layer_body(cleaned)
        heading_match = re.search(
            r'(?im)^#{2,4}\s+(?:Chapter\s+\d+\s*:|Front Matter\b|接续\b)',
            cleaned
        )
        if heading_match:
            cleaned = cleaned[heading_match.start():]
        return cleaned.strip()

    def _split_numbered_chapter_sections(self, text):
        if not text:
            return []
        matches = list(re.finditer(r'(?im)^#{2,4}\s+Chapter\s+(\d+)\s*:.*$', text))
        sections = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            section_text = text[start:end].strip()
            if section_text:
                sections.append((int(match.group(1)), section_text))
        return sections

    def _insert_numbered_sections(self, existing_text, repair_sections):
        repair_blocks = self._split_numbered_chapter_sections(repair_sections)
        if not repair_blocks:
            return existing_text.strip() if existing_text else repair_sections.strip()

        merged_text = (existing_text or "").strip()
        for chapter_num, section_text in sorted(repair_blocks, key=lambda item: item[0]):
            if re.search(rf'(?im)^#{{2,4}}\s+Chapter\s+{chapter_num}\s*:', merged_text):
                continue

            insert_matches = list(re.finditer(r'(?im)^#{2,4}\s+Chapter\s+(\d+)\s*:', merged_text))
            insert_at = len(merged_text)
            for match in insert_matches:
                try:
                    existing_num = int(match.group(1))
                except ValueError:
                    continue
                if existing_num > chapter_num:
                    insert_at = match.start()
                    break

            if not merged_text:
                merged_text = section_text.strip()
            elif insert_at >= len(merged_text):
                merged_text = f"{merged_text.rstrip()}\n\n{section_text.strip()}"
            else:
                before = merged_text[:insert_at].rstrip()
                after = merged_text[insert_at:].lstrip()
                merged_text = f"{before}\n\n{section_text.strip()}\n\n{after}"

        return merged_text.strip()

    def _insert_sections_into_content_layer(self, full_text, repair_sections):
        body_match = re.search(r'(<content_layer>)(.*?)(</content_layer>)', full_text, re.DOTALL | re.IGNORECASE)
        if not body_match:
            return self._insert_numbered_sections(full_text, repair_sections)

        merged_body = self._insert_numbered_sections(body_match.group(2), repair_sections)
        return f"{full_text[:body_match.start(2)]}{merged_body}{full_text[body_match.end(2):]}"

    def _repair_interim_map_output_if_needed(self, chunk_text, content):
        missing_chapters = self._get_missing_visible_chapters(chunk_text, content)
        if not missing_chapters:
            return content, []
        applied_missing = []

        repair_system_prompt = f"""You are repairing a condensed chaptered-document chunk summary.

The first pass missed numbered chapters that are clearly visible in the source chunk.
Return ONLY the missing numbered chapter sections in Markdown.

**Rules**
1. Output ONLY these missing chapters: {missing_chapters}
2. Use exact headings: `### Chapter N: [Title]`
3. Use `### Front Matter` ONLY for true pre-chapter material such as table of contents, preface, or introductory material before the numbered chapters begin
4. Use `### 接续，同属于上一个章节锚点` ONLY for genuine continuation of a chapter already in progress, never for table of contents or front matter
5. Use only natural paragraphs, no bullet points
6. 1-2 natural paragraphs per missing chapter is enough
7. Preserve citations like `([Doc_id, xxxx])`
8. Output entirely in {get_target_language()}

Do not repeat existing chapters.
Do not include a chapter inventory.
Do not use code fences.
Do not add commentary."""

        repair_user_prompt = f"""VISIBLE INPUT CHAPTER HEADERS: {self._extract_visible_interim_chapters(chunk_text)}
VISIBLE OUTPUT CHAPTER HEADERS: {self._extract_visible_interim_chapters(content)}
MISSING CHAPTERS TO ADD: {missing_chapters}

SOURCE CHUNK:
{chunk_text}

FIRST PASS OUTPUT:
{content}"""

        try:
            res = get_openai_client().chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": repair_system_prompt},
                    {"role": "user", "content": repair_user_prompt}
                ],
                temperature=0.2,
                timeout=180
            )
            repair_content = self._clean_generated_interim_sections(res.choices[0].message.content)
            if repair_content:
                content = self._insert_numbered_sections(content, repair_content)
                applied_missing = missing_chapters
        except Exception as e:
            print(f"      ⚠️ Chunk repair 失败，保留首次结果: {e}")

        return content, applied_missing

    def _repair_interim_reduce_output_if_needed(self, result, combined_text, expected_chapters):
        if not result or not expected_chapters:
            return result, []

        output_chapters = self._extract_visible_interim_chapters(result)
        missing_chapters = [chapter for chapter in expected_chapters if chapter not in output_chapters]
        if not missing_chapters:
            return result, []
        applied_missing = []

        repair_system_prompt = f"""You are repairing a reconstructed chaptered-document note.

The current final note is missing numbered chapters that are explicitly present in the chunk summaries.
Return ONLY the missing numbered chapter sections in the final note style.

**Rules**
1. Output ONLY these chapters: {missing_chapters}
2. Use exact headings: `### Chapter N: [Title]`
3. Do NOT output JSON metadata
4. Do NOT repeat the document overview
5. Do NOT rewrite existing chapters
6. Use only natural paragraphs, no bullet points
7. Preserve evidence from the chunk summaries and end claims with `[[MD: X]]`
8. Output entirely in {get_target_language()}

Do not use code fences.
Do not add commentary."""

        repair_user_prompt = f"""CURRENT FINAL NOTE IS MISSING CHAPTERS: {missing_chapters}

Return ONLY the missing numbered chapter sections.

SOURCE CHUNK SUMMARIES:
{combined_text}

CURRENT FINAL NOTE:
{result}"""

        try:
            res = get_openai_client().chat.completions.create(
                model=get_reduce_model_name("analytical"),
                messages=[
                    {"role": "system", "content": repair_system_prompt},
                    {"role": "user", "content": repair_user_prompt}
                ],
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max",
                timeout=180
            )
            repair_content = self._clean_generated_interim_sections(res.choices[0].message.content)
            if repair_content:
                result = self._insert_sections_into_content_layer(result, repair_content)
                applied_missing = missing_chapters
        except Exception as e:
            print(f"   ⚠️ [Reduce Repair] 补章失败，保留首次 Reduce 结果: {e}")

        return result, applied_missing

    def split_interim_dynamic_packing(self, text, max_chunk_size=1300000, max_raw_blocks_per_bin=12):
        """
        🌟 Interim 专用动态装箱切分：
        1. 先按原始 chunk 边界（=== NEXT CHUNK EXTRACTION ===）分割
        2. 使用装箱算法合并小 chunks，但不超过 max_chunk_size
        3. 如果某个原始 chunk 太大，再按需切分
        """
        separator = "=== NEXT CHUNK EXTRACTION ==="

        # 先按原始 chunk 边界分割
        raw_chunks = text.split(separator)
        raw_chunks = [c.strip() for c in raw_chunks if c.strip()]

        result_chunks = []
        current_bin = []
        current_bin_size = 0

        for raw_chunk in raw_chunks:
            chunk_len = len(raw_chunk)

            if chunk_len > max_chunk_size:
                # 如果当前有积累的内容，先保存
                if current_bin:
                    result_chunks.append(f"\n\n{separator}\n\n".join(current_bin))
                    current_bin = []
                    current_bin_size = 0

                # 原始 chunk 太大，需要进一步切分
                print(f"      ⚠️ 原始 chunk 过大 ({chunk_len} 字)，进一步切分...")
                sub_chunks = self.split_text_with_overlap(raw_chunk, chunk_size=max_chunk_size, overlap=5000)
                result_chunks.extend(sub_chunks)

            elif current_bin_size + chunk_len <= max_chunk_size and len(current_bin) < max_raw_blocks_per_bin:
                # 可以装入当前箱子
                current_bin.append(raw_chunk)
                current_bin_size += chunk_len

            else:
                # 当前箱子已满，保存并开新箱子
                result_chunks.append(f"\n\n{separator}\n\n".join(current_bin))
                current_bin = [raw_chunk]
                current_bin_size = chunk_len

        # 处理最后一个箱子
        if current_bin:
            result_chunks.append(f"\n\n{separator}\n\n".join(current_bin))

        return result_chunks
    def identify_best_skill(self, text):
        print("   ↳ 正在匹配最佳技能...", end="", flush=True)
        
        skills_menu = self.registry.get_router_prompt()
        
        # ================= 🌟 新增：动态跳读切片逻辑 =================
        total_length = len(text)
        
        if total_length <= 6000:
            # 如果总字数不足6000字，直接全量读取，不作裁剪
            document_snippet = text
        else:
            # 1. 取开头 2000 字
            start_snippet = text[:2000]
            
            # 2. 取中间 2000 字
            mid_start = (total_length // 2) - 1000
            mid_end = mid_start + 2000
            middle_snippet = text[mid_start:mid_end]
            
            # 3. 取结尾 2000 字
            end_snippet = text[-2000:]
            
            # 缝合并加入明显的截断提示，防止大模型产生连贯性幻觉
            document_snippet = (
                f"{start_snippet}\n\n"
                f"...... [中部内容省略] ......\n\n"
                f"{middle_snippet}\n\n"
                f"...... [尾部内容省略] ......\n\n"
                f"{end_snippet}"
            )
        # ==============================================================
        ROUTER_PROMPT = f"""
        You are an Intelligent Document Router.
        Your task is to select the BEST Skill to analyze a given document.
        === AVAILABLE SKILLS ===
        {skills_menu}
        
        === USER DOCUMENT SNIPPET ===
        {document_snippet}
        === INSTRUCTION ===
        Analyze the document's content, structure, and intent. Match it against the 'Function' descriptions of the available skills.
        
        You MUST output your response strictly as a JSON object. Do NOT output any markdown blocks or other text.
        Format requirement:
        {{
            "thought_process": "1. 归纳输入文本的核心特征。 2. 对比各个技能的 [STRICT TRIGGER]。 3. 【强制检查】必须明确声明该文本是否触犯了目标技能的 [CRITICAL EXCLUSION 1/2/3]，如果触犯，必须立刻排除该技能并重新考虑选择其他",
            "skill_id": "The exact 'Skill File' name  you selected (e.g., 'gov.md')."
        }}
        """
        
        try:
            response = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": "You are a precise classifier."},
                    {"role": "user", "content": ROUTER_PROMPT}
                ],
                response_format={"type": "json_object"}, # ⬅️ 核心改动：强制输出 JSON
                extra_body={"thinking": {"type": "enabled"}},
                reasoning_effort="max",
                stream=False,
                timeout=120
            )
            
            # 解析大模型返回的 JSON
            result_dict = json.loads(response.choices[0].message.content)
            raw_choice = result_dict.get("skill_id", "OTHER")
            
            # ⬅️ 保留你原本极其严谨的校验逻辑：对比 registry
            matched_skill = "OTHER"
            for skill_name in self.registry.skills.keys():
                if skill_name in raw_choice:
                    matched_skill = skill_name
                    break
            
            # 将校验后真实存在的合法 ID 写回字典，然后返回整个字典
            result_dict["skill_id"] = matched_skill
            return result_dict
                
        except Exception as e:
            print(f"(匹配失败: {e})")
            return {"thought_process": f"匹配失败或解析异常: {str(e)}", "skill_id": "OTHER"}

    # 🔍 调试计数器
    _call_count = 0

    def process_file(self, file_path, user_instruction="", shared_workspace=None, source_ctx=None):
        # 🔍 调试：追踪调用次数
        RunnerWorker._call_count += 1
        # 🔍 调试计数器已移除

        import re as regex_module  # 用于调试日志中的章节检测

        input_path = file_path
        filename = os.path.basename(file_path)

        is_interim = filename.startswith("interim_")
        if is_interim:
            source_ctx = self._build_source_ctx(source_ctx=source_ctx)
        else:
            source_ctx = self._build_source_ctx(file_path=input_path, source_ctx=source_ctx)
        original_input_path = source_ctx.get("original_input_path")
        original_input_ext = source_ctx.get("original_input_ext", os.path.splitext(filename)[1])

        msg = f"\n📄 正在分析: {filename}"
        print(msg)
        self.send_gui_msg(msg, 'info')
        text = self.read_file(input_path)
        if not text:
            msg = "   ⚠️ 无法提取文本，跳过。"
            print(msg)
            self.send_gui_msg(msg, 'warning')
            return None
        if is_interim:
            print(f"   🔄 检测到 interim 文件 ({len(text)} 字)，启用专用精炼-重组流程...")
            interim_context = self._load_interim_context(input_path, text)
            interim_profile = interim_context.get("interim_profile") or "generic"
            origin_skill_id = interim_context.get("origin_skill_id")
            if interim_context.get("original_input_ext"):
                original_input_ext = interim_context.get("original_input_ext")
                source_ctx["original_input_ext"] = original_input_ext
            print(f"   🧭 [Interim Profile] {interim_profile} (origin: {origin_skill_id or 'unknown'})")

            # Interim 文件跳过重新路由，按来源 skill profile 选择专用 prompts
            md_folder = self.paths["kb_notes"]
            text_length = len(text)
            result = None
            chunk_results = None
            handled_by_structural_interim = interim_profile != "book_chapter"

            if handled_by_structural_interim:
                result = self._process_structural_interim_text(
                    filename=filename,
                    text=text,
                    interim_profile=interim_profile,
                    origin_skill_id=origin_skill_id,
                    user_instruction=user_instruction,
                    shared_workspace=shared_workspace
                )

            # 对于 interim 文件：
            # - 如果 <= MAX_LENGTH：直接 Reduce
            # - 如果 > MAX_LENGTH：仍需分块处理（但可以复用现有 MapReduce 逻辑）
            if handled_by_structural_interim:
                pass
            elif text_length <= MAX_LENGTH:
                print("   🧩 [Reduce] 启动 Reasoner 直接缝合 (interim 模式)...")
                chunk_results = [text]
            else:
                print("   📏 Interim 仍超限，启用动态装箱切分...")
                # 🌟 过滤附录/参考文献内容
                text = self._filter_appendix_content(text)
                # 🌟 使用动态装箱切分：优先保持原始 chunk 边界，小 chunk 合并，大 chunk 拆分
                raw_chunks = self.split_interim_dynamic_packing(
                    text,
                    max_chunk_size=1300000,
                    max_raw_blocks_per_bin=12
                )
                chunks = [self._prepare_interim_chunk_for_map(chunk) for chunk in raw_chunks]

                # 📊 切分验证日志
                print(f"   📊 [切分统计] 文本总长: {len(text)} 字符, 生成 {len(chunks)} 个 chunks")
                chunk_sizes = [f"#{i}:{len(c)}字" for i, c in enumerate(chunks[:5])]
                suffix = "..." if len(chunks) > 5 else ""
                print(f"      Chunk 分布: {', '.join(chunk_sizes)}{suffix}")

                # 🔄 断点续传：加载已完成的 checkpoint
                unordered_results = {}
                checkpoint_session_dir = shared_workspace.get("session_dir") if shared_workspace else None
                if checkpoint_session_dir:
                    completed_chunks = self._load_checkpoint(checkpoint_session_dir, filename)
                    if completed_chunks:
                        reusable_chunks = {}
                        invalid_chunks = []
                        for idx, content in completed_chunks.items():
                            if idx >= len(chunks):
                                continue
                            missing_visible = self._get_missing_visible_chapters(chunks[idx], content)
                            if missing_visible:
                                invalid_chunks.append((idx, missing_visible))
                                continue
                            reusable_chunks[idx] = content

                        if reusable_chunks:
                            print(f"   🔄 [断点续传] 复用 {len(reusable_chunks)} 个已完成且通过章节校验的 chunks")
                            unordered_results.update(reusable_chunks)
                        if invalid_chunks:
                            print(f"   ♻️ [断点校验] {len(invalid_chunks)} 个旧 checkpoint 检测到漏章，将重新计算")
                            for idx, missing_visible in invalid_chunks[:5]:
                                print(f"      Chunk {idx}: 缺失可见章节 {missing_visible}")

                checkpoint_dir = None
                if checkpoint_session_dir:
                    checkpoint_dir = self._get_checkpoint_dir(checkpoint_session_dir, filename)

                # 🌟 Interim 专用：精炼 System Prompt（不降维分析，只做压缩保留）
                interim_map_system_prompt = f"""You are an expert Document Condenser. Your CRITICAL mission is to analyze a chunk of a chaptered document and EXTRACT ALL CHAPTERS present in it.

**🎯 PRIMARY OBJECTIVE - CHAPTER EXTRACTION (MANDATORY):**

Before writing ANY summary, you MUST perform a COMPLETE SCAN of the entire input text and identify EVERY chapter present.
The chunk may contain repeated boundaries, front matter, or repeated metadata already stripped for clarity. Do NOT let that distract you from early chapters.

**STEP 1: COMPLETE CHAPTER SCAN (DO NOT SKIP)**
- Read the ENTIRE text from start to finish
- Identify ALL chapter markers:
  * English: `Chapter X`, `CHAPTER X`, `Ch. X`, `Book X`, `Part X`
  * Chinese: `第X章`, `第一章`, `第1章`, `一、`, `1. `, `1 `
- List EVERY chapter number you find (e.g., "Found: Chapters 1, 2, 3, 7")
- **CRITICAL**: Chapters often appear in the MIDDLE or END of a chunk - you MUST scan the WHOLE text

**STEP 2: MENTALLY MAP CHAPTER BOUNDARIES**
For each chapter found, determine:
- Where does it START in the text?
- Where does it END (or continue to the end of chunk)?
- What content belongs to each chapter?

**STEP 3: OUTPUT FORMAT (STRICT)**

**Case A - Chunk contains Chapter X at ANY position:**
```markdown
### Chapter [N]: [Extracted Title]

[Natural paragraph summary of ONLY the content belonging to Chapter N in this chunk...]
```
- Output ONE `### Chapter [N]: Title` block for EACH chapter found
- Convert Chinese numbers: "第一章" → "Chapter 1"
- Include summary of ONLY that chapter's content present in this chunk

**Case B - Content BEFORE the first Chapter marker (front matter):**
```markdown
### Front Matter

[Summary of front matter, table of contents, or introductory material...]
```

**Case C - Genuine continuation content without a new numbered chapter header:**
```markdown
### 接续，同属于上一个章节锚点

[Summary of content that genuinely continues the most recent numbered chapter...]
```
- Use this ONLY when the content is a real continuation of a chapter already in progress
- NEVER use this for table of contents, preface, or other pre-chapter material

**Case D - Content BETWEEN chapters (if any):**
- If there's content between Chapter X and Chapter Y, assign it to the most appropriate nearby chapter
- Do not skip it just because it is transitional or repeated

**STEP 4: MULTIPLE CHAPTERS IN ONE CHUNK (VERY COMMON)**
If you find Chapter 3, Chapter 4, AND Chapter 5 in the same chunk:
```markdown
### Chapter 3: [Title]

[Summary of Chapter 3 content from this chunk...]

### Chapter 4: [Title]

[Summary of Chapter 4 content from this chunk...]

### Chapter 5: [Title]

[Summary of Chapter 5 content from this chunk...]
```

**CONTENT RULES:**
1. Use ONLY natural paragraphs (NO bullet points)
2. EVERY factual claim MUST cite: `([Doc_id, xxxx])`
3. Prioritize complete chapter coverage over aggressive compression
4. 1-2 natural paragraphs per chapter is enough when the chunk contains many chapters
5. Preserve quantitative data and unique examples

**⚠️ COMMON FAILURE MODES TO AVOID:**
1. ❌ DON'T only check the beginning - chapters appear THROUGHOUT the text
2. ❌ DON'T skip chapters because they seem "short" - include ALL found chapters
3. ❌ DON'T merge multiple chapters into one - output separate headers for each
4. ❌ DON'T ignore chapter markers in the middle or end of the text
5. ❌ DON'T start from a later chapter just because the chunk begins with front matter, repeated boundaries, or repeated metadata

**EXAMPLE OF CORRECT BEHAVIOR:**
Input chunk contains:
- Lines 1-100: Chapter 1 content
- Lines 101: "### Chapter 2: New Topic"
- Lines 101-500: Chapter 2 content
- Lines 501: "### Chapter 7: Another Topic"
- Lines 501-1000: Chapter 7 content

Your output MUST be:
```markdown
### Chapter 1: [Title]

[Summary of lines 1-100...]

### Chapter 2: New Topic

[Summary of lines 101-500...]

### Chapter 7: Another Topic

[Summary of lines 501-1000...]
```

Output language: {get_target_language()}"""

                def fetch_chunk(i, chunk_text):
                    # 子线程中完全禁用 print，避免 Streamlit NoSessionContext 错误
                    # 所有日志在主线程中统一输出
                    user_prompt = f"""FIRST - SCAN AND REPORT: Before writing any summaries, you MUST identify ALL chapters in this chunk.

**REQUIRED: Start your response with a CHAPTER INVENTORY list:**
```
CHAPTERS_DETECTED: [List every chapter number found, e.g., "1, 2, 3, 7" or "None"]
CHAPTER_POSITIONS:
- Chapter X: starts at line/position Y, ends at Z
- Chapter Y: starts at...
```

THEN proceed with the content summaries using the formats specified in your instructions.
If the chunk begins with table of contents, preface, or other pre-chapter material, label it `### Front Matter`.
If the chunk already visibly contains `### Chapter N:` headings, do NOT start from a later chapter.

Output language must be: {get_target_language()}

TEXT CHUNK (cleaned for map only; on-disk interim file is unchanged):
{chunk_text}"""

                    # 🔄 重试机制：最多重试 3 次
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            res = get_openai_client().chat.completions.create(
                                model="deepseek-v4-flash",
                                messages=[
                                    {"role": "system", "content": interim_map_system_prompt},
                                    {"role": "user", "content": user_prompt}
                                ],
                                temperature=0.3,
                                timeout=180
                            )
                            content = res.choices[0].message.content
                            content, repaired_missing = self._repair_interim_map_output_if_needed(chunk_text, content)

                            # 💾 保存 checkpoint
                            if checkpoint_dir:
                                self._save_checkpoint(checkpoint_session_dir, filename, i, content, len(chunks))

                            return i, content, repaired_missing
                        except Exception as e:
                            if attempt < max_retries - 1:
                                wait_time = (attempt + 1) * 2
                                # 子线程中不能使用print，避免Streamlit NoSessionContext错误
                                # 错误信息将在主线程中处理
                                time.sleep(wait_time)
                            else:
                                # 子线程中不能使用print，返回错误信息让主线程处理
                                return i, None, []
                    return i, None, []

                with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    # 只提交未完成的 chunks
                    futures = []
                    for i, chunk in enumerate(chunks):
                        if i not in unordered_results:
                            futures.append(executor.submit(fetch_chunk, i, chunk))
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            idx, content, repaired_missing = future.result()
                            if content:
                                unordered_results[idx] = content
                                # 调试：显示 chunk 处理结果预览（检测整个内容）
                                chapter_headers = regex_module.findall(r'#{2,4}\s+Chapter\s+(\d+)', content, regex_module.IGNORECASE)
                                continuation = regex_module.findall(r'#{2,4}\s+接续', content)
                                if chapter_headers:
                                    unique_chapters = sorted(set([int(c) for c in chapter_headers]))
                                    status = f"Chapters {unique_chapters}"
                                elif continuation:
                                    status = f"接续标记 ({len(continuation)}个)"
                                else:
                                    status = "(无章节标记)"
                                if repaired_missing:
                                    status = f"{status} | Repair补回: {sorted(repaired_missing)}"
                                print(f"      ✅ Chunk {idx} 完成，检测到: {status}")
                            else:
                                print(f"      ⚠️ Chunk {idx} 返回空结果")
                        except Exception as e:
                            import traceback
                            tb_str = traceback.format_exc()
                            print(f"      ❌ 获取结果失败: {e}")
                            print(f"      📋 详细错误: {tb_str}")

                chunk_results = [unordered_results[i] for i in sorted(unordered_results.keys())]

                # ✅ 完整性检查：验证所有 chunks 都已处理
                expected_chunk_count = len(chunks)
                actual_chunk_count = len(chunk_results)
                if actual_chunk_count != expected_chunk_count:
                    missing = set(range(expected_chunk_count)) - set(unordered_results.keys())
                    print(f"   ⚠️ [完整性警告] 预期 {expected_chunk_count} 个 chunks, 实际获得 {actual_chunk_count} 个")
                    print(f"      缺失 chunks: {sorted(missing)}")
                else:
                    print(f"   ✅ [完整性检查] 所有 {expected_chunk_count} 个 chunks 已处理")

                # 执行 Reduce（Interim 专用：章节重组 + Metadata 生成）
                if chunk_results:
                    print("   🧩 [Reduce] 正在启动 Interim 章节重组与 Metadata 生成...")
                    print(f"   📊 [输入统计] 共 {len(chunk_results)} 个精炼块待重组")

                    # 先分析有哪些章节（多模式匹配）
                    all_summaries_text = "\n".join(chunk_results)
                    found_chapters = set()
                    # regex_module 已在函数开始处导入

                    # 调试：打印每个 chunk 的章节检测情况
                    print("   🔍 [Reduce 输入审计] 各 chunk 章节检测:")
                    for idx, chunk_text in enumerate(chunk_results):
                        preview = chunk_text[:150].replace('\n', ' ')
                        # 检测新的章节格式: ### Chapter X: Title (整个 chunk)
                        chapter_headers = regex_module.findall(r'#{2,4}\s+Chapter\s+(\d+)', chunk_text, regex_module.IGNORECASE)
                        # 检测 "接续" 标记
                        continuation_markers = regex_module.findall(r'#{2,4}\s+接续', chunk_text)
                        if chapter_headers:
                            unique_chapters = sorted(set([int(c) for c in chapter_headers]))
                            chapter_str = f"Chapters {unique_chapters}"
                        else:
                            chapter_str = "(无章节)"
                        cont_str = f", 接续: {len(continuation_markers)}个" if continuation_markers else ""
                        print(f"      Chunk {idx}: {chapter_str}{cont_str}")
                        print(f"         预览: {preview}...")

                    # 多模式章节检测 (更新为新的格式)
                    chapter_patterns = [
                        # 新格式: ### Chapter X: Title
                        (r'#{2,4}\s+Chapter\s+(\d+)', lambda m: int(m.group(1))),
                        # 旧格式兼容: CHAPTER X (大写无Markdown)
                        (r'CHAPTER\s+(\d+)', lambda m: int(m.group(1))),
                    ]

                    for pattern, extractor in chapter_patterns:
                        for match in regex_module.finditer(pattern, all_summaries_text, regex_module.IGNORECASE):
                            try:
                                chapter_num = extractor(match)
                                if chapter_num and chapter_num > 0:
                                    found_chapters.add(chapter_num)
                            except:
                                pass

                    # 检测 "接续" 标记数量
                    continuation_count = len(regex_module.findall(r'#{2,4}\s+接续', all_summaries_text))
                    front_matter_count = len(regex_module.findall(r'#{2,4}\s+Front Matter', all_summaries_text, regex_module.IGNORECASE))

                    print(f"   📚 [检测到章节] {sorted(found_chapters) if found_chapters else '未检测到标准章节号'}")
                    if continuation_count > 0:
                        print(f"   📝 [检测到接续标记] {continuation_count} 个待合并章节片段")
                    if front_matter_count > 0:
                        print(f"   📑 [检测到 Front Matter] {front_matter_count} 个")

                    all_chapters_list = sorted(found_chapters) if found_chapters else []
                    expected_chapters_str = f"Chapters {min(all_chapters_list)} through {max(all_chapters_list)}" if all_chapters_list else "Unknown (detect from input)"

                    reduce_system_prompt = f"""You are a Master Document Architect. Your CRITICAL mission is to reconstruct a COMPLETE chaptered document by intelligently merging chapter fragments from multiple chunks.

**INPUT ANALYSIS:**
- Input contains {len(chunk_results)} condensed chunk summaries from the same chaptered document
- Pre-detected numeric chapters: {all_chapters_list if all_chapters_list else 'None'}
- Expected chapters to output: {expected_chapters_str}
- Continuation markers detected: {continuation_count} sections marked "接续，同属于上一个章节锚点"
- Front Matter markers detected: {front_matter_count} sections marked "Front Matter"

**🎯 UNDERSTANDING THE CHAPTER MARKERS:**

Input uses FOUR formats - you MUST understand what each means:

1. **`### Chapter [N]: [Title]`**
   - This chunk contains content belonging to Chapter N
   - The SAME Chapter N can appear in MULTIPLE chunks (due to overlap)
   - You must MERGE all instances of the same chapter

2. **`### Front Matter`**
   - This is pre-chapter material such as table of contents, preface, or introductory framing
   - It belongs in the document overview / introductory area
   - It is NOT a numbered chapter

3. **`### 接续，同属于上一个章节锚点`**
   - This content is a CONTINUATION of whatever chapter came before it
   - It has NO chapter number of its own
   - Assign it to the most recently mentioned chapter number

4. **Plain text (no header)**
   - Also belongs to the preceding chapter
   - Include in the chapter above it

**📋 YOUR TASK - 4 PHASE RECONSTRUCTION:**

**PHASE 1: INVENTORY ALL CHAPTER FRAGMENTS**

Step 1: Go through ALL {len(chunk_results)} chunks SEQUENTIALLY
Step 2: For each chunk, list ALL chapter headers found:
   - Record: Chunk 0 → Chapters [1, 2, 3]
   - Record: Chunk 1 → Chapters [7, 8, 9]
   - Record `Front Matter` and continuation sections too

Step 3: Identify the explicit numbered chapter sequence already present in the input summaries.

**PHASE 2: CONTENT MAPPING (CRITICAL)**

For each explicitly detected chapter number:
1. Find ALL chunks that mention this chapter
2. Extract content from each occurrence
3. Handle overlaps (same content in multiple chunks → use once)
4. Merge into coherent narrative

Example - Chapter 3 reconstruction:
```
Chunk 0 ends with: "Chapter 3 starts here..." [content cut off]
Chunk 1 starts with: "### 接续，同属于上一个章节锚点"
                      [content continues Chapter 3]
                      "### Chapter 7: New Chapter"
Result: Chapter 3 = (end of Chunk 0) + (beginning of Chunk 1 until next chapter)
```

**PHASE 3: RECONSTRUCTION RULES**

1. **Reconstruct every explicitly detected numbered chapter**
2. **Merge overlaps**: Same paragraph appearing twice = include once
3. **"接续" assignment**: Content under "接续" belongs to the chapter IMMEDIATELY BEFORE it
4. **"Front Matter" assignment**: Absorb it into the document overview / introductory area, never into a fake numbered chapter
5. **Flowing narrative**: Combine fragments into smooth, continuous paragraphs
6. **Preserve citations**: Keep ALL `([Doc_id, xxxx])` anchors

**PHASE 4: COMPLETENESS VERIFICATION**

Before output, CHECK:
- [ ] Have I included ALL explicitly detected chapter numbers from the input?
- [ ] Did I include content from ALL {len(chunk_results)} input chunks?
- [ ] Are there any "接续" sections I haven't assigned to a chapter?
- [ ] Did I absorb all `Front Matter` into the document overview / introductory area?

**OUTPUT STRUCTURE**

You MUST output in this EXACT structure:

```markdown
```json
{{
  "doc_id": "[Author]_[Year]_[ShortTitle]_[BOOK]",
  "title": "[Full title]",
  "authors": ["Author 1", "Author 2"],
  "publish_year": 2020,
  "publication_information": "[Publisher / issuing organization]",
  "document_type": "Chaptered Book / Edited Volume / Manual / Handbook / Treatise / Guide",
  "primary_field": "[Subject Domain]",
  "tags": ["Topic1", "DocumentGenre", "KeyConcept"],
  "core_claim": "[50-70 words: Document Scope | Claim_Type | [Anchor] - Information]",
  "file_format": "pdf",
  "page_count": [number],
  "paths": {{
    "md_note": "knowledge_base/notes/[doc_id].md",
    "original_file": "[original_filename]"
  }},
  "add_time": "2026-04-10T00:00:00Z"
}}
```

<content_layer>

## 📚 [Document Overview & Chapter Architecture]

The document's organizing purpose can be stated as follows: [One sentence capturing what the chaptered work is trying to explain, argue, instruct, document, or compile. If no unified thesis exists, describe the document's scope rather than inventing one.] [[MD: Global]]

The document is structured into **[N chapters]**, organized according to **[chronological/thematic/procedural/reference/etc.]**. [2-3 paragraphs describing the chapter architecture and how chapters relate where that relationship is explicit.] [[MD: Global]]

The chapters primarily rely upon **[evidence/procedure/example/reference types]**. [2-3 paragraphs describing the basis of the content without forcing an overarching central thesis.] [[MD: Global]]

## 📖 [Chapter-by-Chapter Analysis]

### Chapter 1: [Title]

[5-7 natural paragraphs summarizing Chapter 1. Include: core topic/function, key arguments/procedures/evidence/examples, and internal development. Every claim ends with [[MD: X]] anchor.]

### Chapter 2: [Title]

[Same format...]

[Continue for ALL chapters - DO NOT skip any!]

</content_layer>
```

**CRITICAL RULES - DO NOT VIOLATE:**
1. **COMPLETENESS**: Include ALL explicitly detected numbered chapters in ascending order. If output limit reached, STOP and wait for continuation.
2. **MERGE "接续" CONTENT**: Any section marked `### 接续，同属于上一个章节锚点` MUST be merged with the previous chapter.
3. **MERGE "Front Matter" CONTENT**: Any section marked `### Front Matter` belongs in the document overview / introduction, not as a numbered chapter.
4. **NO BULLET POINTS**: Use ONLY natural paragraphs. Never use `* **` format.
5. **CITATION**: EVERY claim MUST cite: `([Doc_id, xxxx])` or `[[MD: X]]`.
6. **LANGUAGE**: Output entirely in {get_target_language()}.
7. **NO HALLUCINATION**: Do NOT invent chapters not present in input.

**AFTER RECONSTRUCTION - VERIFY:**
- Count chapters in output: Every explicitly detected chapter number should be present
- Check: Did you merge ALL "接续" sections?
- Check: Did you absorb all `Front Matter` into the document overview / introduction?
- Check: Are there ANY bullet points? (If yes, convert to paragraphs)
"""

                    combined_text = "\n\n=== NEXT CHUNK SUMMARY ===\n\n".join(chunk_results)
                    reduce_user_prompt = f"Reconstruct the complete document from these condensed chapter summaries. Ensure ALL chapters are included and properly organized:\n\n{combined_text}"
                    reduce_messages = [
                        {"role": "system", "content": reduce_system_prompt},
                        {"role": "user", "content": reduce_user_prompt}
                    ]
                    final_raw_text = ""
                    try:
                        while True:
                            response = client.chat.completions.create(
                                model=get_reduce_model_name("analytical"),
                                messages=reduce_messages,
                                extra_body={"thinking": {"type": "enabled"}},
                                reasoning_effort="max",
                                timeout=180
                            )
                            piece = response.choices[0].message.content
                            final_raw_text += piece

                            if response.choices[0].finish_reason == 'length':
                                print("   ⏳ [自动续写] Pro 触发单次输出极值，正在继续...")
                                # 检查最后输出了哪些章节
                                last_chapters = regex_module.findall(r'(?:#{2,4}\s+)?Chapter\s+(\d+)', piece, regex_module.IGNORECASE)
                                if last_chapters:
                                    completed = sorted(set([int(c) for c in last_chapters]))
                                    print(f"      [已输出章节] {completed}")
                                    # 检测缺失章节
                                    if completed:
                                        all_expected = list(range(1, max(completed) + 2))  # 包含下一个可能的章节
                                        missing = [c for c in all_expected if c not in completed]
                                        if missing:
                                            print(f"      [待续写章节] {missing[:3]}...")
                                reduce_messages.append({"role": "assistant", "content": piece})
                                reduce_messages.append({"role": "user", "content": f"You hit the output length limit. You MUST CONTINUE from exactly where you stopped.\n\nCRITICAL: Review what you've written. Look for the LAST ### Chapter [N]: [Title] heading you completed.\nThen continue with the NEXT chapter in sequence.\n\nYour previous output contained: Chapters {sorted(set([int(c) for c in last_chapters])) if last_chapters else 'unknown'}.\n\nIf Chapter X is incomplete, continue it.\nIf Chapter X is complete, start Chapter X+1 immediately with: ### Chapter [X+1]: [Extract title from input chunks]\n\nNO conversational intro, NO apologies, NO summaries - just continue the Markdown content immediately."})
                            else:
                                break

                        result = re.sub(r'<think>.*?</think>', '', final_raw_text, flags=re.DOTALL).strip()
                        print("   ✅ [Reduce] 缝合完成！")

                        # 调试：分析 Reduce 输出
                        output_chapters = regex_module.findall(r'(?:#{2,4}\s+)?Chapter\s+(\d+)', result, regex_module.IGNORECASE)
                        if output_chapters:
                            chapter_nums = sorted(set([int(c) for c in output_chapters]))
                            print(f"   📚 [Reduce 输出] 包含章节: {chapter_nums}")
                            # 检测缺失章节
                            if chapter_nums:
                                expected = list(range(min(chapter_nums), max(chapter_nums) + 1))
                                missing = [c for c in expected if c not in chapter_nums]
                                empty_chapters = regex_module.findall(r'### Chapter (\d+): \[标题\]\s*\n\s*\*本章内容在当前提供的输入摘要中缺失\*', result)
                                if missing:
                                    print(f"   ⚠️ [Reduce 输出] 缺失章节: {missing}")
                                elif empty_chapters:
                                    print(f"   ⚠️ [Reduce 输出] 空章节标记: {empty_chapters}")
                                else:
                                    print(f"   ✅ [Reduce 输出] 章节连续完整")
                        # 检查内容完整性
                        expected_chapters = sorted(found_chapters) if found_chapters else []
                        if expected_chapters:
                            output_set = set([int(c) for c in output_chapters]) if output_chapters else set()
                            expected_set = set(expected_chapters)
                            not_output = expected_set - output_set
                            extra_output = output_set - expected_set
                            if not_output:
                                print(f"   ❌ [Reduce 严重警告] 未输出应有的章节: {sorted(not_output)}")
                                print(f"   🔧 [Reduce Repair] 尝试补回缺失章节: {sorted(not_output)}")
                                result, repaired_reduce_chapters = self._repair_interim_reduce_output_if_needed(
                                    result,
                                    combined_text,
                                    expected_chapters
                                )
                                if repaired_reduce_chapters:
                                    output_chapters = regex_module.findall(r'(?:#{2,4}\s+)?Chapter\s+(\d+)', result, regex_module.IGNORECASE)
                                    repaired_output_set = set([int(c) for c in output_chapters]) if output_chapters else set()
                                    still_missing = sorted(expected_set - repaired_output_set)
                                    if still_missing:
                                        print(f"   ⚠️ [Reduce Repair] 仍缺失章节: {still_missing}")
                                    else:
                                        print(f"   ✅ [Reduce Repair] 已补齐显式章节")
                            if extra_output:
                                print(f"   ⚠️ [Reduce 警告] 输出了输入中未检测到的章节: {sorted(extra_output)}")
                        print(f"   📝 [Reduce 输出] 最终文本长度: {len(result)} 字符")
                    except Exception as e:
                        import traceback
                        print(f"   ❌ Reduce 请求失败: {e}")
                        tb_str = traceback.format_exc()
                        print(f"   📋 详细错误: {tb_str}")
                        import sys
                        sys.stdout.flush()

            # 🌟 Interim 流程的归档处理（与非interim流程共享相同的归档逻辑）
            if result:
                return self._finalize_note_output(filename, result, md_folder, source_ctx, shared_workspace)
            else:
                msg = f"   ⚠️ 分析失败或无结果，原文件保留在 inputs 中，等待下次重试。"
                print(msg)
                self.send_gui_msg(msg, 'warning')
                return None

        else:
            # 🌟 非 interim 文件：走原有 skill 匹配流程（保持原样）
            routing_decision = self.identify_best_skill(text)
            best_match = routing_decision.get("skill_id", "OTHER")
            thought_process = routing_decision.get("thought_process", "无思考记录")

            if shared_workspace and "session_dir" in shared_workspace:
                self.log_routing_decision(
                    session_dir=shared_workspace["session_dir"],
                    target_name=filename,
                    skill_id=best_match,
                    reason=thought_process
                )

            if best_match != "OTHER":
                print(f"   🎯 匹配到技能框架: {best_match}")

                md_folder = self.paths["kb_notes"]

                system_prompt = self.registry._load_skill_content(best_match)
                system_prompt = system_prompt.replace("{{language}}", get_target_language())

                instruction_to_inject = user_instruction if user_instruction else "None"
                system_prompt = system_prompt.replace("{{user_instruction}}", instruction_to_inject)
                text_length = len(text)
                result = None

                if text_length <= MAX_LENGTH:
                    print(f"   🧠 文档长度适中 ({text_length} 字)，启动 Reasoner 深度解构...")
                    user_prompt = f"Please process the following text based on your system instructions. Output language must be: {get_target_language()}\n\nTEXT:\n{text}"
                    try:
                        response = client.chat.completions.create(
                            model=get_reduce_model_name("analytical"),
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}
                            ],
                            extra_body={"thinking": {"type": "enabled"}},
                            reasoning_effort="max",
                            timeout=180
                        )
                        raw_result = response.choices[0].message.content
                        result = re.sub(r'<think>.*?</think>', '', raw_result, flags=re.DOTALL).strip()
                    except Exception as e:
                        import traceback
                        tb_str = traceback.format_exc()
                        print(f"   ❌ API 请求失败: {e}")
                        print(f"   📋 详细错误: {tb_str}")
                        # 确保输出不被截断
                        import sys
                        sys.stdout.flush()

                else:
                    print(f"   📏 触发长文本【并发】处理引擎 | 总字数: {text_length} 字，将切割处理。")
                    chunks = self.split_text_with_overlap(text, chunk_size=CHUNK_SIZE, overlap=OVERLAP)

                    # 🔄 断点续传：加载已完成的 checkpoint
                    unordered_results = {}
                    checkpoint_session_dir = shared_workspace.get("session_dir") if shared_workspace else None
                    if checkpoint_session_dir:
                        completed_chunks = self._load_checkpoint(checkpoint_session_dir, filename)
                        if completed_chunks:
                            print(f"   🔄 [断点续传] 发现 {len(completed_chunks)} 个已完成的 chunks")
                            unordered_results.update(completed_chunks)

                    # 获取 checkpoint 目录用于保存
                    checkpoint_dir = None
                    if checkpoint_session_dir:
                        checkpoint_dir = self._get_checkpoint_dir(checkpoint_session_dir, filename)

                    def fetch_chunk(i, chunk_text):
                        # 子线程中完全禁用 print，避免 Streamlit NoSessionContext 错误
                        # 所有日志在主线程中统一输出
                        user_prompt = f"Please process the following text CHUNK based on your system instructions. Output language must be: {get_target_language()}\n\nTEXT CHUNK:\n{chunk_text}"

                        # 🔄 重试机制：最多重试 3 次
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                res = get_openai_client().chat.completions.create(
                                    model="deepseek-v4-flash",
                                    messages=[
                                        {"role": "system", "content": system_prompt},
                                        {"role": "user", "content": user_prompt}
                                    ],
                                    temperature=0.3,
                                    timeout=180
                                )
                                content = res.choices[0].message.content

                                # 💾 保存 checkpoint
                                if checkpoint_dir:
                                    self._save_checkpoint(checkpoint_session_dir, filename, i, content, len(chunks))

                                return i, content
                            except Exception as e:
                                if attempt < max_retries - 1:
                                    wait_time = (attempt + 1) * 2  # 2, 4, 6秒
                                    # 子线程中不能使用print，避免Streamlit NoSessionContext错误
                                    time.sleep(wait_time)
                                else:
                                    # 子线程中不能使用print，返回错误信息让主线程处理
                                    return i, None
                        return i, None

                    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                        # 只提交未完成的 chunks
                        futures = []
                        for i, chunk in enumerate(chunks):
                            if i not in unordered_results:  # 跳过已完成的
                                futures.append(executor.submit(fetch_chunk, i, chunk))

                        for future in concurrent.futures.as_completed(futures):
                            try:
                                idx, content = future.result()
                                if content:
                                    unordered_results[idx] = content
                            except Exception as e:
                                import traceback
                                tb_str = traceback.format_exc()
                                print(f"      ❌ 获取结果失败: {e}")
                                print(f"      📋 详细错误: {tb_str}")

                    chunk_results = []
                    for i in range(len(chunks)):
                        if i in unordered_results:
                            chunk_results.append(unordered_results[i])

                    if chunk_results:
                        print("   🧩 [Reduce] 正在启动 Reasoner 深度缝合各区块逻辑 (自动防截断)...")

                        reduce_system_prompt = f"""
                        You are a Master Chief Editor and Expert Synthesizer.
                        I will provide you with several sequential Markdown notes extracted from different chunks of the SAME long document.
                        Your task is to merge them into ONE cohesive, logically flawless final document.

                        CRITICAL INSTRUCTION: You MUST strictly conform to the exact Output Template and analytical rules defined in your Core System Framework below.

                        === YOUR CORE SYSTEM FRAMEWORK & TEMPLATE ===
                        {system_prompt}
                        =============================================

                        ADDITIONAL MERGE RULES:
                        1. MACRO-ARGUMENT RECONSTRUCTION: Do not just blindly stack the points. You must reconstruct the overarching chronological or logical argument of the original author.
                        2. Eliminate any duplicate headers, overlapping summaries, or redundant transition sentences between chunks.
                        3. DO NOT omit any concrete data, metric, or crucial logical proof extracted in the partial notes.
                        4. The final output must be ENTIRELY in {get_target_language()}.
                        """

                        combined_text = "\n\n=== NEXT CHUNK EXTRACTION ===\n\n".join(chunk_results)
                        # 简化处理：如果超限，保存到 interim 并重新处理
                        if len(combined_text) > MAX_LENGTH:
                            print(f"   ⚠️ 文本仍超限 ({len(combined_text)} 字)，保存到 interim 并重新处理...")
                            # 获取 session_dir
                            interim_dir = None
                            if shared_workspace and "session_dir" in shared_workspace:
                                interim_dir = os.path.join(shared_workspace["session_dir"], "interim")
                                os.makedirs(interim_dir, exist_ok=True)

                            if interim_dir:
                                # 保存为临时 .md 文件
                                interim_filename = f"interim_{os.path.splitext(filename)[0]}.md"
                                interim_path = os.path.join(interim_dir, interim_filename)
                                with open(interim_path, "w", encoding="utf-8") as f:
                                    f.write(combined_text)
                                print(f"   💾 已保存 interim 文件: {interim_path}")
                                interim_context = {
                                    "origin_skill_id": best_match,
                                    "interim_profile": self._get_skill_interim_profile(best_match) or "generic",
                                    "heading_strategy": "preserve_original_headings",
                                    "source_filename": filename,
                                    "original_input_ext": original_input_ext,
                                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
                                }
                                self._write_interim_context(interim_path, interim_context)

                                # 递归调用 process_file 处理临时文件
                                return self.process_file(interim_path, user_instruction, shared_workspace, source_ctx=source_ctx)

                            # 如果没有 session_dir，回退到直接拼接
                            final_raw_text = "\n\n---\n\n".join(chunk_results)
                        else:
                            # 原有单次 Reduce 逻辑
                            reduce_user_prompt = f"Please MERGE the following sequential notes into a single masterful Markdown document based strictly on the required Output Template:\n\n{combined_text}"
                            reduce_messages = [
                                {"role": "system", "content": reduce_system_prompt},
                                {"role": "user", "content": reduce_user_prompt}
                            ]
                            final_raw_text = ""
                            try:
                                while True:
                                    response = client.chat.completions.create(
                                        model=get_reduce_model_name("analytical"),
                                        messages=reduce_messages,
                                        extra_body={"thinking": {"type": "enabled"}},
                                        reasoning_effort="max",
                                        timeout=180
                                    )
                                    piece = response.choices[0].message.content
                                    final_raw_text += piece

                                    if response.choices[0].finish_reason == 'length':
                                        print("   ⏳ [自动续写] Pro 触发单次输出极值，正在命令 AI 无缝衔接后文...")
                                        reduce_messages.append({"role": "assistant", "content": piece})
                                        reduce_messages.append({"role": "user", "content": "You hit the output length limit. Please CONTINUE exactly from the very last word you wrote. Do NOT repeat anything you have already outputted, and do NOT add any conversational introduction. Just output the direct continuation of the Markdown text."})
                                    else:
                                        break

                                result = re.sub(r'<think>.*?</think>', '', final_raw_text, flags=re.DOTALL).strip()
                                print("   ✅ [Reduce] 缝合、结构化重组与净化完成！")
                            except Exception as e:
                                print(f"   ❌ 全局融合请求失败: {e}")
            if result:
                return self._finalize_note_output(filename, result, md_folder, source_ctx, shared_workspace)
            else:
                msg = f"   ⚠️ 分析失败或无结果，原文件保留在 inputs 中，等待下次重试。"
                print(msg)
                self.send_gui_msg(msg, 'warning')
                return None

    # ================= 🔄 断点续传相关函数 =================

    def _get_checkpoint_dir(self, session_dir, filename):
        """获取 chunk checkpoint 目录"""
        checkpoint_dir = os.path.join(session_dir, "chunk_checkpoints", filename)
        os.makedirs(checkpoint_dir, exist_ok=True)
        return checkpoint_dir

    def _load_checkpoint(self, session_dir, filename):
        """加载 checkpoint，返回已完成的 chunk 索引列表"""
        checkpoint_dir = os.path.join(session_dir, "chunk_checkpoints", filename)
        meta_path = os.path.join(checkpoint_dir, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                # 读取已完成的 chunk 内容
                completed_chunks = {}
                for idx in meta.get("completed_indices", []):
                    chunk_path = os.path.join(checkpoint_dir, f"chunk_{idx}.md")
                    if os.path.exists(chunk_path):
                        with open(chunk_path, "r", encoding="utf-8") as f:
                            completed_chunks[idx] = f.read()
                return completed_chunks
            except:
                pass
        return {}

    def _save_checkpoint(self, session_dir, filename, chunk_idx, chunk_content, total_chunks):
        """保存单个 chunk 的 checkpoint（原子写入避免竞态）"""
        checkpoint_dir = self._get_checkpoint_dir(session_dir, filename)
        # 保存 chunk 内容
        chunk_path = os.path.join(checkpoint_dir, f"chunk_{chunk_idx}.md")
        with open(chunk_path, "w", encoding="utf-8") as f:
            f.write(chunk_content)

        # 原子更新 meta.json（避免多线程竞态）
        meta_path = os.path.join(checkpoint_dir, "meta.json")
        temp_meta = meta_path + ".tmp"

        # 读取现有 meta
        meta = {"completed_indices": [], "total": total_chunks}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except:
                pass

        # 更新数据
        if chunk_idx not in meta["completed_indices"]:
            meta["completed_indices"].append(chunk_idx)
            meta["completed_indices"].sort()
        meta["total"] = total_chunks

        # 原子写入：先写临时文件，再重命名
        try:
            with open(temp_meta, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            os.replace(temp_meta, meta_path)  # 原子替换
        except Exception as e:
            # 清理临时文件
            if os.path.exists(temp_meta):
                os.remove(temp_meta)
            raise e

    def _clear_checkpoints(self, session_dir, filename):
        """清理指定文件的 checkpoint"""
        checkpoint_dir = os.path.join(session_dir, "chunk_checkpoints", filename)
        if os.path.exists(checkpoint_dir):
            shutil.rmtree(checkpoint_dir)

    def execute(self, task_params: dict, shared_workspace: dict) -> dict:
        """
        对接厂长的标准 API。厂长下达指令，Runner 开始扫库并处理。
        支持断点续传：如果 session_dir 中有 checkpoint，则跳过已完成的文件。
        """
        input_dir = task_params.get("input_dir", self.paths["inputs"])
        user_instruction = task_params.get("user_instruction", "")

        print(f"👨‍🔧 [{self.name}] 收到厂长指令，开始处理目录: {input_dir}")
        if user_instruction:
            print(f"🎯 [目标锁定] 本次阅读将带入特定视角: {user_instruction}")

        supported_exts = ('*.pdf', '*.docx', '*.doc', '*.pptx', '*.xlsx', '*.md')
        files = []
        for ext in supported_exts:
            files.extend(glob.glob(os.path.join(input_dir, ext)))

        if not files:
            msg = f"目录 {input_dir} 下未找到可处理的文件。"
            print(f"⚠️ [{self.name}] {msg}")
            return {"status": "success", "output_filepaths": [], "message": msg}

        clear_force_rerun_for_missing_files()
        processed_lookup = build_hash_lookup(self.paths["processed"], recursive=False)
        processed_hashes = set(processed_lookup.keys())
        input_hashes_seen = set()
        if shared_workspace is not None:
            shared_workspace["_runner_processed_hash_lookup"] = processed_lookup

        # 检查 session_dir 是否有 checkpoint（用于断点续传）
        session_dir = shared_workspace.get("session_dir") if shared_workspace else None
        checkpoint_dir = None
        if session_dir:
            checkpoint_dir = os.path.join(session_dir, "chunk_checkpoints")

        processed_files = []
        for file_path in files:
            filename = os.path.basename(file_path)
            try:
                source_ctx = self._build_source_ctx(file_path=file_path)
                source_hash = source_ctx.get("source_hash")
                force_rerun = bool(source_ctx.get("force_rerun"))

                if source_hash and not force_rerun:
                    if source_hash in processed_hashes:
                        print(f"   ⏭️ [去重] {filename} 与 processed 中已有原文内容相同，默认跳过")
                        continue
                    if source_hash in input_hashes_seen:
                        print(f"   ⏭️ [去重] {filename} 与本批次另一输入内容相同，默认跳过")
                        continue
                    input_hashes_seen.add(source_hash)

                # 检查是否有已完成的 checkpoint
                if checkpoint_dir and os.path.exists(checkpoint_dir):
                    file_checkpoint_dir = os.path.join(checkpoint_dir, filename)
                    meta_path = os.path.join(file_checkpoint_dir, "meta.json")
                    if os.path.exists(meta_path):
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                meta = json.load(f)
                            completed_indices = meta.get("completed_indices", [])
                            total = meta.get("total", 0)
                            # 检查是否所有 chunk 都已完成
                            if len(completed_indices) == total and total > 0:
                                print(f"   ⏭️ [断点续传] {filename} 已完成，跳过")
                                # 仍然添加到 processed_files（假设已有输出）
                                continue
                        except:
                            pass

                output_md_path = self.process_file(file_path, user_instruction, shared_workspace, source_ctx=source_ctx)
                if output_md_path:
                    processed_files.append(output_md_path)
                    if source_hash:
                        processed_hashes.add(source_hash)
                    # 🌟 修改点 4：把新产出的笔记路径写入共享黑板！
                    if shared_workspace and "current_session_new_notes" in shared_workspace:
                        shared_workspace["current_session_new_notes"].append(output_md_path)
                    # ✅ 成功后清理 checkpoint
                    if checkpoint_dir:
                        self._clear_checkpoints(session_dir, filename)
            except Exception as e:
                print(f"⚠️ [{self.name}] 处理文件 {file_path} 时出错: {e}")

        if shared_workspace is not None:
            shared_workspace.pop("_runner_processed_hash_lookup", None)
        clear_force_rerun_for_missing_files()
        print(f"✅ [{self.name}] 任务完成！共成功处理 {len(processed_files)} 个文件。")
        return {
            "status": "success",
            "output_filepaths": processed_files
        }
