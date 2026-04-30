"""
Lightweight Vector Search Module (Vector Store)
- Incremental update: Automatically update vectors when new documents are added
- Semantic search: Support RAG semantic matching
- Lightweight: Supports ONNX Runtime / sentence-transformers, runs locally
"""
import os
import sys

# Force offline mode - never connect to HuggingFace Hub
# Model is loaded from local cache only
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'

# Fix Windows console encoding issues
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass

import json
import numpy as np
import threading
from pathlib import Path

# ================= 配置 =================
from paths import get_index_path
from vector_store_config import EMBEDDING_MODEL, VECTOR_DB_DIR

# 向量库文件路径
VECTORS_FILE = os.path.join(VECTOR_DB_DIR, "vectors.npy")
METADATA_FILE = os.path.join(VECTOR_DB_DIR, "metadata.json")

# ================= 全局变量 =================
_model = None
_embedder = None
_model_lock = threading.Lock()  # 线程锁，防止重复加载


def _get_embedder():
    """延迟加载 embedding 模型（首次调用时才加载，线程安全）"""
    global _model, _embedder

    # 快速检查（无需锁）
    if _embedder is not None:
        return _embedder

    # 加锁等待
    with _model_lock:
        # 双重检查（获得锁后再次确认）
        if _embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
                print(f"   📥 首次运行，正在加载轻量级语义模型: {EMBEDDING_MODEL} ...")

                # 🌟 添加重试逻辑，处理网络不稳定问题
                import time
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        _embedder = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
                        print("   ✅ 语义模型加载完成！")
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 2  # 2, 4, 6秒
                            print(f"   ⚠️ 模型加载失败 (尝试 {attempt+1}/{max_retries})，{wait_time}秒后重试...")
                            time.sleep(wait_time)
                        else:
                            print(f"   ❌ 语义模型加载失败: {e}")
                            return None

            except ImportError:
                print("   ⚠️ 未安装 sentence-transformers，请运行: pip install sentence-transformers")
                return None

    return _embedder


def _ensure_vector_store_dir():
    """确保向量库目录存在"""
    os.makedirs(VECTOR_DB_DIR, exist_ok=True)


def _load_existing_data():
    """加载现有的向量和元数据"""
    _ensure_vector_store_dir()

    vectors = None
    metadata = []

    if os.path.exists(VECTORS_FILE):
        vectors = np.load(VECTORS_FILE)

    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r', encoding='utf-8') as f:
            metadata = json.load(f)

    return vectors, metadata


