"""
Page Anchor Parser - Micro Layer Extraction Utility

Parses [[TYPE: X]] anchors and extracts exact text from original files.
Supports: PDF, DOCX, Excel, PPTX
This module provides pure Python extraction to prevent LLM hallucinations.
"""

import re
import fitz  # PyMuPDF
import os
import json

try:
    from paths import APP_ROOT, DATA_ROOT
except Exception:
    APP_ROOT = None
    DATA_ROOT = None

try:
    from utils import find_antiword
except Exception:
    find_antiword = None


# ================= 文件类型检测 =================

def detect_file_type(file_path: str) -> str:
    """Detect file type from extension"""
    ext = os.path.splitext(file_path)[1].lower()
    type_map = {
        '.pdf': 'PDF',
        '.docx': 'DOCX',
        '.doc': 'DOCX',
        '.xlsx': 'EXCEL',
        '.xls': 'EXCEL',
        '.pptx': 'PPTX',
        '.ppt': 'PPTX',
        '.txt': 'TEXT',
        '.md': 'MD'  # 🌟 MD 文件独立类型，支持行号锚点
    }
    return type_map.get(ext, 'UNKNOWN')


# ================= Anchor 解析 =================

def parse_anchor(anchor: str) -> dict:
    """
    Parse anchor string into file type and location info.

    Supported formats:
    - PDF: [[PDF: 12]] or [[PDF: 15-20]] or [[PDF: Global]]
    - DOCX: [[DOCX: 12]] or [[DOCX: 15-20]] or [[DOCX: Para 5]] or [[DOCX: Global]]
    - EXCEL: [[EXCEL: Sheet1]] or [[EXCEL: Sheet1|A1:B10]] or [[EXCEL: Global]]
    - PPTX: [[PPTX: 5]] or [[PPTX: 5-10]] or [[PPTX: Global]]
    - MD/HTML/WEB: [[MD: 10]], [[HTML: 2-3]], [[WEB: 4]]
    - SOURCE (generic): [[SOURCE: X]] or [[SOURCE: Global]] or [[SOURCE: X1-X2]]

    Compatibility notes:
    - Single-bracket variants like [PDF: 17-20] are normalized.
    - Mixed anchors such as [[PDF: 2, Global]] prefer the local numeric spans.
    - SOURCE supports comma-separated ranges.

    Returns:
        dict: {type: 'PDF'|'DOCX'|'EXCEL'|'PPTX'|'SOURCE', pages: [...], sheet: str, range: str, is_global: bool, quote_text: str}
    """
    result = {'type': 'UNKNOWN', 'pages': [], 'sheet': None, 'range': None, 'is_global': False, 'quote_text': None}

    anchor = (anchor or "").strip()
    if not anchor:
        return result

    normalized_anchor = anchor
    if re.match(r'^\w+:\s*.+$', normalized_anchor):
        normalized_anchor = f'[[{normalized_anchor}]]'
    elif normalized_anchor.startswith('[['):
        if not normalized_anchor.endswith(']]'):
            normalized_anchor = normalized_anchor.rstrip(']') + ']]'
    elif normalized_anchor.startswith('['):
        if normalized_anchor.endswith(']'):
            if not normalized_anchor.endswith(']]'):
                normalized_anchor = f'[{normalized_anchor}]'
        else:
            normalized_anchor = normalized_anchor + ']'

    def parse_numeric_spec(spec: str) -> list:
        pages = []
        for part in spec.split(','):
            part = part.strip()
            if not part:
                continue
            if '-' in part:
                start, end = part.split('-')
                pages.extend(range(int(start), int(end) + 1))
            else:
                pages.append(int(part))
        return pages

    # 🌟 Global format: [[PDF: Global]], [[DOCX: Global]], [[EXCEL: Global]], [[PPTX: Global]]
    global_match = re.search(r'\[\[(\w+):\s*Global\]\]', normalized_anchor, re.IGNORECASE)
    if global_match:
        file_type = global_match.group(1).upper()
        if file_type in ['PDF', 'DOCX', 'EXCEL', 'PPTX', 'MD', 'HTML', 'WEB', 'SOURCE']:
            result['type'] = file_type
            result['is_global'] = True
            return result

    # 🌟 QUOTE format: [[QUOTE: "exact text from document"]]
    quote_match = re.search(r'\[\[QUOTE:\s*"([^"]+)"\]\]', normalized_anchor, re.IGNORECASE)
    if quote_match:
        result['type'] = 'QUOTE'
        result['quote_text'] = quote_match.group(1)
        return result

    # First check for SOURCE format (generic/agnostic anchor)
    source_match = re.search(r'\[\[SOURCE:\s*([^\]]+)\]\]', normalized_anchor, re.IGNORECASE)
    if source_match:
        spec = source_match.group(1).strip()
        if spec.upper() == 'GLOBAL':
            result['type'] = 'SOURCE'
            result['is_global'] = True
            return result
        if re.fullmatch(r'[\d,\s\-]+', spec):
            result['type'] = 'SOURCE'
            result['pages'] = parse_numeric_spec(spec)
            return result

    # Try to match different formats
    # PDF/DOCX/PPTX: [[TYPE: 12]] or [[TYPE: 15-20]] or [[TYPE: 1, 3, 5]] or [[TYPE: 3-4, 43]]
    # Support comma-separated page numbers like "1,3,5" or "3-4,43" or "1-3,5-7,10"
    match = re.search(r'\[\[(\w+):\s*([^\]]+)\]\]', normalized_anchor)
    if match:
        file_type = match.group(1).upper()
        page_spec_raw = match.group(2).strip()

        if file_type in ['PDF', 'DOCX', 'PPTX', 'MD', 'HTML', 'WEB'] and not page_spec_raw.upper().startswith('PARA '):
            page_parts = [
                part.strip()
                for part in page_spec_raw.split(',')
                if part.strip() and part.strip().lower() != 'global'
            ]
            page_spec = ', '.join(page_parts)
            if page_spec and re.fullmatch(r'[\d,\s\-]+', page_spec):
                result['type'] = file_type
                result['pages'] = parse_numeric_spec(page_spec)
                return result

        if file_type in ['PDF', 'DOCX', 'PPTX'] and re.fullmatch(r'[\d,\s\-]+', page_spec_raw):
            result['type'] = file_type
            result['pages'] = parse_numeric_spec(page_spec_raw)
            return result

    # 🌟 DOCX Paragraph format: [[DOCX: Para 5]] or [[DOCX: Para 10-20]] or [[DOCX: Para 88, 90]]
    docx_para_match = re.search(r'\[\[DOCX:\s*Para\s+([\d,\s\-]+)\]\]', normalized_anchor, re.IGNORECASE)
    if docx_para_match:
        result['type'] = 'DOCX'
        result['is_paragraph'] = True
        para_spec = docx_para_match.group(1).strip()
        # 解析段落号：支持 "88", "88, 90", "88-90", "88, 90-95" 等格式
        result['pages'] = parse_numeric_spec(para_spec)
        return result

    # 🌟 MD format: [[MD: 10]] or [[MD: 10-20]] or [[MD: 10, 15]]
    md_line_match = re.search(r'\[\[MD:\s*([\d,\s\-]+)\]\]', normalized_anchor, re.IGNORECASE)
    if md_line_match:
        result['type'] = 'MD'
        # MD uses same 'pages' key as other formats
        line_spec = md_line_match.group(1).strip()
        # 解析行号：支持 "10", "10, 15", "10-20", "10, 15-20" 等格式
        result['pages'] = parse_numeric_spec(line_spec)
        return result

    # Excel: [[EXCEL: Sheet1]] or [[EXCEL: Sheet1|A1:B10]]
    match = re.search(r'\[\[EXCEL:\s*(\w+)(?:\|([A-Za-z0-9:]+))?\]\]', normalized_anchor)
    if match:
        result['type'] = 'EXCEL'
        result['sheet'] = match.group(1)
        result['range'] = match.group(2)  # e.g., "A1:B10"
        return result

    return result


