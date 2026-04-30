# ================= ⚙️ 向量搜索模块配置 =================

import os

from paths import get_vector_store_dir, model_path

# 获取模型路径的函数
def _get_model_path():
    """获取本地模型路径"""
    local_path = model_path("all-MiniLM-L6-v2")
    if os.path.exists(local_path):
        return local_path
    # 模型已内置于项目目录，分发时必须包含 models/ 文件夹
    raise FileNotFoundError(
        f"本地模型未找到: {local_path}\n"
        f"请确保 models/all-MiniLM-L6-v2/ 目录存在且包含模型文件。"
    )

# 使用的 embedding 模型 (轻量级，在 CPU 上运行很快)
# 备选: "paraphrase-multilingual-MiniLM-L12-v2" (支持多语言，但更大)
EMBEDDING_MODEL = _get_model_path()

# 向量库存储路径（用户数据目录，可由 COGNARCH_HOME 覆盖）
VECTOR_DB_DIR = get_vector_store_dir()
