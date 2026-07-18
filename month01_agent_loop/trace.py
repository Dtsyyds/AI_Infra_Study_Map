"""
trace.py

Agent 执行轨迹日志模块

作用：
1. 记录每次用户请求
2. 记录每一步 LLM 输出、Action、工具执行结果
3. 记录最终答案
4. 保存为 JSONL，方便后续分析和评估
"""

import json
import os
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict


class AgentTrace:
    def __init__(self, log_dir: str = "month01_agent_loop/logs"):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

        self.trace_id = str(uuid.uuid4())
        self.start_time = datetime.now().isoformat(timespec="seconds")

        self.data: Dict[str, Any] = {
            "trace_id": self.trace_id,
            "start_time": self.start_time,
            "end_time": None,
            "user_input": None,
            "steps": [],
            "final_answer": None,
            "status": "running",
            "error": None,
            "duration_seconds": None,
        }

        self._start_dt = datetime.now()
        self.start_time = self._start_dt.isoformat(timespec="seconds")

    def set_user_input(self, user_input: str):
        self.data["user_input"] = user_input

    def add_step(self, step_index: int, llm_output: str, action: str, result: Dict[str, Any]):
        step = {
            "step_index": step_index,
            "time": datetime.now().isoformat(timespec="seconds"),
            "llm_output": llm_output,
            "action": action,
            "result": result,
        }
        self.data["steps"].append(step)

    def finish(self, final_answer: str, status: str = "success") -> None:
        end_dt = datetime.now()
        self.data["final_answer"] = final_answer
        self.data["status"] = status
        self.data["end_time"] = end_dt.isoformat(timespec="seconds")
        self.data["duration_seconds"] = round((end_dt - self._start_dt).total_seconds(), 3)
        self._save()

    def fail(self, error: str) -> None:
        end_dt = datetime.now()
        self.data["status"] = "failed"
        self.data["error"] = error
        self.data["end_time"] = end_dt.isoformat(timespec="seconds")
        self.data["duration_seconds"] = round((end_dt - self._start_dt).total_seconds(), 3)
        self._save()

    def _save(self) -> None:
        log_path = os.path.join(self.log_dir, "agent_trace.jsonl")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.data, ensure_ascii=False) + "\n")
    
    def snapshot(self) -> Dict[str, Any]:
        """
        返回当前 Trace 数据的深拷贝

        Eval 可以安全读取执行轨迹，但不能直接修改 Trace 内部数据
        """
        return deepcopy(self.data)