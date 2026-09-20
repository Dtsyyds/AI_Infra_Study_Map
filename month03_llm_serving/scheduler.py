from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from month03_llm_serving.request_queue import InferenceRequest, RequestQueue

from month03_llm_serving.paged_kv_cache import PagedKVCache, PagedKVCacheFullError

class RequestKVCacheTooLargeError(ValueError):
    """ 超过系统容量 """

class PreemptionPolicy(str, Enum):
    # KV不足时继续等待，保持现有行为
    NONE = "none"
    # 抢占最后进入的、处于DECODE阶段的活跃请求
    RECOMPUTE_LAST = "recompute_last"

class RequestPhase(str, Enum):
    PREFILL = "prefill"
    DECODE = "decode"
    FINISHED = "finished"

@dataclass(slots=True)
class RequestState:
    request: InferenceRequest
    phase: RequestPhase = RequestPhase.PREFILL
    generated_tokens: int = 0
    prefilled_tokens: int = 0

    # 这两个字段是运行期内部状态，不允许调用者在构造时传入。
    prefill_target_tokens: int = field(init=False)
    is_recomputing: bool = field(
        default=False,
        init=False,
    )
    preemption_count: int = field(
        default=0,
        init=False,
    )

    def __post_init__(self):
        # 首次 Prefill 只需要处理原始 Prompt
        self.prefill_target_tokens = self.request.prompt_tokens

    @property
    def remaining_prefill_tokens(self) -> int:
        return (
            self.prefill_target_tokens
            - self.prefilled_tokens
        )
    
    def run_prefill(self, max_tokens: int | None = None,) -> int:
        if self.phase != RequestPhase.PREFILL:
            raise RuntimeError(
                "只有 PREFILL 状态可以执行 prefill"
            )

        remaining = self.remaining_prefill_tokens

        if max_tokens is None:
            processed_tokens = remaining
        else:
            if max_tokens < 1:
                raise ValueError(
                "max_tokens 必须大于等于 1"
            )
            processed_tokens = min(
                remaining,
                max_tokens,
            )

        self.prefilled_tokens += processed_tokens

        # Prompt 尚未处理完，继续保持 PREFILL。
        if self.remaining_prefill_tokens > 0:
            return processed_tokens

        # 全部 Prompt 处理完成，得到首个输出 Token。
        # self.generated_tokens = 1
        if self.is_recomputing:
            # Recompute 只重建 KV，不生成新的输出 Token。
            self.is_recomputing = False
        else:
            # 首次 Prefill 完成时生成首个输出 Token。
            self.generated_tokens = 1

        if (
            self.generated_tokens
            >= self.request.max_new_tokens
        ):
            self.phase = RequestPhase.FINISHED
        else:
            self.phase = RequestPhase.DECODE

        return processed_tokens
        

    def run_decode_step(self):
        if self.phase != RequestPhase.DECODE:
            raise RuntimeError(
                "只有 DECODE 状态可以执行 decode"
            )
        # Decode 每一步只生成一个新 Token
        self.generated_tokens += 1

        if (
            self.generated_tokens >= self.request.max_new_tokens
        ):
            self.phase = RequestPhase.FINISHED

    def preempt_for_recompute(self):
        if self.phase != RequestPhase.DECODE:
            raise RuntimeError(
                "只有 DECODE 状态可以执行 preempt_for_recompute"
            )
        # 已生成最后一个 Token 尚未写入 KV，因此需要减一。
        self.prefill_target_tokens = (
            self.request.prompt_tokens + self.generated_tokens - 1
        )

        # KV 已经释放,从头计算进度
        self.prefilled_tokens = 0
        self.is_recomputing = True
        self.phase = RequestPhase.PREFILL

