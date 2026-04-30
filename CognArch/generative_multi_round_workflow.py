import json
import os
import re
import time
from typing import Any

from openai import OpenAI

from config import get_api_key, get_base_url


def _get_client():
    return OpenAI(api_key=get_api_key(), base_url=get_base_url())


def _send(worker, msg: str, tag: str = "info") -> None:
    print(msg)
    if hasattr(worker, "send_gui_msg"):
        worker.send_gui_msg(msg, tag)


def _safe_name(value: str, fallback: str) -> str:
    name = re.sub(r'[\\/*?:"<>|，。、（）\[\]{}《》]', "_", value or fallback).strip("_ ")
    return name[:60] if name else fallback


def _render_template(template: str, values: dict[str, Any]) -> str:
    rendered = template or ""
    for key, value in values.items():
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def _extract_json_object(text: str) -> Any:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    start = cleaned.find("{")
    if start >= 0:
        depth = 0
        for idx in range(start, len(cleaned)):
            if cleaned[idx] == "{":
                depth += 1
            elif cleaned[idx] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(cleaned[start:idx + 1])

    start = cleaned.find("[")
    if start >= 0:
        depth = 0
        for idx in range(start, len(cleaned)):
            if cleaned[idx] == "[":
                depth += 1
            elif cleaned[idx] == "]":
                depth -= 1
                if depth == 0:
                    return json.loads(cleaned[start:idx + 1])

    raise ValueError("No JSON object or array found in model response")


def _normalise_stages(raw_result: Any) -> list[dict[str, Any]]:
    if isinstance(raw_result, list):
        raw_stages = raw_result
    elif isinstance(raw_result, dict):
        raw_stages = []
        for key in ("stages", "sections", "outline", "chapters", "parts", "outline_keywords"):
            value = raw_result.get(key)
            if isinstance(value, list):
                raw_stages = value
                break
        if not raw_stages:
            raw_stages = [{"name": key, "keywords": value} for key, value in raw_result.items()]
    else:
        raw_stages = []

    stages = []
    for idx, stage in enumerate(raw_stages):
        if isinstance(stage, dict):
            keywords = stage.get("keywords", stage.get("tags", []))
            if isinstance(keywords, str):
                keywords = [k.strip() for k in re.split(r"[,，;；\n]+", keywords) if k.strip()]
            elif not isinstance(keywords, list):
                keywords = [str(keywords)] if keywords else []

            stages.append({
                "name": stage.get("name", stage.get("title", f"阶段{idx + 1}")),
                "purpose": stage.get("purpose", stage.get("goal", "")),
                "keywords": keywords,
            })
        elif isinstance(stage, str):
            stages.append({"name": stage, "purpose": "", "keywords": []})

    return stages


def _extract_stages(client, user_instruction: str, workflow_config: dict[str, Any]) -> list[dict[str, Any]]:
    prompt_template = workflow_config.get("stage_extraction_prompt") or """从以下用户指令中提取多个写作阶段。

要求：
1. 识别用户提供的大纲、框架、章节或几个部分。
2. 为每个阶段提取具体检索关键词。
3. 返回严格 JSON：{"stages":[{"name":"...","purpose":"...","keywords":["..."]}]}

用户指令：
{instruction}
"""
    prompt = prompt_template.replace("{instruction}", user_instruction)

    response = client.chat.completions.create(
        model=workflow_config.get("stage_extraction_model", "deepseek-v4-flash"),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        extra_body={"thinking": {"type": "enabled"}},
        reasoning_effort="max",
    )
    raw_text = response.choices[0].message.content
    print(f"   🔍 [Multi-Round] 阶段提取原始返回(前500字符): {raw_text[:500]}...")
    return _normalise_stages(_extract_json_object(raw_text))


def _build_stage_query(stage: dict[str, Any], user_instruction: str, workflow_config: dict[str, Any]) -> str:
    keywords = stage.get("keywords", [])
    max_keywords = int(workflow_config.get("max_keywords_per_stage", 5))
    if isinstance(keywords, list):
        keywords = keywords[:max_keywords]

    template = workflow_config.get("stage_query_template") or """阶段名称：{stage_name}
阶段目的：{stage_purpose}
阶段关键词：{stage_keywords}
全局任务：{user_instruction}
"""
    return _render_template(template, {
        "stage_name": stage.get("name", ""),
        "stage_purpose": stage.get("purpose", ""),
        "stage_keywords": keywords,
        "user_instruction": user_instruction,
    }).strip()


def _format_doc(doc: Any, index: int) -> str:
    if isinstance(doc, dict):
        doc_id = doc.get("doc_id") or doc.get("title") or f"Doc {index}"
        title = doc.get("title", doc_id)
        filepath = doc.get("filepath", "")
        score = doc.get("score", "")
        content = doc.get("content", "")
    else:
        doc_id = f"Doc {index}"
        title = doc_id
        filepath = ""
        score = ""
        content = str(doc)

    meta = [f"Doc ID: {doc_id}", f"Title: {title}"]
    if filepath:
        meta.append(f"Filepath: {filepath}")
    if score != "":
        meta.append(f"Score: {score}")

    return f"--- [Source {index}] {' | '.join(meta)} ---\n{content}"


