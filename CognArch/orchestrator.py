import os
from datetime import datetime

from analytical_worker import RunnerWorker
from generative_worker import GenerativeWorker

from paths import get_system_paths

# 🌟 全局唯一真理：所有的顶级目录都在这里定义，Worker 不再自己乱建文件夹
# 支持 release 默认同目录写入，也支持 COGNARCH_HOME 覆盖用户数据目录。
SYSTEM_PATHS = get_system_paths()


class Orchestrator:
    def __init__(self, msg_queue=None):
        self.paths = SYSTEM_PATHS
        self.msg_queue = msg_queue

        for path in SYSTEM_PATHS.values():
            os.makedirs(path, exist_ok=True)

        print("🏭 工厂初始化中...")
        self.workers = {
            "RunnerWorker": RunnerWorker(SYSTEM_PATHS, msg_queue),
            "GenerativeWorker": GenerativeWorker(SYSTEM_PATHS, msg_queue)
        }
        self.shared_workspace = {}
        print("✅ 工厂初始化完毕，随时可以接单！\n" + "="*50)

    def create_session(self) -> str:
        """建立本次任务的专属档案袋"""
        print("🗂️ 正在建立本次任务专属档案...")

        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = os.path.join(SYSTEM_PATHS["sessions"], session_id)
        os.makedirs(session_dir, exist_ok=True)

        self.shared_workspace = {
            "session_id": session_id,
            "session_dir": session_dir,
            "current_session_new_notes": []
        }

        print(f"✅ 专案建立成功！本次产物将保存在: {session_dir}")
        return session_id
