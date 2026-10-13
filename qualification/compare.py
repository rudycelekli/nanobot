"""Strict comparison of complete native inventories, outcomes and source receipts."""
import datetime, hashlib, json, re, sys
from pathlib import Path, PurePosixPath, PureWindowsPath
import xml.etree.ElementTree as ET
MANIFEST = json.loads(Path(__file__).with_name('manifest.json').read_text())


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def expected_commands(lane, phase, environment):
    path = PureWindowsPath if MANIFEST['lanes'][lane]['os']=='Windows' else PurePosixPath
    proof, harness = path(environment['proof']), path(environment['harness'])
    commands = {}
    if lane=='Docker':
        for label, argv in read(Path(__file__).with_name('docker-contract.json')).items():
            commands[label] = ([v.replace('{harness}',str(harness)) for v in argv],0,{'HOME':environment['runner_temp']})
        return commands
    if lane=='Linux314': commands['import-boundaries']=(['python','-m','scripts.check_import_boundaries'],0,{})
    for label, argv in [
        ('uv-version',['uv','--version']),
        ('install-dependencies',['uv','sync','--all-extras','--dev']),
        ('install-channel-dependencies',['uv','run','--no-sync','python','-m','scripts.install_channel_dependencies','--all-channels']),
        ('dependency-consistency',['uv','pip','check']),
        ('runtime',['uv','run','--no-sync','python','-c','import json,sys,platform,os,ntpath; from pathlib import Path; print(json.dumps({"python":sys.version,"minor":str(sys.version_info.major)+"."+str(sys.version_info.minor),"system":platform.system(),"home":str(Path.home()),"resolved_home":str(Path.home().resolve()),"username":os.environ.get("USERNAME"),"userprofile":os.environ.get("USERPROFILE"),"named_root_expansion":ntpath.expanduser("~root/.bashrc")}))']),
        ('dependency-versions',['uv','pip','list','--format=json'])]: commands[label]=(argv,0,{})
    if MANIFEST['lanes'][lane]['os']=='Linux':
        # The native runner performs apt between uv readiness and Python installation.
        keys=list(commands);position=keys.index('install-dependencies')
        ordered=list(commands.items());ordered[position:position]=[('system-update',(['sudo','apt-get','update'],0,{})),('system-install',(['sudo','apt-get','install','-y','libolm-dev','build-essential'],0,{}))]
        commands=dict(ordered)
    if lane=='Linux314':
        commands.update({
            'ruff':(['uv','run','--no-sync','ruff','check','nanobot','tests','conftest.py','scripts/check_import_boundaries.py'],0,{}),
            'basedpyright':(['uv','run','--no-sync','basedpyright'],0,{})})
    def test(label, args, code=0):
        selection=[];skip=False
        for value in args:
            if skip:skip=False;continue
            if value=='-o':skip=True;continue
            if value=='-vv' or value.startswith(('--cov','--durations')):continue
            selection.append(value)
        commands[label+'-collection']=(['uv','run','--no-sync','python','-m','pytest',*selection,'--collect-only'],0,None)
        commands[label]=(['uv','run','--no-sync','python','-m','pytest',*args,'--junitxml='+str(proof/(label+'.xml'))],code,None)
    common=['--durations=25','--durations-min=1.0']
    if lane=='WindowsProcess':
        test('pytest-process',['tests/tools/test_exec_platform.py',*common]); return commands
    full=['-n','auto','--dist','loadfile','--ignore=tests/cli/test_commands.py']
    if lane=='Windows314': full += ['--ignore=tests/tools/test_exec_platform.py']
    if lane=='Linux314': full += ['--cov=nanobot','--cov-report=term-missing:skip-covered','-vv','-o','faulthandler_timeout=60']
    test('pytest-full',full+common)
    cli=['tests/cli/test_commands.py']
    if lane=='Linux314': cli += ['--cov=nanobot','--cov-append','--cov-report=term-missing:skip-covered']
    test('pytest-cli',cli+common)
    test('pytest-focus',[MANIFEST['fixture_path'],'-vv'],1 if phase=='baseline' else 0)
    return commands


