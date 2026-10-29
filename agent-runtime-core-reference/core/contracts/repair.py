"""Strict data contracts. No filesystem, provider, or sandbox dependencies."""

import re
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError("PATH_ESCAPE")
    if any(part.startswith(".") for part in path.parts):
        raise ValueError("HIDDEN_PATH")
    return value


class RepairLimits(StrictDTO):
    candidates: int = Field(default=3, ge=1, le=3)
    files: int = Field(default=3, ge=1, le=3)
    changed_lines: int = Field(default=200, ge=1, le=200)
    patch_bytes: int = Field(default=32768, ge=1, le=32768)
    wall_seconds: int = Field(default=10, ge=1, le=30)
    output_bytes: int = Field(default=262144, ge=1024, le=1048576)
    max_steps: int = Field(default=9, ge=3, le=9)


class CaseManifest(StrictDTO):
    schema_version: Literal["1"] = "1"
    case_id: str = Field(pattern=r"^case-[0-9]{2}$")
    split: Literal["dev", "holdout"]
    goal: str = Field(min_length=1, max_length=2000)
    base_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    snapshot_hash: Hash | None = None
    repo_content_hash: Hash
    image_digest: str = Field(pattern=r"^python:3\.11\.17-slim-bookworm@sha256:[0-9a-f]{64}$")
    test_suite_hash: Hash
    runner_hash: Hash
    allowed_paths: list[str] = Field(min_length=1, max_length=3)
    forbidden_paths: list[str] = Field(min_length=1)
    expected_test_ids: list[str] = Field(min_length=1, max_length=100)
    baseline_fail: list[str] = Field(min_length=1)
    baseline_pass: list[str]
    test_argv: list[str] = Field(min_length=1)
    limits: RepairLimits = Field(default_factory=RepairLimits)

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def paths(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("DUPLICATE_PATH")
        return [safe_path(v) for v in values]

    @model_validator(mode="after")
    def consistent(self) -> "CaseManifest":
        if (self.base_revision is None) == (self.snapshot_hash is None):
            raise ValueError("EXACTLY_ONE_BASE_IDENTITY_REQUIRED")
        if self.snapshot_hash and self.snapshot_hash != self.repo_content_hash:
            raise ValueError("SNAPSHOT_MISMATCH")
        for path in self.allowed_paths:
            if (
                not path.startswith("src/")
                or not path.endswith(".py")
                or PurePosixPath(path).name == "conftest.py"
            ):
                raise ValueError("SOURCE_PATH_NOT_APPROVED")
            if any(path == p or path.startswith(p + "/") for p in self.forbidden_paths):
                raise ValueError("PROTECTED_PATH")
        for values in (self.expected_test_ids, self.baseline_fail, self.baseline_pass):
            if len(values) != len(set(values)) or any(
                not re.fullmatch(r"[A-Za-z0-9_.]+", v) for v in values
            ):
                raise ValueError("INVALID_TEST_IDS")
        if set(self.baseline_fail) & set(self.baseline_pass) or set(
            self.baseline_fail + self.baseline_pass
        ) != set(self.expected_test_ids):
            raise ValueError("BASELINE_PARTITION_MISMATCH")
        return self


class PatchEdit(StrictDTO):
    path: str
    old: str = Field(min_length=1)
    new: str

    @field_validator("path")
    @classmethod
    def path_valid(cls, value: str) -> str:
        return safe_path(value)


class Patch(StrictDTO):
    edits: list[PatchEdit] = Field(min_length=1, max_length=3)


class VerificationIdentity(StrictDTO):
    task_id: str = Field(min_length=1)
    candidate_no: int = Field(ge=0, le=3)
    candidate_hash: Hash
    manifest_hash: Hash
    verification_run_id: str = Field(min_length=1)
    nonce: str = Field(min_length=32)
    image_digest: str
    test_suite_hash: Hash
    repo_content_hash: Hash
    runner_hash: Hash


class TestRecord(StrictDTO):
    test_id: str
    outcome: Literal["passed", "failed", "error", "skipped", "xfail", "xpass"]


class RunnerReport(StrictDTO):
    schema_version: Literal["1"]
    identity: VerificationIdentity
    collected_ids: list[str]
    tests: list[TestRecord]
    protected_hash: Hash
    complete: bool


class RunRecord(StrictDTO):
    """Constructed by trusted controller; never parsed from candidate output."""

    identity: VerificationIdentity
    started: bool
    exit_code: int | None
    timed_out: bool = False
    resource_error: bool = False
    cleanup_confirmed: bool = False
    report_sha256: Hash | None = None


class Verdict(StrictDTO):
    status: Literal["PASS", "FAIL", "ERROR", "NOT_RUN", "INCOMPLETE"]
    reason_code: str
    identity: VerificationIdentity
    failed_ids: list[str] = Field(default_factory=list)