def _save_data(vectors, metadata):
    """保存向量和元数据"""
    _ensure_vector_store_dir()

    np.save(VECTORS_FILE, vectors)

    with open(METADATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def _get_text_for_embedding(item):
    """将索引项转换为用于 embedding 的文本"""
    # 组合多个字段以获得更好的语义表示
    parts = []

    if item.get("title"):
        parts.append(f"标题: {item['title']}")

    if item.get("core_claim"):
        parts.append(f"核心论点: {item['core_claim']}")

    if item.get("keywords"):
        keywords = ", ".join(item.get("keywords", []))
        parts.append(f"关键词: {keywords}")

    if item.get("extracted_concepts"):
        concepts = ", ".join(item.get("extracted_concepts", []))
        parts.append(f"概念: {concepts}")

    if item.get("document_type"):
        parts.append(f"类型: {item['document_type']}")

    return "\n".join(parts)


def add_documents(index_items: list):
    """
    增量添加文档到向量库
    - 只添加不存在的文档（根据 filepath 判断）
    - 已有文档不会重复计算
    """
    embedder = _get_embedder()
    if embedder is None:
        return False

    # 加载现有数据
    existing_vectors, existing_metadata = _load_existing_data()

    # 构建已有 filepath 集合
    existing_paths = {item.get("filepath") for item in existing_metadata if item.get("filepath")}

    # 筛选需要添加的新文档
    new_items = [item for item in index_items if item.get("filepath") not in existing_paths]

    if not new_items:
        print("   ℹ️ 向量库已是最新，无需更新。")
        return True

    print(f"   🔄 发现 {len(new_items)} 个新文档，正在计算语义向量...")

    # 为新文档生成 embedding
    texts = [_get_text_for_embedding(item) for item in new_items]
    new_vectors = embedder.encode(texts, show_progress_bar=True)

    # 合并向量和元数据
    if existing_vectors is not None:
        all_vectors = np.vstack([existing_vectors, new_vectors])
    else:
        all_vectors = new_vectors

    all_metadata = existing_metadata + new_items

    # 保存
    _save_data(all_vectors, all_metadata)

    print(f"   ✅ 向量库已更新！共 {len(all_metadata)} 个文档。")
    return True


def search(query: str, threshold: float = 0.3) -> list:
    """
    语义搜索
    - 输入查询文本
    - 返回所有相似度 >= threshold 的文档

    Args:
        query: 查询文本
        threshold: 相似度阈值（默认 0.3），低于此值的文档不返回

    返回格式:
    [
        {"item": {...}, "score": 0.95},
        ...
    ]
    """
    embedder = _get_embedder()
    if embedder is None:
        return []

    # 加载现有数据
    vectors, metadata = _load_existing_data()

    if vectors is None or len(vectors) == 0:
        print("   ⚠️ 向量库为空，请先添加文档。")
        return []

    # 计算查询向量
    query_vector = embedder.encode([query])

    # 计算余弦相似度
    from sklearn.metrics.pairwise import cosine_similarity
    similarities = cosine_similarity(query_vector, vectors)[0]

    # 获取所有 >= threshold 的索引（按相似度降序）
    results = []
    for idx in range(len(similarities)):
        score = similarities[idx]
        if score >= threshold:
            results.append({
                "item": metadata[idx],
                "score": float(score)
            })

    # 按相似度排序
    results.sort(key=lambda x: x["score"], reverse=True)

    return results


def rebuild_vector_store(force: bool = False):
    """
    重建整个向量库
    - 从 index.json 读取所有文档
    - 重新计算所有向量

    Args:
        force: 是否强制重建（会覆盖现有向量库）
    """
    index_path = get_index_path()

    if not os.path.exists(index_path):
        print(f"   ⚠️ 索引文件不存在: {index_path}")
        return False

    with open(index_path, 'r', encoding='utf-8') as f:
        index_items = json.load(f)

    if not index_items:
        print("   ⚠️ 索引文件为空。")
        return False

    # 检查是否需要重建
    _, existing_metadata = _load_existing_data()

    if existing_metadata and not force:
        print(f"   ℹ️ 向量库已有 {len(existing_metadata)} 个文档，使用 add_documents() 增量更新。")
        return add_documents(index_items)

    print(f"   🔨 正在重建向量库，共 {len(index_items)} 个文档...")

    # 删除旧文件
    if os.path.exists(VECTORS_FILE):
        os.remove(VECTORS_FILE)
    if os.path.exists(METADATA_FILE):
        os.remove(METADATA_FILE)

    # 重建
    return add_documents(index_items)


def sync_from_index(force: bool = False) -> dict:
    """
    从 index.json 增量同步到向量库

    对比 index.json 和 VDB metadata.json，检测并处理：
    - 新增：index.json 有但 VDB 没有的文档
    - 变更：filepath 相同但内容不同的文档（force=True 时重新编码）
    - 删除：VDB 有但 index.json 没有的文档

    Args:
        force: True 时强制重新编码所有文档（即使内容未变）

    Returns:
        {"added": N, "updated": N, "removed": N}
    """
    index_path = get_index_path()

    if not os.path.exists(index_path):
        print(f"   ⚠️ 索引文件不存在: {index_path}")
        return {"added": 0, "updated": 0, "removed": 0}

    with open(index_path, 'r', encoding='utf-8') as f:
        index_data = json.load(f)

    # 加载现有 VDB 数据
    existing_vectors, existing_metadata = _load_existing_data()

    # 建立 filepath 集合用于快速对比
    index_paths = {item.get("filepath") for item in index_data if item.get("filepath")}
    vdb_paths = {item.get("filepath") for item in existing_metadata if item.get("filepath")}

    # 计算差集
    to_add_paths = index_paths - vdb_paths  # 新增
    to_remove_paths = vdb_paths - index_paths  # 删除
    to_check_paths = index_paths & vdb_paths  # 可能需要更新的

    # 检查是否有内容变更（比较核心字段）
    def is_changed(item1, item2):
        """比较两个文档是否内容不同"""
        key_fields = ["title", "core_claim", "keywords", "extracted_concepts", "document_type"]
        for field in key_fields:
            v1 = json.dumps(item1.get(field, ""), sort_keys=True)
            v2 = json.dumps(item2.get(field, ""), sort_keys=True)
            if v1 != v2:
                return True
        return False

    # 构建现有 VDB 的 dict（便于查找）
    vdb_dict = {item.get("filepath"): item for item in existing_metadata if item.get("filepath")}

    # 构建 index_dict 便于查找
    index_dict = {item.get("filepath"): item for item in index_data if item.get("filepath")}

    # 筛选需要更新的文档（内容有变化）
    to_update_items = []
    if force:
        for path in to_check_paths:
            to_update_items.append(index_dict[path])
    else:
        for path in to_check_paths:
            if is_changed(index_dict[path], vdb_dict[path]):
                to_update_items.append(index_dict[path])

    to_update_paths = {item["filepath"] for item in to_update_items}

    # 统计
    added_count = len(to_add_paths)
    updated_count = len(to_update_paths)
    removed_count = len(to_remove_paths)

    if added_count == 0 and updated_count == 0 and removed_count == 0:
        print("   ℹ️ 向量库已是最新，无需同步。")
        return {"added": 0, "updated": 0, "removed": 0}

    print(f"   🔄 同步检测: 新增 {added_count}, 更新 {updated_count}, 删除 {removed_count}")

    # 构建新的 metadata（保留未变动的 + 新增/更新的）
    new_metadata = []

    # 添加未变动的文档（存在于两者中且内容相同）
    unchanged_paths = (index_paths & vdb_paths) - to_update_paths
    for path in unchanged_paths:
        if vdb_dict[path]:
            new_metadata.append(vdb_dict[path])

    # 添加新增的文档
    for path in to_add_paths:
        if index_dict.get(path):
            new_metadata.append(index_dict[path])

    # 添加更新的文档（使用 index.json 中的版本）
    for path in to_update_paths:
        if index_dict.get(path):
            new_metadata.append(index_dict[path])

    # 如果有新增或更新，需要重新计算向量
    embedder = _get_embedder()
    if embedder is None:
        print("   ❌ 无法加载 embedding 模型，同步失败")
        return {"added": 0, "updated": 0, "removed": 0}

    # 需要重新编码的文档
    docs_to_encode = [index_dict[path] for path in (to_add_paths | to_update_paths) if index_dict.get(path)]

    if docs_to_encode:
        print(f"   🔄 正在计算 {len(docs_to_encode)} 个文档的语义向量...")
        texts = [_get_text_for_embedding(item) for item in docs_to_encode]
        new_vectors = embedder.encode(texts, show_progress_bar=True)
    else:
        new_vectors = None

    # 构建新的 vectors
    if new_vectors is not None and existing_vectors is not None:
        # 重建整个向量矩阵（按 new_metadata 的顺序）
        # 需要重新编码的向量
        encoded_dict = {item["filepath"]: new_vectors[i] for i, item in enumerate(docs_to_encode)}

        all_vectors_list = []
        for item in new_metadata:
            fp = item.get("filepath")
            if fp in encoded_dict:
                all_vectors_list.append(encoded_dict[fp])
            elif fp in vdb_dict and fp not in to_remove_paths:
                # 保留原有向量（找到对应索引）
                old_idx = next((i for i, m in enumerate(existing_metadata) if m.get("filepath") == fp), -1)
                if old_idx >= 0 and existing_vectors is not None:
                    all_vectors_list.append(existing_vectors[old_idx])

        new_vectors_final = np.array(all_vectors_list) if all_vectors_list else None
    elif new_vectors is not None:
        new_vectors_final = new_vectors
    elif existing_vectors is not None:
        # 只处理删除，需要重建
        indices_to_keep = [i for i, m in enumerate(existing_metadata) if m.get("filepath") not in to_remove_paths]
        new_vectors_final = existing_vectors[indices_to_keep] if indices_to_keep else None
    else:
        new_vectors_final = None

    # 保存
    if new_metadata:
        _save_data(new_vectors_final, new_metadata)
        print(f"   ✅ 同步完成！向量库共 {len(new_metadata)} 个文档。")
    else:
        # 清空向量库
        if os.path.exists(VECTORS_FILE):
            os.remove(VECTORS_FILE)
        if os.path.exists(METADATA_FILE):
            os.remove(METADATA_FILE)
        print("   ✅ 同步完成！向量库已清空。")

    return {"added": added_count, "updated": updated_count, "removed": removed_count}


if __name__ == "__main__":
    # 测试
    print("=" * 50)
    print("向量库测试")
    print("=" * 50)

    # 重建向量库
    rebuild_vector_store(force=True)

    # 测试搜索
    if os.path.exists(METADATA_FILE):
        print("\n🔍 测试搜索: '欧洲防务政策'")
        results = search("欧洲防务政策", top_k=3)

        for i, r in enumerate(results, 1):
            print(f"\n--- 结果 {i} (相似度: {r['score']:.3f}) ---")
            print(f"标题: {r['item'].get('title', 'N/A')}")
            print(f"关键词: {r['item'].get('keywords', [])}")
