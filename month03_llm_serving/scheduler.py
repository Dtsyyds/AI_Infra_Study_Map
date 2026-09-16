from dataclasses import dataclass
from enum import Enum

from month03_llm_serving.request_queue import InferenceRequest, RequestQueue
from month03_llm_serving.kv_cache import KVCache
from month03_llm_serving.paged_kv_cache import PagedKVCache

class RequestKVCacheTooLargeError(ValueError):
    """ 超过系统容量 """

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

    @property
    def remaining_prefill_tokens(self) -> int:
        return (
            self.request.prompt_tokens
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

class BatchScheduler:
    def __init__(
            self,
            *,
            max_active_requests: int,
            queue_capacity: int,
            max_batch_tokens: int,
            kv_cache_capacity_tokens: int,
            kv_cache_block_size: int = 1
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
    def kv_cache_used_tokens(self) -> int:
        return self._kv_cache.used_tokens

    @property
    def kv_cache_reserved_tokens(self) -> int:
        return self._kv_reservations.used_tokens

    @property
    def kv_cache_used_tokens(self) -> int:
        return self._kv_cache.logical_tokens

    @property
    def kv_cache_allocated_tokens(self) -> int:
        return self._kv_cache.allocated_tokens

    @property
    def kv_cache_reserved_tokens(self) -> int:
        return self._kv_reservation_pool.logical_tokens

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

            if (
                required_kv_tokens > self._kv_reservations.available_tokens
            ):
                break

            request = self._request_queue.pop_next()

            self._kv_reservations.append_tokens(
                request.request_id,
                required_kv_tokens,
            )

            self._request_states.append(
                RequestState(
                    request = request,
                )
            )

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

            if state.phase == RequestPhase.PREFILL:
                # processed_tokens = state.run_prefill(
                #     max_tokens=remaining_budget,
                # )
                planned_tokens = min(
                    state.remaining_prefill_tokens,
                    remaining_budget,
                    self._kv_cache.available_tokens,
                )

                if planned_tokens <= 0:
                    remaining_states.append(state)
                    continue

                self._kv_cache.append_tokens(
                    state.request.request_id,
                    planned_tokens,
                )

                processed_tokens = state.run_prefill(
                    max_tokens=planned_tokens,
                )
            
            elif state.phase == RequestPhase.DECODE:
                if self._kv_cache.available_tokens < 1:
                    remaining_states.append(state)
                    continue

                self._kv_cache.append_tokens(
                    state.request.request_id,
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

