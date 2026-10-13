"""Execute unchanged canonical checks against an immutable native source checkout."""
import argparse, datetime, hashlib, json, os, platform, shutil, subprocess, sys, tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
for name in ['phase', 'lane', 'source', 'proof']: parser.add_argument(name)
a = parser.parse_args()
harness = Path(__file__).resolve().parent
manifest = json.loads((harness/'manifest.json').read_text())
source, proof = Path(a.source).resolve(), Path(a.proof).resolve()
proof.mkdir(parents=True, exist_ok=True)
commands = []
env = dict(os.environ)
expected = manifest['base'] if a.phase == 'baseline' else manifest['fixed']
expected_tree = manifest['base_tree'] if a.phase == 'baseline' else manifest['fixed_tree']
lane = manifest['lanes'][a.lane]


def dump(name, value):
    (proof/name).write_text(json.dumps(value, indent=2), encoding='utf-8')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=source).decode().strip()


def snapshot(label):
    entries = []
    for row in subprocess.check_output(['git','ls-tree','-r','-z','HEAD'],cwd=source).split(b'\0'):
        if not row: continue
        metadata, name = row.split(b'\t',1)
        mode, kind, oid = metadata.decode().split()
        if kind != 'blob': raise RuntimeError('Unsupported non-file tree entry')
        entries.append((mode, oid, name.decode()))
    payload = subprocess.check_output(['git','cat-file','--batch'],cwd=source,
                                     input=''.join(oid+'\n' for _,oid,_ in entries).encode())
    cursor, checked = 0, {}
    for mode, oid, name in entries:
        end = payload.index(b'\n',cursor)
        actual_oid, kind, size = payload[cursor:end].decode().split()
        cursor = end+1; size = int(size)
        original = payload[cursor:cursor+size]; cursor += size
        if actual_oid != oid or kind != 'blob' or payload[cursor:cursor+1] != b'\n':
            raise RuntimeError('Git blob receipt mismatch')
        cursor += 1
        file = source/name
        raw = os.fsencode(os.readlink(file)) if file.is_symlink() else file.read_bytes()
        checked[name] = {'mode':mode,'git_blob':oid,'expected_sha256':hashlib.sha256(original).hexdigest(),
                         'actual_sha256':hashlib.sha256(raw).hexdigest(),'storage':'symlink' if file.is_symlink() else 'file'}
        if raw != original or (mode != '120000' and file.is_symlink()):
            dump(label+'.json',checked); raise RuntimeError('Tracked bytes changed: '+name)
    dump(label+'.json',checked)
    return checked


def python_inventory(label):
    expected_names={name for name in subprocess.check_output(['git','ls-files','-z'],cwd=source).decode().split('\0') if name.endswith('.py')}
    actual=set()
    for root, directories, files in os.walk(source):
        directories[:]=[name for name in directories if name not in ['.git','.venv','node_modules','__pycache__']]
        actual.update((Path(root)/name).relative_to(source).as_posix() for name in files if name.endswith('.py'))
    dump(label+'.json',{'expected':sorted(expected_names),'actual':sorted(actual)})
    if actual!=expected_names: raise RuntimeError('Missing/untracked Python source')


def sandbox_home(system, current_env):
    parent = Path(tempfile.mkdtemp(prefix='nanobot-qualification-home-')).resolve()
    if system == 'Windows':
        username = current_env.get('USERNAME')
        if not username or username in ['.','..'] or any(ord(c)<32 or c in '<>:"/\\|?*' for c in username) or username.endswith((' ','.')):
            raise RuntimeError('Unsafe/missing actual Windows USERNAME')
        home = parent/username
        home.mkdir()
        return home.resolve()
    return parent