def _pack_docs(docs: list[Any], char_limit: int, max_docs_per_batch: int) -> list[list[Any]]:
    batches = []
    current = []
    current_chars = 0

    for doc in docs:
        doc_chars = len(_format_doc(doc, len(current) + 1))
        should_close = current and (
            len(current) >= max_docs_per_batch or current_chars + doc_chars > char_limit
        )
        if should_close:
            batches.append(current)
            current = []
            current_chars = 0

        current.append(doc)
        current_chars += doc_chars

    if current:
        batches.append(current)

    return batches


def _call_stage_generation(
    client,
    system_prompt: str,
    stage: dict[str, Any],
    user_instruction: str,
    stage_query: str,
    all_stages: list[dict[str, Any]],
    docs: list[Any],
    workflow_config: dict[str, Any],
    previous_draft: str = "",
    batch_index: int = 1,
    batch_count: int = 1,
) -> str:
    docs_text = "\n\n".join(_format_doc(doc, idx + 1) for idx, doc in enumerate(docs))
    if not docs_text:
        docs_text = "本阶段未检索到可用文献。请明确说明证据不足，不要编造文献或引用。"

    previous_block = ""
    if previous_draft:
        previous_block = f"\n【已有阶段草稿】\n{previous_draft}\n"

    stages_overview = "\n".join(
        f"- {s.get('name', '')}: {s.get('purpose', '')}" for s in all_stages
    )
    stage_keywords = stage.get("keywords", [])

    prompt = f"""【内部阶段写作任务】
你正在为一个大纲驱动的最终文章撰写其中一个阶段。这里只输出该阶段正文，不输出 JSON block，不输出 <content_layer> 包裹。

【阶段名称】
{stage.get("name", "")}

【阶段目的】
{stage.get("purpose", "")}

【阶段关键词】
{stage_keywords}

【阶段检索问题】
{stage_query}

【全局任务】
{user_instruction}

【全部阶段概览】
{stages_overview}
{previous_block}
【本轮可用文献，批次 {batch_index}/{batch_count}】
{docs_text}

【写作要求】
1. 只基于提供的文献写作；没有证据时说明证据不足，不要编造。
2. 保留笔记中已有的 doc_id 与物理锚点引用，不要发明页码或锚点。
3. 输出完整的该阶段正文；如果有已有阶段草稿，要把新文献融合进去并输出完整更新稿。
4. 不要提及“批次”“迭代”“内部阶段写作”等处理过程。
5. 不输出 JSON block，不输出 <content_layer>，不写参考文献总表。
"""

    stage_system = f"""{system_prompt}

[Multi-round workflow override]
This is an internal section-draft call. The workflow-level output contract overrides the final Output Format above for this call only: do not output a JSON metadata block and do not output <content_layer>. Output only the requested section body.
"""
    response = client.chat.completions.create(
        model=workflow_config.get("stage_generation_model", "deepseek-v4-flash"),
        messages=[
            {"role": "system", "content": stage_system},
            {"role": "user", "content": prompt},
        ],
        extra_body={"thinking": {"type": "enabled"}},
        reasoning_effort="max",
    )
    return response.choices[0].message.content


def _generate_stage(
    client,
    worker,
    system_prompt: str,
    user_instruction: str,
    stage: dict[str, Any],
    stage_query: str,
    all_stages: list[dict[str, Any]],
    docs: list[Any],
    workflow_config: dict[str, Any],
    stage_dir: str,
    stage_index: int,
) -> str:
    char_limit = int(workflow_config.get("stage_batch_char_limit", 60000))
    max_docs_per_batch = int(workflow_config.get("max_docs_per_batch", 8))
    batches = _pack_docs(docs, char_limit, max_docs_per_batch) if docs else [[]]

    _send(worker, f"   📦 [Multi-Round] 阶段 {stage_index}: {len(docs)} 篇文献装箱为 {len(batches)} 批", "info")

    draft = ""
    for batch_index, batch in enumerate(batches, 1):
        draft = _call_stage_generation(
            client=client,
            system_prompt=system_prompt,
            stage=stage,
            user_instruction=user_instruction,
            stage_query=stage_query,
            all_stages=all_stages,
            docs=batch,
            workflow_config=workflow_config,
            previous_draft=draft,
            batch_index=batch_index,
            batch_count=len(batches),
        )

        checkpoint = os.path.join(
            stage_dir,
            f"stage_{stage_index}_{_safe_name(stage.get('name', ''), f'stage_{stage_index}')}_batch_{batch_index}.md",
        )
        with open(checkpoint, "w", encoding="utf-8") as f:
            f.write(draft)

    return draft


