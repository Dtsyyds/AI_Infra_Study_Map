class RuntimeFault(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail or code
        super().__init__(self.detail)


class LeaseLost(RuntimeFault):
    def __init__(self) -> None:
        super().__init__("LEASE_UNCERTAIN")