# ================= 文本提取 =================

def extract_text_by_anchor(file_path: str, anchor: str, context_chars: int = 500) -> str:
    """
    Extract text from file using anchor, with context.

    Args:
        file_path: Path to the file
        anchor: Anchor string, e.g., '[[PDF: 12]]' or '[[DOCX: 5]]'
        context_chars: Number of characters to include before/after (default 500)

    Returns:
        Extracted text with anchor prefix preserved and context added
    """
    if not os.path.exists(file_path):
        # Try dynamic path resolution
        resolved_path = resolve_path_dynamic(file_path)
        if resolved_path:
            file_path = resolved_path
        else:
            return f"[Error: File not found: {file_path}]"

    anchor_info = parse_anchor(anchor)
    file_type = anchor_info['type']

    # Handle SOURCE (generic) anchor - detect from file extension
    if file_type == 'SOURCE':
        # If it's a global/source reference, detect file type and extract entire doc
        if anchor_info.get('is_global'):
            # For global, extract entire document based on detected file type
            file_type = detect_file_type(file_path)
            # For global, we pass empty pages to signal "entire document"
            anchor_info['pages'] = []
        else:
            # For specific page, detect file type and use appropriate extractor
            file_type = detect_file_type(file_path)

    if file_type == 'UNKNOWN':
        # Try to detect from file extension
        file_type = detect_file_type(file_path)

    if file_type == 'PDF':
        # If pages is empty (global request) or not specified, fallback to note content
        if not anchor_info['pages']:
            return "[Global anchor: fallback to note content]"
        else:
            return _add_context_to_extract(file_path, anchor, _extract_from_pdf(file_path, anchor_info['pages']), context_chars)
    elif file_type == 'DOCX':
        # 🌟 检测是否是旧版 .doc 文件
        if file_path.lower().endswith('.doc'):
            # 使用专门的 .doc 提取函数
            if not anchor_info['pages']:
                return "[Global anchor: fallback to note content]"
            else:
                return _add_context_to_extract(file_path, anchor, _extract_from_doc(file_path, anchor_info['pages']), context_chars)
        else:
            # 标准 DOCX 处理
            if not anchor_info['pages']:
                return "[Global anchor: fallback to note content]"
            else:
                return _add_context_to_extract(file_path, anchor, _extract_from_docx(file_path, anchor_info['pages']), context_chars)
    elif file_type == 'EXCEL':
        return _add_context_to_extract(file_path, anchor, _extract_from_excel(file_path, anchor_info['sheet'], anchor_info['range']), context_chars)
    elif file_type == 'PPTX':
        if not anchor_info['pages']:
            return "[Global anchor: fallback to note content]"
        else:
            return _add_context_to_extract(file_path, anchor, _extract_from_pptx(file_path, anchor_info['pages']), context_chars)
    elif file_type == 'MD':
        # 🌟 MD 文件行号提取
        if not anchor_info['pages']:
            return "[Global anchor: fallback to note content]"
        else:
            return _add_context_to_extract(file_path, anchor, _extract_from_md(file_path, anchor_info['pages']), context_chars)
    else:
        return f"[Error: Unsupported file type: {file_type}]"