def _integrate_stages(
    client,
    system_prompt: str,
    user_instruction: str,
    stages_content: list[dict[str, Any]],
    workflow_config: dict[str, Any],
) -> str:
    stages_text = "\n\n".join(
        f"## {item['name']}\n\n{item['content']}" for item in stages_content
    )

    template = workflow_config.get("integration_prompt_template") or """【任务】：将以下各阶段正文整合为一篇完整文章。

【用户原始指令】
{user_instruction}

【各阶段正文】
{stages}

【整合要求】
1. 最终输出只允许一个顶部 fenced json 元数据块。
2. JSON 后必须紧跟一个 <content_layer>，正文全部放入其中。
3. 保留各阶段正文中的真实引用锚点，不要发明引用。
4. 只做结构整合、衔接润色和必要去重，不要透露内部多轮处理过程。
"""
    prompt = _render_template(template, {
        "user_instruction": user_instruction,
        "stages": stages_text,
    })

    response = client.chat.completions.create(
        model=workflow_config.get("integration_model", "deepseek-v4-flash"),
        messages=[
            {"role": "system", "content": f"严格遵循以下技能架构指令：\n\n{system_prompt}"},
            {"role": "user", "content": prompt},
        ],
        extra_body={"thinking": {"type": "enabled"}},
        reasoning_effort="max",
    )
    return response.choices[0].message.content


def run_multi_round_retrieval_workflow(
    worker,
    user_instruction: str,
    system_prompt: str,
    session_dir: str,
    shared_workspace: dict,
    workflow_config: dict,
) -> str:
    """
    Reusable generative workflow:
    split a user outline/task into stages, retrieve/filter per stage, generate each
    stage, then integrate the stage drafts into a single final artifact.
    """
    client = _get_client()
    workflow_config = workflow_config or {}

    stage_dir = os.path.join(session_dir, "stage_drafts")
    os.makedirs(stage_dir, exist_ok=True)

    _send(worker, "\n🎯 [Multi-Round] 启动可复用多轮检索工作流...", "info")
    stages = _extract_stages(client, user_instruction, workflow_config)
    if not stages:
        _send(worker, "   ⚠️ [Multi-Round] 无法提取阶段，返回空结果。", "warning")
        return ""

    _send(worker, f"   ✅ [Multi-Round] 阶段拆分完成，共 {len(stages)} 个阶段", "success")

    stages_content = []
    for idx, stage in enumerate(stages, 1):
        stage_name = stage.get("name", f"阶段{idx}")
        _send(worker, f"\n📦 [Multi-Round] 处理阶段 {idx}/{len(stages)}: {stage_name}", "info")

        stage_query = _build_stage_query(stage, user_instruction, workflow_config)
        _send(worker, f"   🔎 [Multi-Round] 阶段检索问题: {stage_query[:300]}", "info")

        docs = worker._search_global_kb(stage_query, shared_workspace) or []
        _send(worker, f"   📚 [Multi-Round] 阶段初始召回: {len(docs)} 篇", "info")

        if docs and workflow_config.get("use_coarse_filter", True):
            docs = worker._coarse_filter_documents(docs, stage_query)
            _send(worker, f"   🧹 [Multi-Round] 粗筛后保留: {len(docs)} 篇", "info")

        if docs and workflow_config.get("use_relevant_doc_filtering", True):
            docs = worker._filter_relevant_docs(docs, stage_query)
            _send(worker, f"   🎯 [Multi-Round] LLM 精筛后保留: {len(docs)} 篇", "info")

        stage_content = _generate_stage(
            client=client,
            worker=worker,
            system_prompt=system_prompt,
            user_instruction=user_instruction,
            stage=stage,
            stage_query=stage_query,
            all_stages=stages,
            docs=docs,
            workflow_config=workflow_config,
            stage_dir=stage_dir,
            stage_index=idx,
        )

        stage_path = os.path.join(stage_dir, f"stage_{idx}_{_safe_name(stage_name, f'stage_{idx}')}.md")
        with open(stage_path, "w", encoding="utf-8") as f:
            f.write(f"## {stage_name}\n\n{stage_content}")

        stages_content.append({
            "idx": idx,
            "name": stage_name,
            "purpose": stage.get("purpose", ""),
            "keywords": stage.get("keywords", []),
            "doc_count": len(docs),
            "content": stage_content,
        })
        _send(worker, f"   ✅ [Multi-Round] 阶段完成: {stage_name}", "success")

    _send(worker, "\n🔗 [Multi-Round] 正在整合各阶段为最终稿...", "info")
    final_content = _integrate_stages(
        client=client,
        system_prompt=system_prompt,
        user_instruction=user_instruction,
        stages_content=stages_content,
        workflow_config=workflow_config,
    )

    final_path = os.path.join(stage_dir, "final_output.md")
    with open(final_path, "w", encoding="utf-8") as f:
        f.write(final_content)

    meta_path = os.path.join(stage_dir, "multi_round_manifest.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "workflow": "multi_round_retrieval",
            "stage_count": len(stages_content),
            "stages": [
                {
                    "idx": item["idx"],
                    "name": item["name"],
                    "purpose": item["purpose"],
                    "keywords": item["keywords"],
                    "doc_count": item["doc_count"],
                }
                for item in stages_content
            ],
        }, f, ensure_ascii=False, indent=2)

    _send(worker, f"   ✅ [Multi-Round] 整合完成: {final_path}", "success")
    return final_content
