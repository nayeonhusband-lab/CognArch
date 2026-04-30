import os
import json
import time
from abc import ABC, abstractmethod

class BaseWorker(ABC):
    """
    所有具体执行者（Worker/Agent）的基类。
    强制规范了每个车间必须提供的信息和执行接口，并提供通用的基础工具。
    """
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @abstractmethod
    def execute(self, task_params: dict, shared_workspace: dict) -> dict:
        """
        统一的执行接口。
        :param task_params: 厂长派发任务时给的参数
        :param shared_workspace: 全局共享的工作台
        :return: 执行结果，必须是一个字典
        """
        pass

    def log_routing_decision(self, session_dir: str, target_name: str, skill_id: str, reason: str):
        """[通用工具] 将路由决策持久化到 session 专属的 log 文件中"""
        # 如果没有 session_dir（极端情况），就不记日志
        if not session_dir:
            return

        log_path = os.path.join(session_dir, "routing_decisions.json")
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "worker": self.name,
            "target_document_or_task": target_name,
            "selected_skill": skill_id,
            "thought_process": reason
        }
        
        logs = []
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
            except Exception:
                pass
                
        logs.append(log_entry)
        
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
            
        print(f"🧠 [路由思考] {self.name} 为 {target_name} 选定 {skill_id}。")