def _add_context_to_extract(file_path: str, anchor: str, extracted_text: str, context_chars: int = 500) -> str:
    """
    Add context before and after extracted text by finding its position in the full document.
    """
    if not extracted_text or extracted_text.startswith("[Error") or extracted_text.startswith("[Warning"):
        return extracted_text

    # Read full document
    full_text = read_original_file(file_path)
    if not full_text:
        return extracted_text

    # Find position of extracted content in full document
    # Use first 100 chars to find approximate position
    first_100 = extracted_text[:100].strip()
    if not first_100:
        return extracted_text

    pos = full_text.find(first_100)
    if pos < 0:
        return extracted_text

    # Add context before and after
    context_start = max(0, pos - context_chars)
    context_end = min(len(full_text), pos + len(extracted_text) + context_chars)
    context_text = full_text[context_start:context_end]

    # Add ellipsis markers
    prefix = "..." if context_start > 0 else ""
    suffix = "..." if context_end < len(full_text) else ""

    return f"{prefix}{context_text}{suffix}"


def _extract_from_pdf(pdf_path: str, pages: list) -> str:
    """Extract text from PDF pages"""
    try:
        doc = fitz.open(pdf_path)
        extracted_parts = []

        for page_num in pages:
            if 0 <= page_num - 1 < len(doc):
                page = doc[page_num - 1]  # Convert to 0-index
                text = page.get_text().strip()
                if text:
                    extracted_parts.append(f"<page {page_num}>\n{text}\n</page>")
            else:
                extracted_parts.append(f"[Warning: Page {page_num} out of range]")

        doc.close()

        if extracted_parts:
            return f"[[PDF: {pages[0] if len(pages) == 1 else f'{pages[0]}-{pages[-1]}'}]]\n" + "\n\n".join(extracted_parts)
        else:
            return f"[Warning: No text found for pages: {pages}]"

    except Exception as e:
        return f"[Error extracting from PDF: {e}]"


