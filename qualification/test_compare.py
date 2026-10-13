"""Interventions on receipt semantics; these are evaluator controls, not native results."""
import copy, datetime, hashlib, json, tempfile, unittest
from pathlib import Path
import xml.etree.ElementTree as ET
from compare import MANIFEST, expected_commands, compare_pair

HELPERS=['native.py','compare.py','collector.py','test_compare.py','manifest.json','xlsx-locators-fixture.py',
         'docker-contract.json','docker-compose-verify.sh','docker-permissions.sh']
ORIGINAL='tests/original.py::test_original'
SKIP='tests/original.py::test_existing_skip'


def save(path,value):path.write_text(json.dumps(value,indent=2),encoding='utf-8')


def pytest_receipt(directory,label,outcomes,exitstatus,argv):
    rows=[];reports=[];suite=ET.Element('testsuite');mapping={}
    for node,outcome in outcomes.items():
        address,_,parameter=node.partition('[');names=address.split('::');names[0]=names[0].removesuffix('.py').replace('/','.')
        if parameter:names[-1]+='['+parameter
        classname,name='.'.join(names[:-1]),names[-1]
        rows.append({'nodeid':node,'junit_classname':classname,'junit_name':name})
        def phase(when,status,reason=None):
            reports.append({'nodeid':node,'when':when,'outcome':status,'reason':reason,'wasxfail':None})
        reason=['tests/original.py',2,'Skipped: existing unavailable fixture'] if outcome=='skipped' else None
        if outcome=='skipped':phase('setup','skipped',reason)
        else:phase('setup','passed');phase('call',outcome,'actual assertion failed' if outcome=='failed' else None)
        phase('teardown','passed')
        case=ET.SubElement(suite,'testcase',classname=classname,name=name)
        if outcome=='failed':ET.SubElement(case,'failure').text='actual assertion failed'
        if outcome=='skipped':ET.SubElement(case,'skipped').text=str(reason)
    report_dir=directory/(label+'-reports');report_dir.mkdir()
    controller={'worker':'controller','exitstatus':exitstatus,'collected':rows,'reports':reports,
                'collection_reports':[],'invocation':argv[6:],'pytest_version':'9.0.2'}
    save(report_dir/'controller.json',controller)
    collect=directory/(label+'-collection-reports');collect.mkdir()
    independent=dict(controller,collected=rows,reports=[],exitstatus=0)
    save(collect/'controller.json',independent)
    ET.ElementTree(suite).write(directory/(label+'.xml'),encoding='utf-8',xml_declaration=True)