def inspect_pytest(directory, label, expected_exit):
    report_dir=directory/(label+'-reports')
    controller=read(report_dir/'controller.json')
    if controller['worker']!='controller' or controller['exitstatus']!=expected_exit:
        raise RuntimeError('Pytest controller identity/exit mismatch')
    workers=[read(p) for p in sorted(report_dir.glob('gw*.json'),key=lambda p:int(p.stem[2:]))]
    if any(w['exitstatus']!=expected_exit for w in workers): raise RuntimeError('Worker exit mismatch')
    inventories=[w['collected'] for w in workers] or [controller['collected']]
    inventory=inventories[0]
    collection_controller=read(directory/(label+'-collection-reports')/'controller.json')
    if collection_controller['worker']!='controller' or collection_controller['exitstatus']!=0 or collection_controller['reports']:
        raise RuntimeError('Independent collection failed/executed tests')
    if collection_controller['collected']!=inventory:
        raise RuntimeError('Canonical collected inventory differs from independent collection')
    if not inventory or any(i!=inventory for i in inventories): raise RuntimeError('Empty/different worker inventories')
    ids={i['nodeid']:i for i in inventory}
    if len(ids)!=len(inventory): raise RuntimeError('Duplicate collected test identity')
    if any(w['worker']!='gw'+str(index) for index,w in enumerate(workers)):
        raise RuntimeError('Unexpected worker files')
    reports={}
    for report in controller['reports']:
        node=report['nodeid']; when=report['when']
        if node not in ids or when not in ['setup','call','teardown']: raise RuntimeError('Unknown test/phase')
        if when in reports.setdefault(node,{}): raise RuntimeError('Duplicate report phase')
        reports[node][when]=report
    if set(reports)!=set(ids): raise RuntimeError('Collected test lost without outcome')
    outcomes={}
    for node, phases in reports.items():
        if set(phases) not in [{'setup','call','teardown'},{'setup','teardown'}]:
            raise RuntimeError('Incomplete test phases')
        if phases['teardown']['outcome']!='passed': raise RuntimeError('Teardown failure/skip')
        if phases['setup']['outcome']=='skipped':
            if 'call' in phases: raise RuntimeError('Skipped setup also called')
            terminal=phases['setup']
        else:
            if phases['setup']['outcome']!='passed' or 'call' not in phases: raise RuntimeError('Setup error')
            terminal=phases['call']
        if terminal['outcome'] not in ['passed','failed','skipped']: raise RuntimeError('Unknown outcome')
        outcomes[node]={'outcome':terminal['outcome'],'reason':terminal['reason'] if terminal['outcome']=='skipped' else None,
                        'wasxfail':terminal.get('wasxfail')}
    collections=[report['collection_reports'] for report in workers or [controller]]
    collection=collections[0]
    if any(c!=collection for c in collections): raise RuntimeError('Worker collection skips differ')
    if any(r['outcome']!='skipped' for r in collection): raise RuntimeError('Collection failed')
    if collection_controller['collection_reports']!=collection: raise RuntimeError('Independent collection skips changed')
    xml=ET.parse(directory/(label+'.xml')).getroot()
    cases=list(xml.iter('testcase')); by_xml={}
    for case in cases:
        key=(case.get('classname',''),case.get('name',''))
        if key in by_xml: raise RuntimeError('Duplicate JUnit testcase')
        by_xml[key]='failed' if case.find('failure') is not None else 'error' if case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
    matched=set()
    for node,item in ids.items():
        key=(item['junit_classname'],item['junit_name'])
        if key not in by_xml or by_xml[key]!=outcomes[node]['outcome']: raise RuntimeError('JUnit/collector outcome mismatch')
        matched.add(key)
    # Only native module-level collection skips may add JUnit rows, never hidden pass/fail cases.
    extras=set(by_xml)-matched
    expected_extras={(r['junit_classname'],r['junit_name']):r for r in collection}
    if len(expected_extras)!=len(collection) or extras!=set(expected_extras) or any(by_xml[k]!='skipped' for k in extras):
        raise RuntimeError('Unexplained JUnit cases')
    for case in cases:
        key=(case.get('classname',''),case.get('name',''))
        if key in expected_extras and case.find('skipped').text!=expected_extras[key]['reason']:
            raise RuntimeError('Collection skip JUnit reason mismatch')
    if expected_exit==0 and any(x['outcome']=='failed' for x in outcomes.values()): raise RuntimeError('Canonical test failure')
    return outcomes,collection