def _extract_from_docx(docx_path: str, paragraphs: list) -> str:
    """Extract text from DOCX paragraphs"""
    try:
        from docx import Document
        doc = Document(docx_path)
        all_paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

        extracted_parts = []
        for para_num in paragraphs:
            if 0 < para_num <= len(all_paragraphs):
                extracted_parts.append(f"<paragraph {para_num}>\n{all_paragraphs[para_num - 1]}\n</paragraph>")
            else:
                extracted_parts.append(f"[Warning: Paragraph {para_num} out of range]")

        if extracted_parts:
            return f"[[DOCX: {paragraphs[0] if len(paragraphs) == 1 else f'{paragraphs[0]}-{paragraphs[-1]}'}]]\n" + "\n\n".join(extracted_parts)
        else:
            return f"[Warning: No text found for paragraphs: {paragraphs}]"

    except Exception as e:
        return f"[Error extracting from DOCX: {e}]"


def _extract_from_doc(doc_path: str, paragraphs: list) -> str:
    """Extract text from old .doc format (OLE Compound File)."""
    try:
        import subprocess

        antiword_path = find_antiword() if find_antiword else None
        if not antiword_path:
            return "[Error: antiword not found; install it on PATH or set COGNARCH_ANTIWORD]"

        result = subprocess.run([antiword_path, doc_path], capture_output=True)
        if result.returncode != 0:
            stderr_text = result.stderr.decode('utf-8', errors='ignore') if result.stderr else ''
            return f"[Error: antiword failed with code {result.returncode}: {stderr_text}]"

        raw_text = result.stdout.decode('utf-8', errors='ignore') if result.stdout else ''
        if not raw_text.strip():
            return "[Warning: No text extracted from DOC file]"

        all_paragraphs = [p.strip() for p in raw_text.split('\n\n') if p.strip()]
        extracted_parts = []
        for para_num in paragraphs:
            if 0 < para_num <= len(all_paragraphs):
                extracted_parts.append(f"<paragraph {para_num}>\n{all_paragraphs[para_num - 1]}\n</paragraph>")
            else:
                extracted_parts.append(f"[Warning: Paragraph {para_num} out of range (total: {len(all_paragraphs)})]")

        if extracted_parts:
            anchor = paragraphs[0] if len(paragraphs) == 1 else f'{paragraphs[0]}-{paragraphs[-1]}'
            return f"[[DOCX: {anchor}]]\n" + "\n\n".join(extracted_parts)
        return f"[Warning: No text found for paragraphs: {paragraphs}]"

    except Exception as e:
        return f"[Error extracting from DOC: {e}]"


