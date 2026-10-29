"""Trusted stdlib test runner. Executed ONLY inside the pinned Docker image."""
import contextlib
import hashlib
import io
import json
import pathlib
import signal
import sys
import unittest


def digest(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def main():
    data = json.load(sys.stdin)
    root = pathlib.Path('/tmp/repair')
    root.mkdir()
    for name, content in data['files'].items():
        path = pathlib.PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('invalid payload path')
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    print('ARC_REPAIR_STARTED', flush=True)
    sys.path.insert(0, str(root/'src'))
    collected, records = [], []

    class Result(unittest.TestResult):
        def startTest(self, test):
            super().startTest(test)
            if data.get('test_pause'):
                # Trusted process-only fault switch, never a model/API tool argument.
                print('ARC_REPAIR_TEST_ENTERED', file=sys.__stdout__, flush=True)
                while True:
                    signal.pause()

        def addSuccess(self, test):
            super().addSuccess(test)
            records.append({'test_id': test.id(), 'outcome': 'passed'})

        def addFailure(self, test, error):
            super().addFailure(test, error)
            records.append({'test_id': test.id(), 'outcome': 'failed'})

        def addError(self, test, error):
            super().addError(test, error)
            # An unexpected exception inside a collected test is a candidate test failure.
            # Collection/import failures instead fail the expected-ID completeness gate.
            records.append({'test_id': test.id(), 'outcome': 'failed'})

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            records.append({'test_id': test.id(), 'outcome': 'skipped'})

        def addExpectedFailure(self, test, error):
            super().addExpectedFailure(test, error)
            records.append({'test_id': test.id(), 'outcome': 'xfail'})

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            records.append({'test_id': test.id(), 'outcome': 'xpass'})

    def collect(suite):
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                collect(test)
            else:
                collected.append(test.id())

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        suite = unittest.TestLoader().discover(str(root/'tests'))
        collect(suite)
        result = Result()
        suite.run(result)
    protected = {}
    for name in data['files']:
        if name not in data['allowed_paths']:
            p = root/name
            if p.is_symlink():
                raise ValueError('protected symlink')
            protected[name] = p.read_text()
    report = {'schema_version': '1', 'identity': data['identity'], 'collected_ids': collected,
              'tests': records, 'protected_hash': digest(protected), 'complete': True}
    print('ARC_REPAIR_REPORT '+json.dumps(report, sort_keys=True), flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