def inspect(directory, phase, lane):
    result=read(directory/'result.json');environment=read(directory/'environment.json')
    expected=MANIFEST['base'] if phase=='baseline' else MANIFEST['fixed']
    tree=MANIFEST['base_tree'] if phase=='baseline' else MANIFEST['fixed_tree']
    if result!={'status':'PASS','phase':phase,'lane':lane,'head':expected}: raise RuntimeError('Native gate not PASS')
    system={'Linux':'Linux','Windows':'Windows'}[MANIFEST['lanes'][lane]['os']]
    if environment['head']!=expected or environment['tree']!=tree or environment['system']!=system or environment['runner_os']!=MANIFEST['lanes'][lane]['os']:
        raise RuntimeError('Native identity mismatch')
    if not environment['sandbox_home'] or not re.fullmatch('[0-9a-f]{40}',environment['harness_head']): raise RuntimeError('Sandbox/harness identity missing')
    final=read(directory/'final-identity.json')
    if final!={'head':expected,'tree':tree,'status':''} or (directory/'source-status.txt').read_text(): raise RuntimeError('Final identity/cleanliness changed')
    before=read(directory/'tracked-before.json')
    if not before or any(read(directory/(name+'.json'))!=before for name in ['tracked-after','tracked-final']): raise RuntimeError('Tracked bytes changed')
    expected_files=MANIFEST['tracked_files'][phase]
    if set(before)!=set(expected_files): raise RuntimeError('Original tracked file omitted/added')
    for name,check in before.items():
        expected_file=expected_files[name]
        if any(check[k]!=expected_file[k] for k in ['mode','git_blob']) or check['actual_sha256']!=expected_file['sha256']:
            raise RuntimeError('Working bytes differ from immutable tree')
        if check['expected_sha256']!=check['actual_sha256'] or not re.fullmatch('[0-9a-f]{40}',check['git_blob']): raise RuntimeError('Git blob mismatch')
        if check['mode']=='120000' and check['storage']!='symlink': raise RuntimeError('Symlink checkout altered')
        if check['mode']!='120000' and check['storage']!='file': raise RuntimeError('File checkout altered')
    if phase=='fixed' and any(before[f]['actual_sha256']!=digest for f,digest in MANIFEST['source_files_sha256'].items()): raise RuntimeError('Fixed source differs')
    python_names=sorted(name for name in before if name.endswith('.py'))
    for label in ['python-inventory-before','python-inventory-after']:
        if read(directory/(label+'.json'))!={'expected':python_names,'actual':python_names}:
            raise RuntimeError('Python source inventory changed')
    fixture=read(directory/'fixture-bytes.json')
    if any(fixture[k]!=MANIFEST['fixture_sha256'] for k in ['actual_sha256','git_blob_sha256','expected_sha256']) or fixture['actual_crlf_count']!=0:
        raise RuntimeError('Fixture bytes changed')
    helper_names=['native.py','compare.py','collector.py','test_compare.py','manifest.json',
                  'xlsx-locators-fixture.py','docker-contract.json','docker-compose-verify.sh','docker-permissions.sh']
    actual_helpers=read(directory/'harness-hashes.json')
    expected_helpers={n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in helper_names}
    if actual_helpers!=expected_helpers: raise RuntimeError('Reviewed helper hashes changed')
    deployed=read(directory/'harness-before.json')
    if deployed!=read(directory/'harness-final.json'): raise RuntimeError('Harness bytes/identity changed')
    if deployed['head']!=environment['harness_head'] or any(not re.fullmatch('[0-9a-f]{40}',deployed[k]) for k in ['head','tree','parent']):
        raise RuntimeError('Harness Git identity invalid')
    if deployed['parent']!=MANIFEST['deployment']['harness_parent']: raise RuntimeError('Harness parent changed')
    if deployed['untracked'] or deployed['ignored_python']: raise RuntimeError('Harness untracked source')
    expected_layout={'qualification/'+n:Path(__file__).with_name(n) for n in helper_names}
    workflow=Path(__file__).parent.parent/'.github/workflows/nanobot-xlsx-native.yml'
    if not workflow.exists(): workflow=Path(__file__).with_name('nanobot-xlsx-native.yml')
    expected_layout['.github/workflows/nanobot-xlsx-native.yml']=workflow
    if set(deployed['files'])!=set(expected_layout): raise RuntimeError('Harness layout changed')
    for name,file in expected_layout.items():
        raw=file.read_bytes();record=deployed['files'][name]
        oid=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if record!={'mode':'100644','git_blob':oid,'sha256':hashlib.sha256(raw).hexdigest()}:
            raise RuntimeError('Deployed harness blob changed')
    commands=read(directory/'commands.json');contract=expected_commands(lane,phase,environment)
    if [c['label'] for c in commands]!=list(contract): raise RuntimeError('Missing/reordered commands')
    for c in commands:
        argv,code,extra=contract[c['label']]
        if c['cwd']!=environment['source']: raise RuntimeError('Command working directory changed')
        report_files=list((directory/(c['label']+'-reports')).glob('*.json'))
        xml=directory/(c['label']+'.xml')
        if xml.exists():report_files.append(xml)
        hashes={f.relative_to(directory).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(report_files)}
        if c['report_sha256']!=hashes: raise RuntimeError('Raw pytest receipt changed')
        if c['argv']!=argv or c['exit']!=code or c['expected_exit']!=code: raise RuntimeError('Command/exit changed')
        if extra is not None and c['extra_env']!=extra: raise RuntimeError('Command environment changed')
        if extra is None:
            current=c['extra_env']
            if set(current)!= {'PYTEST_PLUGINS','NANOBOT_QUALIFICATION_REPORT_DIR','PYTHONPATH'} or current['PYTEST_PLUGINS']!='collector': raise RuntimeError('Collector environment changed')
            path=PureWindowsPath if system=='Windows' else PurePosixPath
            if current['NANOBOT_QUALIFICATION_REPORT_DIR']!=str(path(environment['proof'])/(c['label']+'-reports')):
                raise RuntimeError('Collector report path changed')
            if current['PYTHONPATH']!=environment['harness']: raise RuntimeError('Collector path changed')
        start,end=datetime.datetime.fromisoformat(c['start']),datetime.datetime.fromisoformat(c['end'])
        if start.tzinfo is None or end.tzinfo is None or start>end: raise RuntimeError('Invalid command times')
        for channel in ['stdout','stderr']:
            if hashlib.sha256((directory/(c['label']+'.'+channel)).read_bytes()).hexdigest()!=c[channel+'_sha256']:
                raise RuntimeError('Raw command log changed')
    if lane=='Docker': return {}
    runtime=read(directory/'runtime.json')
    if runtime['minor']!=MANIFEST['lanes'][lane]['python'] or runtime['system']!=system: raise RuntimeError('Wrong Python runtime')
    if system=='Windows':
        home=PureWindowsPath(runtime['home'])
        if runtime['home']!=runtime['resolved_home'] or home.name!=runtime['username'] or runtime['userprofile']!=runtime['home'] or runtime['username']!=environment['actual_username'] or runtime['home']!=environment['sandbox_home'] or PureWindowsPath(runtime['named_root_expansion'])!=home.parent/'root'/'.bashrc':
            raise RuntimeError('Windows sandbox native profile semantics changed')
    test_labels=['pytest-process'] if lane=='WindowsProcess' else ['pytest-full','pytest-cli','pytest-focus']
    parsed={label:inspect_pytest(directory,label,1 if phase=='baseline' and label=='pytest-focus' else 0) for label in test_labels}
    if lane!='WindowsProcess':
        relation=read(directory/'fixture-relation.json')
        if relation!={'literal_bytes':True,'sha256':MANIFEST['fixture_sha256'],'transplanted_after_original_suite':phase=='baseline'}: raise RuntimeError('Fixture transplantation invalid')
        focus,skip=parsed['pytest-focus']
        expected_focus={n:('failed' if n in MANIFEST['baseline_focus_fail'] and phase=='baseline' else 'passed') for n in MANIFEST['new_ids']}
        if skip or set(focus)!=set(expected_focus) or any(focus[n]['outcome']!=out or focus[n]['wasxfail'] is not None for n,out in expected_focus.items()):
            raise RuntimeError('Wrong regression/control outcomes')
    return parsed