class BatchScheduler:
    def __init__(
            self,
            *,
            max_active_requests: int,
            queue_capacity: int,
            max_batch_tokens: int,
            kv_cache_capacity_tokens: int,
            kv_cache_block_size: int = 1,
            preemption_policy: PreemptionPolicy = PreemptionPolicy.NONE,
            max_preemptions_per_request: int = 1,
    ):
        if (
            isinstance(max_active_requests, bool)
            or not isinstance(max_active_requests, int)
            or max_active_requests < 1
        ):
            raise ValueError("max_active_requests 必须是正整数")

        if (
            isinstance(max_batch_tokens, bool)
            or not isinstance(max_batch_tokens, int)
            or max_batch_tokens < 1
        ):
            raise ValueError("max_batch_tokens 必须是正整数")

        self._request_queue = RequestQueue(
            capacity = queue_capacity,
        )

        self._max_active_requests = max_active_requests
        self._request_states: list[RequestState] = []
        self._max_batch_tokens = max_batch_tokens
        self._last_step_token_count: int = 0
        # 当前真正写入的 KV Token
        self._kv_cache = PagedKVCache(
            capacity_tokens = kv_cache_capacity_tokens,
            block_size=kv_cache_block_size,
        )
        # 活跃请求承诺占用的最大空间
        self._kv_reservations = PagedKVCache(
            capacity_tokens = kv_cache_capacity_tokens,
            block_size=kv_cache_block_size,
        )
        self._preempted_states: deque[RequestState] = deque()
        try:
            self._preemption_policy = PreemptionPolicy(
                preemption_policy,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "preemption_policy 必须是 PreemptionPolicy "
            )from exc
        self._total_preemptions = 0
        self._total_recomputed_tokens = 0
        if(
            isinstance(max_preemptions_per_request, bool)
            or not isinstance(max_preemptions_per_request, int)
            or max_preemptions_per_request < 1
        ):
            raise ValueError(
                "max_preemptions_per_request 必须是正整数")
        self._max_preemptions_per_request = max_preemptions_per_request


    def submit(self, request: InferenceRequest) -> None:
        required_kv_tokens = request.prompt_tokens + request.max_new_tokens - 1
        if required_kv_tokens > self._kv_cache.capacity_tokens:
            raise RequestKVCacheTooLargeError(
                "请求所需 KV Cache "
                f"{required_kv_tokens} tokens，"
                "超过系统总容量 "
                f"{self._kv_cache.capacity_tokens} tokens"
            )
        # 委托等待队列接收请求，不在这里执行推理。
        self._request_queue.submit(request)

    @property
    def waiting_count(self) -> int:
        return len(self._request_queue)

    @property
    def active_count(self) -> int:
        return len(self._request_states)

    @property
    def active_request_ids(self) -> tuple[str, ...]:
        return tuple(
            state.request.request_id
            for state in self._request_states
        )

    @property
    def last_step_token_count(self) -> int:
        return self._last_step_token_count

    @property
    def kv_cache_reserved_tokens(self) -> int:
        return self._kv_reservations.logical_tokens

    @property
    def kv_cache_used_tokens(self) -> int:
        return self._kv_cache.logical_tokens

    @property
    def kv_cache_allocated_tokens(self) -> int:
        return self._kv_cache.allocated_tokens

    @property
    def preempted_count(self) -> int:
        return len(self._preempted_states)

    @property
    def preempted_request_ids(self) -> tuple[str, ...]:
        return tuple(
            state.request.request_id
            for state in self._preempted_states
        )

    @property
    def total_preemptions(self) -> int:
        return self._total_preemptions

    @property
    def total_recomputed_tokens(self) -> int:
        return self._total_recomputed_tokens

    def preempt(self, request_id: str) -> None:
        for index, state in enumerate(self._request_states):
            if state.request.request_id != request_id:
                continue

            # 先转换状态；如果当前状态不能抢占，
            # preempt_for_recompute会在释放资源前抛错。
            state.preempt_for_recompute()
            self._kv_cache.release(request_id)
            self._kv_reservations.release(request_id)
            del self._request_states[index]
            self._preempted_states.append(state)
            state.preemption_count += 1
            self._total_preemptions += 1
            return
            
        raise KeyError(f"活跃请求不存在: {request_id}")

    def _select_preemption_victim(
            self,
    ) -> RequestState | None:
        for state in reversed(self._request_states):
            if state.phase != RequestPhase.DECODE:
                continue

            if (
                state.preemption_count >= self._max_preemptions_per_request
            ):
                continue

            return state
        return None

    def step(self) -> list[str]:
        self._last_step_token_count = 0
        while (
            self.active_count < self._max_active_requests
            and self.waiting_count > 0
        ):
            request = self._request_queue.peek_next()

            required_kv_tokens = (
                request.prompt_tokens
                + request.max_new_tokens
                -1
            )

            # if (
            #     required_kv_tokens > self._kv_reservations.available_tokens
            # ):
            #     break
            try:
                self._kv_reservations.append_tokens(
                    request.request_id,
                    required_kv_tokens,
                )

            except PagedKVCacheFullError:
                # 当前 KV 容量不足，请求继续留在等待队列。
                if (
                    self._preemption_policy
                    != PreemptionPolicy.RECOMPUTE_LAST
                ):
                    break

                victim = self._select_preemption_victim()

                if victim is None:
                    break

                self.preempt(
                    victim.request.request_id,
                )

                # 等待请求仍在队首。
                # 释放受害者资源后，重新尝试为它预留。

                continue

            request = self._request_queue.pop_next()

            # self._kv_reservations.append_tokens(
            #     request.request_id,
            #     required_kv_tokens,
            # )

            self._request_states.append(
                RequestState(
                    request = request,
                )
            )

        while(
            self.active_count < self._max_active_requests
            and self.preempted_count > 0
        ):
            state = self._preempted_states[0]
            request = state.request

            required_kv_tokens = (
                request.prompt_tokens
                + request.max_new_tokens
                -1
            )

            try:
                self._kv_reservations.append_tokens(
                    request.request_id,
                    required_kv_tokens,
                )
            except PagedKVCacheFullError:
                break
                # # 当前 KV 容量不足，请求继续留在等待队列。
                # if (
                #     self._preemption_policy
                #     != PreemptionPolicy.RECOMPUTE_LAST
                # ):
                #     break

                # victim = self._select_preemption_victim()

                # if victim is None:
                #     break

                # self.preempt(
                #     victim.request.request_id,
                # )

                # 等待请求仍在队首。
                # 释放受害者资源后，重新尝试为它预留。

                # continue

            # 只有预留成功后才能移动状态
            self._preempted_states.popleft()
            self._request_states.append(state)

        finished_ids: list[str] = []
        remaining_states: list[RequestState] = []

        for state in self._request_states:
            remaining_budget = (
                self._max_batch_tokens
                - self._last_step_token_count
            )

            if remaining_budget <= 0:
                remaining_states.append(state)
                continue

            request_id = state.request.request_id

            if state.phase == RequestPhase.PREFILL:
                # processed_tokens = state.run_prefill(
                #     max_tokens=remaining_budget,
                # )
                planned_tokens = min(
                    state.remaining_prefill_tokens,
                    remaining_budget,
                    # self._kv_cache.available_tokens,
                )

                # if planned_tokens <= 0:
                #     remaining_states.append(state)
                #     continue

                self._kv_cache.append_tokens(
                    request_id,
                    planned_tokens,
                )

                was_recomputing = state.is_recomputing

                processed_tokens = state.run_prefill(
                    max_tokens=planned_tokens,
                )

                if was_recomputing:
                    self._total_recomputed_tokens += (
                        processed_tokens
                    )
            
            elif state.phase == RequestPhase.DECODE:
                # if self._kv_cache.available_tokens < 1:
                #     remaining_states.append(state)
                #     continue

                self._kv_cache.append_tokens(
                    request_id,
                    1,
                )
                state.run_decode_step()
                processed_tokens = 1

            else:
                processed_tokens = 0

            self._last_step_token_count += processed_tokens

            if state.phase == RequestPhase.FINISHED:
                request_id = state.request.request_id
                finished_ids.append(
                    request_id
                )
                self._kv_cache.release(request_id=request_id)
                self._kv_reservations.release(request_id=request_id)
                
                
            else:
                remaining_states.append(state)


        self._request_states = remaining_states
        
        return finished_ids

