from dataclasses import dataclass


@dataclass(frozen=True)
class AdmissionPolicy:
    active_limit: int = 3
    rate_per_minute: int = 30
