"""Run a small unchanged-base public Client/server baseline; no production edits."""
import datetime,hashlib,json,os,subprocess,sys,tomllib
from pathlib import Path
import xml.etree.ElementTree as ET
harness=Path(__file__).resolve().parent
source=Path(sys.argv[1]).resolve();proof=Path(sys.argv[2]).resolve();proof.mkdir(parents=True,exist_ok=True)
manifest=json.loads((harness/'manifest.json').read_text());commands=[]
def dump(name,value):
    (proof/name).write_text(json.dumps(value,indent=2))
def git(*args):
    return subprocess.check_output(['git',*args],cwd=source).decode().strip()
def harness_snapshot():
    root=harness.parent.parent
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip()
    if head!=os.environ.get('GITHUB_SHA'):raise RuntimeError('Harness HEAD differs from run')
    if subprocess.check_output(['git','rev-parse','HEAD^'],cwd=root).decode().strip()!=manifest['harnessParent']:raise RuntimeError('Harness parent differs')
    names=['.qualification/fastmcp-version-output-baseline/'+f.name for f in harness.iterdir() if f.is_file()] + ['.github/workflows/version-output-baseline.yml']
    records={}
    for name in names:
        row=subprocess.check_output(['git','ls-tree','HEAD','--',name],cwd=root).decode().strip()
        meta,actualname=row.split('\t');mode,kind,oid=meta.split()
        if mode!='100644' or kind!='blob' or actualname!=name:raise RuntimeError('Unexpected harness path/mode')
        raw=(root/name).read_bytes();blob=subprocess.check_output(['git','cat-file','blob',oid],cwd=root)
        if raw!=blob:raise RuntimeError('Harness helper differs from Git')
        records[name]={'gitBlob':oid,'SHA256':hashlib.sha256(raw).hexdigest()}
    return {'head':head,'tree':subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=root).decode().strip(),'parent':subprocess.check_output(['git','rev-parse','HEAD^'],cwd=root).decode().strip(),'files':records}

def snapshot():
    out={}
    for row in subprocess.check_output(['git','ls-tree','-r','-z','HEAD'],cwd=source).split(b'\0'):
        if not row:continue
        meta,name=row.split(b'\t',1);mode,kind,oid=meta.decode().split();name=name.decode();file=source/name
        if kind!='blob':raise RuntimeError('Nonblob source tree')
        original=subprocess.check_output(['git','cat-file','blob',oid],cwd=source)
        actual=os.fsencode(os.readlink(file)) if file.is_symlink()else file.read_bytes()
        if actual!=original:raise RuntimeError('Source Git bytes differ: '+name)
        out[name]={'mode':mode,'oid':oid,'SHA256':hashlib.sha256(actual).hexdigest()}
    return out
def run(label,argv,expected,extra=None):
    env=dict(os.environ);env.update(extra or {});start=datetime.datetime.now(datetime.timezone.utc).isoformat()
    with(proof/(label+'.stdout')).open('wb')as out,(proof/(label+'.stderr')).open('wb')as err:
        try:code=subprocess.run(argv,cwd=source,env=env,stdout=out,stderr=err,timeout=1200).returncode
        except subprocess.TimeoutExpired:code=None
    record={'label':label,'argv':argv,'cwd':str(source),'extraEnv':extra or {},'expectedExit':expected,'actualExit':code,'start':start,'end':datetime.datetime.now(datetime.timezone.utc).isoformat(),'logSHA256':{ch:hashlib.sha256((proof/(label+'.'+ch)).read_bytes()).hexdigest()for ch in ['stdout','stderr']}}
    commands.append(record);dump('commands.json',commands)
    if code!=expected:raise RuntimeError(label+' unexpectedexit '+str(code))
try:
    if os.environ.get('GITHUB_REPOSITORY')!=manifest['harnessRepository']:raise RuntimeError('Wrong qualification repository')
    fork=json.loads(subprocess.check_output(['gh','api','repos/'+manifest['harnessRepository']]))
    dump('github-fork.json',fork)
    if fork['private'] or not fork['fork'] or fork.get('parent',{}).get('full_name')!='HKUDS/nanobot':raise RuntimeError('Qualification repository is not the approved public fork')
    if git('rev-parse','HEAD')!=manifest['base']or git('rev-parse','HEAD^{tree}')!=manifest['baseTree']or git('status','--porcelain'):raise RuntimeError('Wrong/dirty immutable source')
    fixture=harness/'test_version_output.py'
    if hashlib.sha256(fixture.read_bytes()).hexdigest()!=manifest['testSHA256']:raise RuntimeError('Fixture changed')
    before=snapshot();dump('source-before.json',before)
    harness_before=harness_snapshot();dump('harness-before.json',harness_before)
    run('uv-sync',['uv','sync'],0)
    run('uv-version',['uv','--version'],0)
    runtime='import json,sys,platform,importlib.metadata as m,fastmcp,mcp; print(json.dumps({"importPaths":{"fastmcp":fastmcp.__file__,"mcp":mcp.__file__},"python":sys.version,"platform":platform.platform(),"versions":{n:m.version(n) for n in ["mcp","mcp-types","fastmcp-slim","pytest"]}}))'
    run('runtime',['uv','run','--no-sync','python','-c',runtime],0)
    actual=json.loads((proof/'runtime.stdout').read_text())
    if not Path(actual['importPaths']['fastmcp']).resolve().is_relative_to(source/'fastmcp_slim/fastmcp'):raise RuntimeError('FastMCP imported outside exact source')
    locked={x['name']:x['version']for x in tomllib.loads((source/'uv.lock').read_text())['package']if x['name']in['mcp','mcp-types']}
    if {n:actual['versions'][n]for n in locked}!=locked:raise RuntimeError('Installed SDK does not match lock')
    env={'PYTHONPATH':str(harness),'PYTEST_PLUGINS':'collector','FAST_MCP_BASELINE_REPORT':str(proof/'collection.json')}
    common=['uv','run','--no-sync','python','-m','pytest','-c',str(source/'pyproject.toml'),'-o','addopts=','-o','junit_family=xunit1','--confcutdir',str(harness),str(fixture)]
    run('collection',common+['--collect-only','-q'],0,env)
    env['FAST_MCP_BASELINE_REPORT']=str(proof/'outcomes.json')
    run('regression',common+['-vv','--junitxml='+str(proof/'regression.xml')],1,env)
    from evaluate import evaluate
    outcome=evaluate(proof,source,fixture,manifest)
    after=snapshot();dump('source-after.json',after)
    if after!=before or git('status','--porcelain'):raise RuntimeError('Source mutated')
    harness_after=harness_snapshot();dump('harness-final.json',harness_after)
    if harness_after!=harness_before:raise RuntimeError('Harness changed')
    dump('result.json',{'status':'BASELINE_REPRODUCED','source':manifest['base'],'hypothesisFailures':6,'positiveControls':6,'lockedSDK':locked,'fullSourceUnchanged':True,'scope':'Targeted actual Client/server baseline only, not fixed/fullrepository qualification'})
except Exception as error:
    dump('result.json',{'status':'FAIL','error':repr(error)});raise
