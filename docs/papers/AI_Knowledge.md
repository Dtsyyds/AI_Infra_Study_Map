## LLM 的核心任务可以简化为：

根据前面的 Token，预测下一个 Token 的概率分布。

Tokenizer 把文本转换成 Token ID。
Embedding 把 Token ID 转成向量。
Transformer 通过多层注意力和前馈网络处理向量。
输出层生成整个词表的 logits。
使用 greedy、top-k、top-p 等策略选择下一个 Token。
把新 Token 加入序列，继续预测。

因此文本的生成是自回归的，每个新 Token 都依赖于前面生成的 Tokens。所以 Decode 阶段天然具有串行性。

### Prefill 与 Decode

Prefill: 一次处理完整 Prompt, 长 Prompt 效果好，但吞吐量低。TTFT：Time To First Token，首 Token 延迟
Decode: 每次生成一个新 Token, 吞吐量高，但短 Prompt 效果好。通常偏显存带宽和调度瓶颈。TPOT：Time Per Output Token，每个输出 Token 的平均时间，TPS：Tokens Per Second

### KV Cache

注意力层会为历史 Token 计算 Key 和 Value。Decode 时如果每次全部重算，会浪费大量计算。
KV Cache 保存每一层历史 Token 的 K/V：
历史 Token
    ↓
已缓存的 K/V
    ↓
新 Token 只计算自己的 K/V

收益是减少重复计算，代价是显存占用随以下因素增长：
batch size；
序列长度；
Transformer 层数；
KV head 数量；
head dimension；
数据类型字节数。

这就是为什么 Serving Infra 必须考虑：
连续批处理；
KV Cache 管理；
请求调度；
长上下文限制；
显存背压；
Prefix Cache。

Prefill 和 Decode 有什么区别？
KV Cache 保存的是什么？解决什么问题？
Prefill 一次处理全部输入 Token，能够利用矩阵并行计算，主要影响 TTFT；Decode 每轮只生成一个新 Token，具有自回归串行性，主要影响 TPOT。KV Cache 保存每层历史 Token 的 K/V，避免 Decode 时重复计算历史注意力，代价是显存占用随 batch size 和序列长度增长。

