import json, os
from pathlib import Path
STATE={"collected":[],"reports":[],"collection_reports":[],"collection_source_paths":{}}
def pytest_collection_finish(session):
    STATE["collected"]=[item.nodeid for item in session.items]
    STATE["collection_source_paths"]={item.nodeid:str(item.path.resolve())for item in session.items}
def pytest_runtest_logreport(report):
    STATE["reports"].append({"nodeid":report.nodeid,"when":report.when,"outcome":report.outcome,"longrepr":str(report.longrepr) if report.longrepr else None,"wasxfail":getattr(report,"wasxfail",None),"properties":list(report.user_properties)})
def pytest_collectreport(report):
    if report.outcome!="passed":STATE["collection_reports"].append({"nodeid":report.nodeid,"outcome":report.outcome,"longrepr":str(report.longrepr)})
def pytest_sessionfinish(session,exitstatus):
    STATE.update(exitstatus=int(exitstatus),argv=list(session.config.invocation_params.args))
    Path(os.environ["FAST_MCP_BASELINE_REPORT"]).write_text(json.dumps(STATE,indent=2))
