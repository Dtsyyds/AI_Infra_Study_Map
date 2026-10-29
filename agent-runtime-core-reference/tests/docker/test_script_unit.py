"""固定程序的宿主子进程单元测试；这些通过不证明 Docker 隔离有效。"""

import json
import subprocess
import sys

from runtime.tools.docker_backend import CHECK_SCRIPT


def test_script_uses_changed_amount_and_attachments_not_document_identity() -> None:
    rules = [
        {"rule_id": "R1", "rule_type": "approval_threshold", "threshold": 10000,
         "version": "1", "source": "fictional://rule/approval"},
        {"rule_id": "R2", "rule_type": "invoice_required", "version": "1",
         "source": "fictional://rule/invoice"},
    ]
    document = {"document_id": "DOC-001", "version": "1", "amount": 12000,
                "attachments": ["invoice"]}

    def run():
        process = subprocess.run(
            [sys.executable, "-I", "-c", CHECK_SCRIPT],
            input=json.dumps({"document": document, "rules": rules}), text=True,
            capture_output=True, timeout=5, check=True,
        )
        return json.loads(process.stdout)

    assert run()["review_decision"] == "NEEDS_DOCUMENTS"
    document["amount"] = 8000
    assert run()["review_decision"] == "PASS"
    document["attachments"] = []
    assert run()["missing_documents"] == ["invoice"]