def run(label, argv, expected_exit=0, extra=None):
    start = datetime.datetime.now(datetime.timezone.utc).isoformat()
    current = dict(env); current.update(extra or {})
    with (proof/(label+'.stdout')).open('wb') as out, (proof/(label+'.stderr')).open('wb') as err:
        try:
            code = subprocess.run(argv,cwd=source,env=current,stdout=out,stderr=err,timeout=2400).returncode
        except subprocess.TimeoutExpired: code = None
    record = {'label':label,'argv':argv,'cwd':str(source),'expected_exit':expected_exit,'exit':code,'extra_env':extra or {},
              'start':start,'end':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    for channel in ['stdout','stderr']:
        record[channel+'_sha256'] = hashlib.sha256((proof/(label+'.'+channel)).read_bytes()).hexdigest()
    record['report_sha256'] = report_hashes(label)
    commands.append(record); dump('commands.json',commands)
    if code != expected_exit: raise RuntimeError(f'{label}: exit {code}, expected {expected_exit}')
    return (proof/(label+'.stdout')).read_text(encoding='utf-8')


def report_hashes(label):
    files = list((proof/(label+'-reports')).glob('*.json'))
    xml = proof/(label+'.xml')
    if xml.exists(): files.append(xml)
    return {f.relative_to(proof).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(files)}


def harness_snapshot(label):
    root = harness.parent
    helpers = ['native.py','compare.py','collector.py','test_compare.py','manifest.json',
               'xlsx-locators-fixture.py','docker-contract.json','docker-compose-verify.sh','docker-permissions.sh']
    names = ['qualification/'+name for name in helpers]+['.github/workflows/nanobot-xlsx-native.yml']
    if sorted(f.name for f in harness.iterdir() if f.name != '__pycache__') != sorted(helpers):
        raise RuntimeError('Unexpected deployed helper layout')
    checked = {}
    for name in names:
        row = subprocess.check_output(['git','ls-tree','HEAD','--',name],cwd=root).decode().strip()
        metadata, actual_name = row.split('\t')
        mode, kind, oid = metadata.split()
        if actual_name != name or kind != 'blob' or mode != '100644':
            raise RuntimeError('Unexpected harness tracked mode/path')
        blob = subprocess.check_output(['git','cat-file','blob',oid],cwd=root)
        raw = (root/name).read_bytes()
        if raw != blob or (root/name).is_symlink(): raise RuntimeError('Harness working bytes altered')
        checked[name] = {'mode':mode,'git_blob':oid,'sha256':hashlib.sha256(raw).hexdigest()}
    untracked = subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=root).decode().splitlines()
    ignored_python = subprocess.check_output(['git','ls-files','--others','--ignored','--exclude-standard','--','*.py'],cwd=root).decode().splitlines()
    if untracked or ignored_python: raise RuntimeError('Untracked harness files')
    receipt = {'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip(),
               'tree':subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=root).decode().strip(),
               'parent':subprocess.check_output(['git','rev-parse','HEAD^'],cwd=root).decode().strip(),
               'files':checked,'untracked':untracked,'ignored_python':ignored_python}
    if receipt['head'] != env.get('GITHUB_SHA') or receipt['parent'] != manifest['deployment']['harness_parent']:
        raise RuntimeError('Harness immutable ancestry mismatch')
    dump(label+'.json',receipt)
    return receipt


def pytest_run(label, arguments, expected_exit=0):
    report_dir = proof/(label+'-reports')
    extra = {'PYTEST_PLUGINS':'collector','NANOBOT_QUALIFICATION_REPORT_DIR':str(report_dir),
             'PYTHONPATH':str(harness)}
    selection=[];skip=False
    for value in arguments:
        if skip: skip=False;continue
        if value=='-o': skip=True;continue
        if value=='-vv' or value.startswith(('--cov','--durations')):continue
        selection.append(value)
    collection_extra=dict(extra)
    collection_extra['NANOBOT_QUALIFICATION_REPORT_DIR']=str(proof/(label+'-collection-reports'))
    run(label+'-collection',['uv','run','--no-sync','python','-m','pytest',*selection,'--collect-only'],0,collection_extra)
    run(label,['uv','run','--no-sync','python','-m','pytest',*arguments,
               '--junitxml='+str(proof/(label+'.xml'))],expected_exit,extra)