def _extract_from_excel(excel_path: str, sheet_name: str = None, cell_range: str = None) -> str:
    """Extract text from Excel sheet/range"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(excel_path)

        # Use first sheet if not specified
        if not sheet_name:
            sheet = wb.active
            sheet_name = sheet.title
        else:
            sheet = wb[sheet_name]

        if cell_range:
            # Extract specific range
            cells = sheet[cell_range]
            rows = []
            for row in cells:
                row_data = [cell.value for cell in row if cell.value is not None]
                if row_data:
                    rows.append(" | ".join(str(v) for v in row_data))
            return f"[[EXCEL: {sheet_name}|{cell_range}]]\n" + "\n\n".join(rows)
        else:
            # Extract entire sheet
            rows = []
            for row in sheet.iter_rows(values_only=True):
                row_data = [cell for cell in row if cell is not None]
                if row_data:
                    rows.append(" | ".join(str(v) for v in row_data))
            return f"[[EXCEL: {sheet_name}]]\n" + "\n\n".join(rows[:100])  # Limit to 100 rows

    except Exception as e:
        return f"[Error extracting from Excel: {e}]"


def _extract_from_pptx(pptx_path: str, slides: list) -> str:
    """Extract text from PPTX slides"""
    try:
        from pptx import Presentation
        prs = Presentation(pptx_path)

        extracted_parts = []
        for slide_num in slides:
            if 0 < slide_num <= len(prs.slides):
                slide = prs.slides[slide_num - 1]
                texts = []
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        texts.append(shape.text)
                if texts:
                    extracted_parts.append(f"<slide {slide_num}>\n" + "\n".join(texts) + f"\n</slide>")
            else:
                extracted_parts.append(f"[Warning: Slide {slide_num} out of range]")

        if extracted_parts:
            return f"[[PPTX: {slides[0] if len(slides) == 1 else f'{slides[0]}-{slides[-1]}'}]]\n" + "\n\n".join(extracted_parts)
        else:
            return f"[Warning: No text found for slides: {slides}]"

    except Exception as e:
        return f"[Error extracting from PPTX: {e}]"


def _extract_from_md(md_path: str, lines: list) -> str:
    """Extract text from MD file by line numbers"""
    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()

        extracted_parts = []
        for line_num in lines:
            if 0 < line_num <= len(all_lines):
                # 去除行尾的换行符
                line_content = all_lines[line_num - 1].rstrip('\n\r')
                extracted_parts.append(f"<line {line_num}>{line_content}</line>")
            else:
                extracted_parts.append(f"[Warning: Line {line_num} out of range (total: {len(all_lines)})]")

        if extracted_parts:
            return f"[[MD: {lines[0] if len(lines) == 1 else f'{lines[0]}-{lines[-1]}'}]]\n" + "\n".join(extracted_parts)
        else:
            return f"[Warning: No text found for lines: {lines}]"

    except Exception as e:
        return f"[Error extracting from MD: {e}]"


def read_original_file(file_path: str) -> str:
    """
    Read original file (PDF/DOCX) and return as plain text string.
    Used for QUOTE matching in Deep Retrieval.

    Args:
        file_path: Path to the file

    Returns:
        Plain text content of the file
    """
    if not os.path.exists(file_path):
        return ""

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == '.pdf':
            # Use fitz to extract all text
            doc = fitz.open(file_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text
        elif ext in ['.docx', '.doc']:
            # Use python-docx
            from docx import Document
            doc = Document(file_path)
            text = "\n".join([para.text for para in doc.paragraphs])
            return text
        else:
            # Fallback: try to read as text
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
    except Exception as e:
        return f"[Error reading file: {e}]"


def extract_text_by_anchor_any(file_path: str, anchor: str, context_chars: int = 200) -> str:
    """
    Extract text using anchor with context support.
    Supports QUOTE matching for PDF/DOCX and structured tags for PPTX/Excel.

    Args:
        file_path: Path to the file
        anchor: Anchor string, e.g., '[[QUOTE: "exact text"]]', '[[PPTX: 3]]', '[[EXCEL: SheetName]]'
        context_chars: Number of characters to include before/after the match (default 200)

    Returns:
        Extracted text with context
    """
    # Parse anchor first
    anchor_info = parse_anchor(anchor)

    # Handle QUOTE format
    if anchor_info['type'] == 'QUOTE':
        quote_text = anchor_info.get('quote_text', '')
        if not quote_text:
            return "[Error: No quote text found]"

        # Resolve file path if needed
        actual_path = file_path
        if not os.path.exists(file_path):
            resolved_path = resolve_path_dynamic(file_path)
            if resolved_path:
                actual_path = resolved_path
            else:
                return f"[Error: File not found: {file_path}]"

        # Read the original file
        full_text = read_original_file(actual_path)
        if not full_text or full_text.startswith("[Error"):
            return full_text

        # Find exact match
        idx = full_text.find(quote_text)
        if idx >= 0:
            # Extract with context
            start = max(0, idx - context_chars)
            end = min(len(full_text), idx + len(quote_text) + context_chars)
            context = full_text[start:end]

            # Add ellipsis if truncated
            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(full_text) else ""
            return f"{prefix}{context}{suffix}"
        else:
            return f"[Warning: Quote not found in document: '{quote_text[:50]}...']"

    # Handle other formats using existing function
    return extract_text_by_anchor(file_path, anchor)


# ================= 深度扫描 =================

def deep_scan_file(file_path: str, page_range: str = None) -> str:
    """
    Extract entire file or specific page range (for Step 2.5 Deep Scan Fallback).

    Args:
        file_path: Path to the file
        page_range: Optional page range like '15-20' or None for entire doc

    Returns:
        Full extracted text with page markers
    """
    if not os.path.exists(file_path):
        resolved_path = resolve_path_dynamic(file_path)
        if resolved_path:
            file_path = resolved_path
        else:
            return f"[Error: File not found: {file_path}]"

    file_type = detect_file_type(file_path)

    if file_type == 'PDF':
        if page_range:
            pages = []
            for part in page_range.split(','):
                part = part.strip()
                if not part:
                    continue
                if '-' in part:
                    start, end = part.split('-')
                    pages.extend(range(int(start), int(end) + 1))
                else:
                    pages.append(int(part))
            return _extract_from_pdf(file_path, pages)
        else:
            # Full document
            return _extract_from_pdf(file_path, list(range(1, len(fitz.open(file_path)) + 1)))
    elif file_type == 'DOCX' and file_path.lower().endswith('.doc'):
        # 🌟 支持旧版 .doc 的深度扫描
        # .doc 使用段落号模拟页码（每10段作为一个"页"范围）
        if page_range:
            paragraphs = []
            for part in page_range.split(','):
                part = part.strip()
                if not part:
                    continue
                if '-' in part:
                    start, end = part.split('-')
                    para_start = (int(start) - 1) * 10 + 1
                    para_end = int(end) * 10
                    paragraphs.extend(range(para_start, para_end + 1))
                else:
                    page_num = int(part)
                    para_start = (page_num - 1) * 10 + 1
                    para_end = page_num * 10
                    paragraphs.extend(range(para_start, para_end + 1))
            return _extract_from_doc(file_path, paragraphs)
        else:
            # Full document - extract all paragraphs
            return _extract_from_doc(file_path, list(range(1, 9999)))
    else:
        return f"[Note: Deep scan for {file_type} returns full content]"
        # For non-PDF, could implement similarly


# ================= 路径解析 =================

def resolve_path_dynamic(note_path_or_doc_id: str, base_dir: str = None) -> str:
    """
    Dynamically resolve original file path by searching multiple locations.

    Search order:
    1. Direct path as given
    2. In processed/ folder (where files are actually stored)
    3. In knowledge_base/originals/ (legacy path)
    4. By matching doc_id in any subfolder

    Args:
        note_path_or_doc_id: Note path or doc_id to search for
        base_dir: Base directory (optional, defaults to project root)

    Returns:
        Resolved file path or None if not found
    """
    # Determine base directory
    if not base_dir:
        base_dir = str(DATA_ROOT) if DATA_ROOT else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Try different search strategies
    search_dirs = [
        os.path.join(base_dir, "processed"),           # Where files actually are
        os.path.join(base_dir, "knowledge_base", "originals"),  # Legacy path
        base_dir,                                      # Project root
    ]
    if APP_ROOT:
        app_root = str(APP_ROOT)
        search_dirs.extend([
            os.path.join(app_root, "processed"),
            os.path.join(app_root, "knowledge_base", "originals"),
            app_root,
        ])

    # Extract doc_id from note path if given
    doc_id = None
    if os.path.exists(note_path_or_doc_id):
        # It's a file path, extract doc_id from basename
        basename = os.path.basename(note_path_or_doc_id)
        # Remove prefix like [CN]_, suffix like _notes.md
        doc_id = basename.replace("[CN]_", "").replace("_notes.md", "").replace(".md", "")
    else:
        # Assume it's already a doc_id
        doc_id = note_path_or_doc_id

    # Search for matching file
    for search_dir in search_dirs:
        if not os.path.exists(search_dir):
            continue

        for root, dirs, files in os.walk(search_dir):
            for filename in files:
                # Check if filename contains the doc_id
                if doc_id and doc_id.lower() in filename.lower():
                    return os.path.join(root, filename)

                # Also check exact match - 支持所有常见文档格式
                supported_exts = ['.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.txt', '.md']
                for ext in supported_exts:
                    if filename.lower() == f"{doc_id}{ext}".lower():
                        return os.path.join(root, filename)

    # Last resort: return original path
    if os.path.exists(note_path_or_doc_id):
        return note_path_or_doc_id

    return None


def resolve_original_path(note_path: str, kb_base_dir: str) -> str:
    """
    Resolve original file path from note path using index.json or dynamic search.

    Args:
        note_path: Path to the note file
        kb_base_dir: Knowledge base base directory

    Returns:
        Resolved file path or None
    """
    # First try: Use index.json
    index_path = os.path.join(kb_base_dir, "index.json")
    if os.path.exists(index_path):
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                index_data = json.load(f)

            note_basename = os.path.basename(note_path)
            for item in index_data:
                if item.get("filepath") and os.path.basename(item["filepath"]) == note_basename:
                    orig_path = item.get("original_file", "")
                    if orig_path:
                        # Try direct path
                        full_path = os.path.join(kb_base_dir, "..", orig_path) if not os.path.isabs(orig_path) else orig_path
                        if os.path.exists(full_path):
                            return full_path

                        # Try with "processed" instead of "originals"
                        if "originals" in full_path:
                            alt_path = full_path.replace("originals", "processed")
                            if os.path.exists(alt_path):
                                return alt_path

                        # Dynamic search
                        doc_id = item.get("doc_id", "")
                        if doc_id:
                            dynamic_path = resolve_path_dynamic(doc_id, os.path.join(kb_base_dir, ".."))
                            if dynamic_path:
                                return dynamic_path
        except Exception:
            pass

    # Fallback: Dynamic search
    return resolve_path_dynamic(note_path, os.path.join(kb_base_dir, ".."))
