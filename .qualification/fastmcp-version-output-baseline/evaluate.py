"""Strict baseline evidence evaluator; never transforms native outcomes."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

def evaluate(proof, source, fixture, manifest):
    collection=json.loads((proof/'collection.json').read_text());result=json.loads((proof/'outcomes.json').read_text())
    if collection['collection_reports']or result['collection_reports']or collection['reports']:raise RuntimeError('Collection failed or collected test executed')
    if collection['collected']!=result['collected']or len(set(result['collected']))!=12:raise RuntimeError('Missing/duplicate actual identities')
    normalized={}
    for node in result['collected']:
        path,rest=node.split('::',1)
        if collection['collection_source_paths']!=result['collection_source_paths'] or Path(result['collection_source_paths'][node]).resolve()!=fixture.resolve():raise RuntimeError('Wrong collected test source '+node)
        filename=Path(result['collection_source_paths'][node]).name
        if path and Path(path).name!=filename:raise RuntimeError('Nonempty node prefix does not match bound file')
        key=filename+'::'+rest
        if key in normalized:raise RuntimeError('Duplicate normalized identity')
        normalized[key]=node
    if set(normalized)!=set(manifest['expectedCases']):raise RuntimeError('Unexpected/missing test cases')
    grouped={node:{}for node in result['collected']}
    for report in result['reports']:
        if report['nodeid']not in grouped or report['when']in grouped[report['nodeid']]:raise RuntimeError('Unknown/duplicate report')
        grouped[report['nodeid']][report['when']]=report
    for key,node in normalized.items():
        phases=grouped[node]
        if set(phases)!={'setup','call','teardown'}or any(phases[p]['outcome']!='passed'for p in ['setup','teardown']):raise RuntimeError('Setup/teardown/fixture error')
        call=phases['call']
        props=dict(call.get('properties',[]));mode=key.rsplit('-',1)[1].removesuffix(']')
        if props!={'requested_mode':mode,'negotiated_protocol':('2026-07-28' if mode=='auto'else'2025-11-25')}:raise RuntimeError('Actual negotiated protocol missing/wrong')
        expected=manifest['expectedCases'][key]
        if call['outcome']!=expected or call['wasxfail']is not None:raise RuntimeError('Wrong failure/control outcome '+key)
        if expected=='failed':
            text=call['longrepr']or''
            if 'compatible-schema'in key:
                if 'AssertionError'not in text or 'None'not in text:raise RuntimeError('Wrong silentdata failure')
            elif "RuntimeError: Invalid structured content returned by tool value"not in text or "7 is not of type 'string'"not in text:raise RuntimeError('Wrong schema failure')
    xml=ET.parse(proof/'regression.xml');cases=list(xml.iter('testcase'))
    xml_names=[case.get('name') for case in cases]
    expected_names={key.split('::',1)[1]:outcome for key,outcome in manifest['expectedCases'].items()}
    if len(set(xml_names))!=len(xml_names) or set(xml_names)!=set(expected_names):raise RuntimeError('JUnit identity mismatch')
    for case in cases:
        name=case.get('name')
        if (case.find('failure') is not None)!=(expected_names[name]=='failed'):raise RuntimeError('JUnit case outcome mismatch')
    if len(cases)!=12 or any(c.find('error')is not None or c.find('skipped')is not None for c in cases)or sum(c.find('failure')is not None for c in cases)!=6:raise RuntimeError('JUnit errors/skips/failcount')
    return {"cases":len(normalized),"failureIDs":sorted(k for k,v in manifest["expectedCases"].items() if v=="failed")}