class NativeEvidenceControls(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.lane='Linux311';self.seed(self.lane)

    def seed(self,lane):
        self.lane=lane
        for phase in ['baseline','fixed']:
            d=self.root/phase
            if d.exists():
                import shutil;shutil.rmtree(d)
            d.mkdir();head=MANIFEST['base'] if phase=='baseline' else MANIFEST['fixed'];tree=MANIFEST['base_tree'] if phase=='baseline' else MANIFEST['fixed_tree'];system=MANIFEST['lanes'][lane]['os']
            env={'phase':phase,'lane':lane,'head':head,'tree':tree,'system':system,'runner_os':system,
                 'sandbox_home':'C:\\sandbox\\runneradmin' if system=='Windows' else '/empty/owned/home','actual_username':'runneradmin' if system=='Windows' else None,'harness_head':'a'*40,'proof':'C:\\proof' if system=='Windows' else '/proof',
                 'harness':'C:\\harness\\qualification' if system=='Windows' else '/harness/qualification',
                 'runner_temp':'/runner/temp','source':'C:\\source' if system=='Windows' else '/source'}
            save(d/'environment.json',env);save(d/'result.json',{'status':'PASS','phase':phase,'lane':lane,'head':head})
            save(d/'final-identity.json',{'head':head,'tree':tree,'status':''});(d/'source-status.txt').write_text('')
            files=MANIFEST['tracked_files'][phase]
            blobs={n:{'mode':v['mode'],'git_blob':v['git_blob'],'expected_sha256':v['sha256'],'actual_sha256':v['sha256'],
                      'storage':'symlink' if v['mode']=='120000' else 'file'} for n,v in files.items()}
            for label in ['tracked-before','tracked-after','tracked-final']:save(d/(label+'.json'),blobs)
            names=sorted(n for n in files if n.endswith('.py'))
            for label in ['python-inventory-before','python-inventory-after']:save(d/(label+'.json'),{'expected':names,'actual':names})
            save(d/'fixture-bytes.json',{'actual_sha256':MANIFEST['fixture_sha256'],'git_blob_sha256':MANIFEST['fixture_sha256'],
                                        'expected_sha256':MANIFEST['fixture_sha256'],'actual_crlf_count':0})
            save(d/'harness-hashes.json',{n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in HELPERS})
            deployed={}
            for name in HELPERS+['nanobot-xlsx-native.yml']:
                raw=Path(__file__).with_name(name).read_bytes()
                key='.github/workflows/'+name if name.endswith('.yml') else 'qualification/'+name
                deployed[key]={'mode':'100644','git_blob':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'sha256':hashlib.sha256(raw).hexdigest()}
            for label in ['harness-before','harness-final']:
                save(d/(label+'.json'),{'head':'a'*40,'tree':'b'*40,'parent':MANIFEST['deployment']['harness_parent'],'files':deployed,'untracked':[],'ignored_python':[]})
            if lane!='Docker':save(d/'runtime.json' ,{'minor':MANIFEST['lanes'][lane]['python'],'system':system,'python':'actual runtime','home':env['sandbox_home'],'resolved_home':env['sandbox_home'],'username':env['actual_username'],'userprofile':env['sandbox_home'],'named_root_expansion':'C:\\sandbox\\root/.bashrc'})
            commands=[]
            for label,(argv,code,extra) in expected_commands(lane,phase,env).items():
                if extra is None:
                    from pathlib import PureWindowsPath,PurePosixPath
                    pp=PureWindowsPath if system=='Windows' else PurePosixPath
                    extra={'PYTEST_PLUGINS':'collector','NANOBOT_QUALIFICATION_REPORT_DIR':str(pp(env['proof'])/(label+'-reports')),'PYTHONPATH':env['harness']}
                c={'label':label,'argv':argv,'cwd':env['source'],'exit':code,'expected_exit':code,'extra_env':extra,
                   'start':'2026-10-13T00:00:00+00:00','end':'2026-10-13T00:00:01+00:00'}
                for channel in ['stdout','stderr']:
                    (d/(label+'.'+channel)).write_bytes(b'');c[channel+'_sha256']=hashlib.sha256(b'').hexdigest()
                commands.append(c)
                if label.startswith('pytest-') and not label.endswith('-collection'):
                    if label=='pytest-focus':
                        cases={n:('failed' if phase=='baseline' and n in MANIFEST['baseline_focus_fail'] else 'passed') for n in MANIFEST['new_ids']}
                    else:
                        cases={ORIGINAL:'passed',SKIP:'skipped'}
                        if label=='pytest-full' and phase=='fixed':cases.update({n:'passed' for n in MANIFEST['new_ids']})
                    pytest_receipt(d,label,cases,code,argv)
            for c in commands:
                files=list((d/(c['label']+'-reports')).glob('*.json'))
                xml=d/(c['label']+'.xml')
                if xml.exists():files.append(xml)
                c['report_sha256']={f.relative_to(d).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(files)}
            save(d/'commands.json',commands)
            if lane not in ['Docker','WindowsProcess']:
                save(d/'fixture-relation.json',{'literal_bytes':True,'sha256':MANIFEST['fixture_sha256'],'transplanted_after_original_suite':phase=='baseline'})

    def data(self,phase,name):return json.loads((self.root/phase/name).read_text())
    def alter(self,phase,name,fn):
        value=self.data(phase,name);fn(value);save(self.root/phase/name,value)
    def outcome(self,phase,label,node,outcome,reason=None):
        d=self.root/phase;file=d/(label+'-reports')/'controller.json';report=json.loads(file.read_text())
        terminal=next(r for r in report['reports'] if r['nodeid']==node and r['when']=='call')
        terminal.update(outcome=outcome,reason=reason);save(file,report)
        item=next(i for i in report['collected'] if i['nodeid']==node)
        xml=d/(label+'.xml');tree=ET.parse(xml)
        case=next(c for c in tree.getroot() if c.get('classname')==item['junit_classname'] and c.get('name')==item['junit_name'])
        for child in list(case):case.remove(child)
        if outcome=='failed':ET.SubElement(case,'failure').text='actual assertion failed'
        if outcome=='skipped':ET.SubElement(case,'skipped').text=str(reason)
        tree.write(xml)
    def compare(self):return compare_pair(self.root/'baseline',self.root/'fixed',self.lane)
    def reject(self, reseal=True):
        # Semantic interventions retain coherent byte receipts, so rejection must
        # come from the altered gate semantics rather than a stale report hash.
        if reseal:
            for phase in ['baseline','fixed']:
                d=self.root/phase;commands=self.data(phase,'commands.json')
                for c in commands:
                    files=list((d/(c['label']+'-reports')).glob('*.json'))
                    xml=d/(c['label']+'.xml')
                    if xml.exists():files.append(xml)
                    c['report_sha256']={f.relative_to(d).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(files)}
                save(d/'commands.json',commands)
        with self.assertRaises((RuntimeError,KeyError,ValueError,FileNotFoundError,ET.ParseError)):self.compare()

    def test_valid_all_five_lane_contracts(self):
        for lane in MANIFEST['lanes']:
            with self.subTest(lane=lane):self.seed(lane);self.assertEqual(self.compare()['status'],'PASS')
    def test_native_failure(self):self.alter('fixed','result.json',lambda x:x.update(status='FAIL'));self.reject()
    def test_wrong_head(self):self.alter('fixed','environment.json',lambda x:x.update(head='b'*40));self.reject()
    def test_wrong_tree(self):self.alter('baseline','environment.json',lambda x:x.update(tree='b'*40));self.reject()
    def test_wrong_os(self):self.alter('fixed','environment.json',lambda x:x.update(system='Darwin'));self.reject()
    def test_wrong_runtime(self):self.alter('fixed','runtime.json',lambda x:x.update(minor='3.13'));self.reject()
    def test_harness_revision_mixed(self):self.alter('fixed','environment.json',lambda x:x.update(harness_head='b'*40));self.reject()
    def test_missing_command(self):self.alter('fixed','commands.json',lambda x:x.pop());self.reject()
    def test_reordered_commands(self):self.alter('baseline','commands.json',lambda x:x.reverse());self.reject()
    def test_narrowed_command(self):self.alter('fixed','commands.json',lambda x:x[0]['argv'].append('--invented'));self.reject()
    def test_nonzero_canonical_command(self):self.alter('fixed','commands.json',lambda x:x[0].update(exit=1));self.reject()
    def test_expected_exit_forged(self):self.alter('fixed','commands.json',lambda x:x[0].update(expected_exit=1));self.reject()
    def test_naive_command_time(self):self.alter('fixed','commands.json',lambda x:x[0].update(start='2026-10-13T00:00:00'));self.reject()
    def test_reversed_command_time(self):self.alter('fixed','commands.json',lambda x:x[0].update(start='2026-10-14T00:00:00+00:00'));self.reject()
    def test_raw_log_tamper(self):(self.root/'fixed'/'uv-version.stdout').write_text('changed');self.reject()
    def test_source_byte_mutation(self):
        for label in ['tracked-before','tracked-after','tracked-final']:
            self.alter('fixed',label+'.json',lambda x:x[MANIFEST['fixture_path']].update(actual_sha256='0'*64,expected_sha256='0'*64))
        self.reject()
    def test_original_file_missing_in_all_snapshots(self):
        name=next(iter(MANIFEST['tracked_files']['baseline']))
        for phase in ['baseline','fixed']:
            for label in ['tracked-before','tracked-after','tracked-final']:self.alter(phase,label+'.json',lambda x:x.pop(name))
        self.reject()
    def test_original_git_blob_forged(self):self.alter('baseline','tracked-before.json',lambda x:next(iter(x.values())).update(git_blob='f'*40));self.reject()
    def test_python_source_shim(self):self.alter('baseline','python-inventory-before.json',lambda x:x['actual'].append('nanobot/shim.py'));self.reject()
    def test_final_untracked_source(self):self.alter('fixed','final-identity.json',lambda x:x.update(status='?? nanobot/shim.py'));self.reject()
    def test_fixture_crlf(self):self.alter('baseline','fixture-bytes.json',lambda x:x.update(actual_crlf_count=1));self.reject()
    def test_fixture_hash_wrong(self):self.alter('baseline','fixture-relation.json',lambda x:x.update(sha256='0'*64));self.reject()
    def test_fixture_before_pristine_suite(self):self.alter('baseline','fixture-relation.json',lambda x:x.update(transplanted_after_original_suite=False));self.reject()
    def test_helper_hash_forged(self):self.alter('fixed','harness-hashes.json',lambda x:x.update({'collector.py':'0'*64}));self.reject()
    def test_collector_path_forged(self):
        self.alter('fixed','commands.json',lambda x:next(c for c in x if c['label']=='pytest-full')['extra_env'].update(PYTHONPATH='/unreviewed'))
        self.reject()
    def test_lost_original_execution(self):
        self.alter('fixed','pytest-full-reports/controller.json',lambda x:x.update(reports=[r for r in x['reports'] if r['nodeid']!=ORIGINAL]));self.reject()
    def test_original_lost_from_both_canonical_inventories(self):
        for phase in ['baseline','fixed']:
            self.alter(phase,'pytest-full-reports/controller.json',lambda x:x.update(collected=[r for r in x['collected'] if r['nodeid']!=ORIGINAL],reports=[r for r in x['reports'] if r['nodeid']!=ORIGINAL]))
            file=self.root/phase/'pytest-full.xml';tree=ET.parse(file)
            case=next(c for c in tree.getroot() if c.get('name')=='test_original');tree.getroot().remove(case);tree.write(file)
        self.reject()
    def test_independent_collection_executed(self):self.alter('fixed','pytest-full-collection-reports/controller.json',lambda x:x['reports'].append({'unexpected':'execution'}));self.reject()
    def test_setup_error(self):self.alter('fixed','pytest-full-reports/controller.json',lambda x:x['reports'][0].update(outcome='failed'));self.reject()
    def test_teardown_error(self):self.alter('fixed','pytest-full-reports/controller.json',lambda x:x['reports'][2].update(outcome='failed'));self.reject()
    def test_duplicate_report(self):self.alter('fixed','pytest-full-reports/controller.json',lambda x:x['reports'].append(x['reports'][0]));self.reject()
    def test_unknown_report_identity(self):self.alter('fixed','pytest-full-reports/controller.json',lambda x:x['reports'][0].update(nodeid='tests/unknown.py::test_unknown'));self.reject()
    def test_changed_skip_reason(self):
        self.alter('fixed','pytest-full-reports/controller.json',lambda x:next(r for r in x['reports'] if r['outcome']=='skipped').update(reason=['tests/original.py',2,'different reason']));self.reject()
    def test_junit_unknown_case(self):
        file=self.root/'fixed'/'pytest-full.xml';tree=ET.parse(file);ET.SubElement(tree.getroot(),'testcase',classname='unknown',name='test_hidden');tree.write(file);self.reject()
    def test_junit_error(self):
        file=self.root/'fixed'/'pytest-full.xml';tree=ET.parse(file);ET.SubElement(tree.getroot()[0],'error').text='nativeerror';tree.write(file);self.reject()
    def test_junit_original_missing(self):
        file=self.root/'fixed'/'pytest-full.xml';tree=ET.parse(file);tree.getroot().remove(tree.getroot()[0]);tree.write(file);self.reject()
    def test_baseline_behavior_failure_missing(self):self.outcome('baseline','pytest-focus',MANIFEST['baseline_focus_fail'][0],'passed');self.reject()
    def test_baseline_positive_failed(self):self.outcome('baseline','pytest-focus',MANIFEST['baseline_focus_pass'][0],'failed');self.reject()
    def test_new_test_skipped(self):self.outcome('fixed','pytest-focus',MANIFEST['new_ids'][0],'skipped',['source.py',1,'Skipped: invented']);self.reject()
    def test_new_xfail_not_pass(self):self.alter('fixed','pytest-focus-reports/controller.json',lambda x:next(r for r in x['reports'] if r['when']=='call').update(wasxfail='expected_failure'));self.reject()
    def test_pristine_canonical_failed(self):self.outcome('baseline','pytest-full',ORIGINAL,'failed');self.reject()
    def test_both_pristine_and_fixed_original_failure(self):
        for phase in ['baseline','fixed']:self.outcome(phase,'pytest-full',ORIGINAL,'failed')
        self.reject()
    def test_windows_sandbox_alias(self):
        self.seed('Windows314');self.alter('fixed','runtime.json',lambda x:x.update(resolved_home='C:\\different'));self.reject()
    def test_windows_sandbox_username(self):
        self.seed('Windows314');self.alter('fixed','runtime.json',lambda x:x.update(username='another'));self.reject()
    def test_command_cwd(self):self.alter('fixed','commands.json',lambda x:x[0].update(cwd='/wrong'));self.reject()
    def test_harness_missing(self):self.alter('fixed','harness-before.json',lambda x:x['files'].pop('qualification/native.py'));self.reject()
    def test_harness_layout(self):
        for label in ['harness-before','harness-final']:self.alter('fixed',label+'.json',lambda x:x['files'].update({'qualification/unknown.py':{}}))
        self.reject()
    def test_harness_raw_git_blob(self):
        for label in ['harness-before','harness-final']:self.alter('fixed',label+'.json',lambda x:x['files']['qualification/native.py'].update(git_blob='d'*40))
        self.reject()
    def test_raw_report_binding(self):self.alter('fixed','commands.json',lambda x:next(c for c in x if c['label']=='pytest-full')['report_sha256'].update({'pytest-full.xml':'0'*64}));self.reject(reseal=False)
    def test_collection_failure(self):self.alter('fixed','pytest-full-reports/controller.json',lambda x:x['collection_reports'].append({'outcome':'failed','nodeid':'module','reason':'error'}));self.reject()


if __name__=='__main__':unittest.main()
