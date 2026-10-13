"""Read-only native pytest inventory/report collector, unique per xdist worker."""
import json, os, re
from pathlib import Path
import pytest
from _pytest.junitxml import mangle_test_address

STATE = {'collected': [], 'reports': [], 'collection_reports': []}
CONFIG = None


def pytest_configure(config):
    global CONFIG
    CONFIG = config


def pytest_collection_finish(session):
    rows = []
    for item in session.items:
        names = mangle_test_address(item.nodeid)
        rows.append({'nodeid': item.nodeid, 'junit_classname': '.'.join(names[:-1]),
                     'junit_name': names[-1]})
    STATE['collected'] = rows


def pytest_runtest_logreport(report):
    reason = list(report.longrepr) if report.skipped and isinstance(report.longrepr, tuple) else (
        str(report.longrepr) if report.longrepr else None)
    STATE['reports'].append({'nodeid': report.nodeid, 'when': report.when,
                            'outcome': report.outcome, 'reason': reason, 'wasxfail': getattr(report,'wasxfail',None)})


def pytest_collectreport(report):
    if report.outcome != 'passed':
        names = mangle_test_address(report.nodeid)
        STATE['collection_reports'].append({'nodeid': report.nodeid, 'outcome': report.outcome,
                                            'reason': str(report.longrepr),
                                            'junit_classname': '.'.join(names[:-1]),
                                            'junit_name': names[-1]})


def pytest_sessionfinish(session, exitstatus):
    worker = getattr(session.config, 'workerinput', {}).get('workerid', 'controller')
    if not re.fullmatch(r'(?:controller|gw[0-9]+)', worker):
        raise RuntimeError('Unexpected worker identity')
    directory = Path(os.environ['NANOBOT_QUALIFICATION_REPORT_DIR'])
    directory.mkdir(parents=True, exist_ok=True)
    STATE.update(worker=worker, exitstatus=int(exitstatus), invocation=list(session.config.invocation_params.args),
                 pytest_version=pytest.__version__)
    (directory / (worker + '.json')).write_text(json.dumps(STATE, indent=2), encoding='utf-8')
