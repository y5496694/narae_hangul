"""Independent CLI safety: file locks and optional observation of running Hangul.

No Office app, Node.js, or COM registration changes. File jobs work without Hancom.
Only source-overwriting jobs close/reopen an already saved, exactly matched tab.
"""
from contextlib import contextmanager, ExitStack
import hashlib,json,os,tempfile
from pathlib import Path

def data_home():
    return Path(os.environ.get('NARAE_HANGUL_HOME') or Path(os.environ.get('LOCALAPPDATA',tempfile.gettempdir()))/'NaraeHangul/cli')

@contextmanager
def file_lock(file):
    file=Path(file).resolve();directory=data_home()/'locks';directory.mkdir(parents=True,exist_ok=True)
    target=directory/hashlib.sha256(os.path.normcase(str(file)).encode('utf-8')).hexdigest()
    try:target.mkdir()
    except FileExistsError:raise ValueError('다른 나래한글 작업이 진행 중입니다. owner.json의 PID 종료를 확인한 뒤 잠금을 정리하세요: '+str(target))
    (target/'owner.json').write_text(json.dumps({'pid':os.getpid(),'file':str(file)}),'utf-8')
    try:yield
    finally:
        (target/'owner.json').unlink(missing_ok=True);target.rmdir()

def open_match(source):
    import pythoncom
    from win32com.client import dynamic
    rot,context=pythoncom.GetRunningObjectTable(),pythoncom.CreateBindCtx(0);matches=[]
    for moniker in rot:
        if not moniker.GetDisplayName(context,None).startswith('!HwpObject.'):continue
        app=dynamic.Dispatch(rot.GetObject(moniker).QueryInterface(pythoncom.IID_IDispatch))
        for i in range(app.XHwpDocuments.Count):
            doc=app.XHwpDocuments.Item(i);name=str(doc.FullName or '')
            if name and os.path.normcase(str(Path(name).resolve()))==os.path.normcase(str(source)):
                matches.append((app,doc))
    if len(matches)>1:raise ValueError('같은 파일이 여러 한글 문서에서 열려 있습니다. 중복 문서를 닫으세요.')
    return matches[0] if matches else None

@contextmanager
def native_guard(source,overwrite=False):
    state={};app=None;closed=False
    if os.name!='nt':yield state;return
    import pythoncom
    pythoncom.CoInitialize()
    try:
        match=open_match(source)
        if match:
            app,doc=match
            if bool(doc.Modified):raise ValueError('한글에 저장하지 않은 변경이 있습니다. 저장하거나 live-read/live-edit를 사용하세요.')
            if overwrite:
                doc.Close(False);closed=True
        try:yield state
        finally:
            if closed:
                # Never Open into another user document: reuse an empty tab or
                # allocate a fresh tab in this exact application before opening.
                docs=app.XHwpDocuments
                empty=next((docs.Item(i) for i in range(docs.Count) if not str(docs.Item(i).FullName or '') and not bool(docs.Item(i).Modified)),None)
                if empty is None:empty=docs.Add(True)
                empty.SetActive_XHwpDocument()
                if not bool(app.IsEmpty):
                    empty=docs.Add(True);empty.SetActive_XHwpDocument()
                if not app.Open(str(source),'HWPX' if source.suffix.lower()=='.hwpx' else 'HWP',''):
                    raise ValueError('파일 처리 뒤 원래 한글 창에서 재열기를 확인하지 못했습니다.')
                if os.path.normcase(str(Path(str(app.Path)).resolve()))!=os.path.normcase(str(source)):
                    raise ValueError('재열린 한글 문서가 요청한 경로와 다릅니다.')
                state['native_document_reopened']=True
    finally:pythoncom.CoUninitialize()

@contextmanager
def guarded_job(job,base):
    if not isinstance(job,dict):yield {};return
    action=job.get('action');mutating=action in {'create','edit','hancom','live-edit'}
    def resolve(value):
        if not isinstance(value,str) or not value:return None
        return (Path(base)/value).resolve()
    source=resolve(job.get('input'));target=resolve(job.get('output'))
    with ExitStack() as stack:
        if mutating:
            for file in sorted({p for p in (source,target) if p is not None},key=str):stack.enter_context(file_lock(file))
        state={}
        if source and action in {'edit','hancom'}:
            state=stack.enter_context(native_guard(source,source==target and job.get('overwrite_source') is True))
        yield state

def live_job(job,base):
    from .automation import _keys
    _keys(job,('schema','action','input','liveRevision','operations','save'),'live job')
    if job.get('schema',1)!=1:raise ValueError('Unsupported automation schema')
    if not isinstance(job.get('input'),str):raise ValueError('input 문서 경로가 필요합니다.')
    if 'save' in job and not isinstance(job['save'],bool):raise ValueError('save는 true/false로 지정하세요.')
    if job['action']=='live-edit' and not job.get('liveRevision'):raise ValueError('직전 live-read의 liveRevision이 필요합니다.')
    if os.name!='nt':raise ValueError('열린 한글 작업은 Windows 한컴오피스 한글이 필요합니다.')
    from .live_hangul import run
    import pythoncom
    source=(Path(base)/job['input']).resolve();pythoncom.CoInitialize()
    try:
        data=run({**job,'path':str(source),'action':'read' if job['action']=='live-read' else 'edit','backupDir':str(data_home()/'backups')})
        return {'schema':1,'success':True,'action':job['action'],'uses_hancom_runtime':True,**data}
    finally:pythoncom.CoUninitialize()