try:
    if a.phase not in ['baseline','fixed']: raise RuntimeError('Unexpected phase')
    system = platform.system()
    if {'Linux':'Linux','Windows':'Windows','macOS':'Darwin'}.get(env.get('RUNNER_OS')) != system:
        raise RuntimeError('Actual native OS mismatch')
    if env['RUNNER_OS'] != lane['os']: raise RuntimeError('Lane OS mismatch')
    if git('rev-parse','HEAD') != expected or git('rev-parse','HEAD^{tree}') != expected_tree:
        raise RuntimeError('Source identity mismatch')
    if git('status','--porcelain','--untracked-files=all'): raise RuntimeError('Initial source not pristine')
    if a.phase == 'fixed':
        if git('rev-parse','HEAD^') != manifest['fixed_parent']: raise RuntimeError('Candidate parent mismatch')
        subprocess.run(['git','merge-base','--is-ancestor',manifest['base'],'HEAD'],cwd=source,check=True)
        for name, digest in manifest['source_files_sha256'].items():
            if hashlib.sha256((source/name).read_bytes()).hexdigest() != digest:
                raise RuntimeError('Candidate source hash mismatch: '+name)
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=harness).decode().strip() != env.get('GITHUB_SHA'):
        raise RuntimeError('Immutable harness head mismatch')
    fixture = harness/'xlsx-locators-fixture.py'
    fixture_raw = fixture.read_bytes()
    fixture_blob = subprocess.check_output(['git','show','HEAD:qualification/xlsx-locators-fixture.py'],cwd=harness)
    fixture_receipt = {'actual_sha256':hashlib.sha256(fixture_raw).hexdigest(),
                       'git_blob_sha256':hashlib.sha256(fixture_blob).hexdigest(),
                       'expected_sha256':manifest['fixture_sha256'],'actual_crlf_count':fixture_raw.count(b'\r\n')}
    dump('fixture-bytes.json',fixture_receipt)
    if fixture_raw != fixture_blob or fixture_receipt['actual_sha256'] != manifest['fixture_sha256']:
        raise RuntimeError('Fixture bytes mismatch')
    harness_before = harness_snapshot('harness-before')
    before = snapshot('tracked-before')
    python_inventory('python-inventory-before')
    dump('harness-hashes.json',{Path(name).name:record['sha256'] for name,record in harness_before['files'].items() if name.startswith('qualification/')})
    original_home_env = {key:env.get(key) for key in ['HOME','USERPROFILE','USERNAME','HOMEDRIVE','HOMEPATH']}
    home = sandbox_home(system, env)
    env.update(HOME=str(home),USERPROFILE=str(home),XDG_CONFIG_HOME=str(home/'.config'),
               XDG_CACHE_HOME=str(home/'.cache'),APPDATA=str(home/'AppData/Roaming'),
               LOCALAPPDATA=str(home/'AppData/Local'),PYTEST_ADDOPTS='-o tmp_path_retention_policy=failed')
    dump('environment.json',{'phase':a.phase,'lane':a.lane,'head':expected,'tree':expected_tree,
                            'system':system,'runner_os':env['RUNNER_OS'],'platform':platform.platform(),
                            'machine':platform.machine(),'harness_head':env.get('GITHUB_SHA'),
                            'source':str(source),'proof':str(proof),'harness':str(harness),
                            'sandbox_home':str(home),'original_home_env':original_home_env,'actual_username':env.get('USERNAME'),'runner_temp':env.get('RUNNER_TEMP'),'host_python':sys.version})
    if a.lane == 'Docker':
        for label, argv in json.loads((harness/'docker-contract.json').read_text()).items():
            run(label,[value.replace('{harness}',str(harness)) for value in argv],extra={'HOME':env['RUNNER_TEMP']})
    else:
        if str(sys.version_info.major)+'.'+str(sys.version_info.minor)!=lane['python']:
            raise RuntimeError('Host Python runtime mismatch')
        if a.lane == 'Linux314':
            run('import-boundaries',['python','-m','scripts.check_import_boundaries'])
        run('uv-version',['uv','--version'])
        if system == 'Linux':
            run('system-update',['sudo','apt-get','update'])
            run('system-install',['sudo','apt-get','install','-y','libolm-dev','build-essential'])
        run('install-dependencies',['uv','sync','--all-extras','--dev'])
        run('install-channel-dependencies',['uv','run','--no-sync','python','-m',
                                            'scripts.install_channel_dependencies','--all-channels'])
        run('dependency-consistency',['uv','pip','check'])
        runtime = json.loads(run('runtime',['uv','run','--no-sync','python','-c',
            'import json,sys,platform,os,ntpath; from pathlib import Path; print(json.dumps({"python":sys.version,"minor":str(sys.version_info.major)+"."+str(sys.version_info.minor),"system":platform.system(),"home":str(Path.home()),"resolved_home":str(Path.home().resolve()),"username":os.environ.get("USERNAME"),"userprofile":os.environ.get("USERPROFILE"),"named_root_expansion":ntpath.expanduser("~root/.bashrc")}))']))
        dump('runtime.json',runtime)
        if runtime['minor'] != lane['python'] or runtime['system'] != system:
            raise RuntimeError('Actual Python runtime mismatch')
        if system == 'Windows' and (runtime['home'] != runtime['resolved_home'] or Path(runtime['home']).name != runtime['username'] or runtime['userprofile'] != runtime['home'] or runtime['named_root_expansion'] == '~root/.bashrc'):
            raise RuntimeError('Windows sandbox changed native profile semantics')
        run('dependency-versions',['uv','pip','list','--format=json'])
        if a.lane == 'Linux314':
            run('ruff',['uv','run','--no-sync','ruff','check','nanobot','tests','conftest.py',
                        'scripts/check_import_boundaries.py'])
            run('basedpyright',['uv','run','--no-sync','basedpyright'])
        if a.lane == 'WindowsProcess':
            pytest_run('pytest-process',['tests/tools/test_exec_platform.py','--durations=25','--durations-min=1.0'])
        else:
            common = ['--durations=25','--durations-min=1.0']
            full = ['-n','auto','--dist','loadfile','--ignore=tests/cli/test_commands.py']
            if a.lane == 'Windows314': full += ['--ignore=tests/tools/test_exec_platform.py']
            if a.lane == 'Linux314':
                full += ['--cov=nanobot','--cov-report=term-missing:skip-covered','-vv','-o','faulthandler_timeout=60']
            pytest_run('pytest-full',full+common)
            cli = ['tests/cli/test_commands.py']
            if a.lane == 'Linux314': cli += ['--cov=nanobot','--cov-append','--cov-report=term-missing:skip-covered']
            pytest_run('pytest-cli',cli+common)
            target = source/manifest['fixture_path']
            if a.phase == 'baseline':
                if target.exists(): raise RuntimeError('Baseline fixture present before transplantation')
                target.write_bytes(fixture_raw)
            elif target.read_bytes() != fixture_raw: raise RuntimeError('Fixed fixture differs')
            dump('fixture-relation.json',{'literal_bytes':True,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                                         'transplanted_after_original_suite':a.phase=='baseline'})
            pytest_run('pytest-focus',[manifest['fixture_path'],'-vv'],1 if a.phase=='baseline' else 0)
            if a.phase == 'baseline': target.unlink()
    python_inventory('python-inventory-after')
    after = snapshot('tracked-after')
    if before != after or git('rev-parse','HEAD') != expected or git('rev-parse','HEAD^{tree}') != expected_tree:
        raise RuntimeError('Tracked source identity changed')
    status = git('status','--porcelain','--untracked-files=all')
    (proof/'source-status.txt').write_text(status,encoding='utf-8')
    if status: raise RuntimeError('Final untracked source mutation')
    result = {'status':'PASS','phase':a.phase,'lane':a.lane,'head':expected}
except Exception as exc:
    result = {'status':'FAIL','phase':a.phase,'lane':a.lane,'error':str(exc)}
finally:
    try:
        harness_snapshot('harness-final')
        snapshot('tracked-final')
        dump('final-identity.json',{'head':git('rev-parse','HEAD'),'tree':git('rev-parse','HEAD^{tree}'),
                                    'status':git('status','--porcelain','--untracked-files=all')})
    except Exception as exc:
        result = {'status':'FAIL','phase':a.phase,'lane':a.lane,'error':result.get('error',''),
                  'final_source_error':str(exc)}
dump('result.json',result)
print(json.dumps(result)); sys.exit(0 if result['status']=='PASS' else 1)