def compare_pair(base,fixed,lane):
    original=inspect(base,'baseline',lane);current=inspect(fixed,'fixed',lane)
    if read(base/'environment.json')['harness_head']!=read(fixed/'environment.json')['harness_head']:
        raise RuntimeError('Different harness revisions paired')
    summaries={}
    for label,(cases,collection) in original.items():
        if label=='pytest-focus': continue
        now,now_collection=current[label]
        additions=set(MANIFEST['new_ids']) if label=='pytest-full' else set()
        if set(now)!=set(cases)|additions: raise RuntimeError('Original test lost/unknown new test')
        if collection!=now_collection or any(now[n]!=out for n,out in cases.items()): raise RuntimeError('Original outcomes/skip reasons changed')
        if any(now[n]!={'outcome':'passed','reason':None,'wasxfail':None} for n in additions): raise RuntimeError('New canonical test not PASS')
        summaries[label]={'original':len(cases),'fixed':len(now),'skips':sum(c['outcome']=='skipped' for c in cases.values()),'new_pass':len(additions)}
    return {'status':'PASS','lane':lane,'tests':summaries}


if __name__=='__main__':
    root,output=Path(sys.argv[1]),Path(sys.argv[2])
    try:
        result={'status':'PASS','lanes':[compare_pair(root/('nanobot-'+lane+'-baseline'),root/('nanobot-'+lane+'-fixed'),lane) for lane in MANIFEST['lanes']]}
    except Exception as exc: result={'status':'FAIL','error':str(exc)}
    output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result));sys.exit(0 if result['status']=='PASS' else 1)
