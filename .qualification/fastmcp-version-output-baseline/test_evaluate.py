"""Synthetic interventions on the evaluator, never native product evidence."""
import copy,json,tempfile,unittest
from pathlib import Path
import xml.etree.ElementTree as ET
from evaluate import evaluate
MANIFEST=json.loads(Path(__file__).with_name('manifest.json').read_text())
class Controls(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name);source=root/'source';proof=root/'proof';proof.mkdir();fixture=root/'harness/test_version_output.py'
        nodes=[str(fixture)+'::'+key.split('::',1)[1] for key in MANIFEST['expectedCases']]
        reports=[];suite=ET.Element('testsuite')
        for key,node in zip(MANIFEST['expectedCases'],nodes):
            outcome=MANIFEST['expectedCases'][key];mode=key.rsplit('-',1)[1].removesuffix(']');text=None
            if outcome=='failed':text='AssertionError: assert None == 7' if 'compatible-schema'in key else "RuntimeError: Invalid structured content returned by tool value: 7 is not of type 'string'"
            for when,status in [('setup','passed'),('call',outcome),('teardown','passed')]:reports.append({'nodeid':node,'when':when,'outcome':status,'longrepr':text if when=='call'else None,'wasxfail':None,'properties':[['requested_mode',mode],['negotiated_protocol','2026-07-28'if mode=='auto'else'2025-11-25']]})
            case=ET.SubElement(suite,'testcase',classname='test_version_output',name=key.split('::',1)[1])
            if outcome=='failed':ET.SubElement(case,'failure').text=text
        collection={'collected':nodes,'reports':[],'collection_reports':[],'collection_source_paths':{node:str(fixture)for node in nodes}};result={'collected':nodes,'reports':reports,'collection_reports':[],'collection_source_paths':{node:str(fixture)for node in nodes}}
        return source,proof,fixture,collection,result,suite
    def check(self,change=None):
        source,proof,fixture,collection,result,suite=self.fixture()
        if change:change(collection,result,suite)
        (proof/'collection.json').write_text(json.dumps(collection));(proof/'outcomes.json').write_text(json.dumps(result));ET.ElementTree(suite).write(proof/'regression.xml')
        return evaluate(proof,source,fixture,MANIFEST)
    def test_valid(self):self.assertEqual(self.check()['cases'],12)
    def test_actual_bare_prefix_layout(self):
        def bare(c,r,xml):
            mapping={node:'::'+node.split('::',1)[1]for node in r['collected']}
            for value in [c,r]:
                value['collected']=[mapping[node]for node in value['collected']]
                value['collection_source_paths']={mapping[node]:path for node,path in value['collection_source_paths'].items()}
            for report in r['reports']:report['nodeid']=mapping[report['nodeid']]
        self.assertEqual(self.check(bare)['cases'],12)

    def test_interventions(self):
        def calls(r):return [x for x in r['reports']if x['when']=='call']
        def wrong_prefix(c,r,xml):
            mapping={node:'wrong.py::'+node.split('::',1)[1]for node in r['collected']}
            for value in [c,r]:
                value['collected']=[mapping[node]for node in value['collected']]
                value['collection_source_paths']={mapping[node]:path for node,path in value['collection_source_paths'].items()}
            for report in r['reports']:report['nodeid']=mapping[report['nodeid']]
        changes={
          'nonempty-wrong-prefix':wrong_prefix,
          'wrong-source-path-both':lambda c,r,x:(c['collection_source_paths'].update({c['collected'][0]:'/wrong/test_version_output.py'}),r['collection_source_paths'].update({r['collected'][0]:'/wrong/test_version_output.py'})),
          'setup-error':lambda c,r,x:r['reports'][0].update(outcome='failed'),
          'teardown-error':lambda c,r,x:r['reports'][2].update(outcome='failed'),
          'missing-case':lambda c,r,x:r['collected'].pop(),
          'duplicate-case':lambda c,r,x:(c['collected'].append(c['collected'][0]),r['collected'].append(r['collected'][0])),
          'duplicate-report':lambda c,r,x:r['reports'].append(copy.deepcopy(r['reports'][0])),
          'wrong-regression':lambda c,r,x:next(a for a in calls(r)if a['outcome']=='failed').update(longrepr='RuntimeError: unrelated network failure'),
          'positive-control-fails':lambda c,r,x:calls(r)[0].update(outcome='failed'),
          'expected-failure-passes':lambda c,r,x:next(a for a in calls(r)if a['outcome']=='failed').update(outcome='passed'),
          'skip':lambda c,r,x:calls(r)[0].update(outcome='skipped'),
          'xfail':lambda c,r,x:calls(r)[0].update(wasxfail='invented'),
          'protocol-missing':lambda c,r,x:calls(r)[0].update(properties=[]),
          'protocol-wrong':lambda c,r,x:calls(r)[0].update(properties=[['requested_mode','auto'],['negotiated_protocol','other']]),
          'collection-error':lambda c,r,x:c['collection_reports'].append({'outcome':'failed'}),
          'collection-executed':lambda c,r,x:c['reports'].append({'outcome':'passed'}),
          'xml-identity':lambda c,r,x:x[0].set('name','unrelated'),
          'xml-outcome':lambda c,r,x:ET.SubElement(x[0],'failure'),
          'xml-error':lambda c,r,x:ET.SubElement(x[0],'error'),
          'xml-skip':lambda c,r,x:ET.SubElement(x[0],'skipped'),
        }
        for name,change in changes.items():
            with self.subTest(name=name),self.assertRaises(RuntimeError):self.check(change)
if __name__=='__main__':unittest.main()
