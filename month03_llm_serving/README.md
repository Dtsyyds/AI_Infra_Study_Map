# Month03 — LLM Serving Scheduler

## 学习目标

实现一个可测试的 LLM Serving 调度模型，理解：

- Prefill 与 Decode
- TTFT、TPOT 和端到端延迟
- Continuous Batching
- Chunked Prefill
- KV Cache 与峰值预留
- Paged KV Cache
- Recompute Preemption
- 抢占成本与尾延迟权衡

## 核心模块

- `serving_metrics.py`：请求延迟指标
- `request_queue.py`：有界 FIFO 请求队列
- `kv_cache.py`：逻辑 KV 容量模型
- `paged_kv_cache.py`：Block 化 KV Cache
- `scheduler.py`：批处理、KV 预留与抢占
- `simulation.py`：确定性离散 Step 模拟
- `benchmark_preemption.py`：抢占策略对比

## KV Cache 三种口径

- used：实际已经写入的逻辑 KV Token
- allocated：Block 对齐后的物理分配空间
- reserved：为活跃请求承诺的峰值逻辑空间

## 抢占策略实验

| 指标 | NONE | RECOMPUTE_LAST |
|---|---:|---:|
| B 延迟 | 3 | 1 |
| C 延迟 | 3 | 5 |
| 总完成步数 | 4 | 5 |
| 抢占次数 | 0 | 1 |
| 重计算 Token | 0 | 3 |

`RECOMPUTE_LAST` 用额外重计算和尾延迟换取短请求更快完成。

这些指标使用 `scheduler_step`，不代表真实毫秒延迟或 GPU 吞吐率。

## 运行测试

```bash
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

python3 -m pytest -q month03_llm_serving
python3 -m ruff check month03_llm_serving
python3 -m compileall -q month03_llm_serving