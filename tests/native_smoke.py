"""Opt-in real Hancom check, using only disposable documents and owned Hwp PIDs."""
import json,os,subprocess,sys,tempfile,time
from pathlib import Path
import pythoncom,win32process,win32api
from win32com.client import dynamic
ROOT=Path(__file__).resolve().parents[1];CLI=ROOT/'1_나래엔진/narae-hangul/scripts/narae.py'
HOME=Path(tempfile.mkdtemp(prefix='narae-07-native-',dir='D:/Codex/tmp'))
ENV={**os.environ,'NARAE_HANGUL_HOME':str(HOME),'PYTHONUTF8':'1','PYTHONNOUSERSITE':'1','QT_QPA_PLATFORM':'offscreen','TEMP':str(HOME),'TMP':str(HOME)}
for key in ['NARAE_HANGUL_EXE','NARAE_OFFICE_ROOT','NARAE_OFFICE_PYTHON','PYTHONPATH','PYTHONHOME']:ENV.pop(key,None)
counter=0
def job(req,success=True):
    global counter
    counter+=1;file=HOME/f'job-{counter}.json';file.write_text(json.dumps(req,ensure_ascii=False),'utf-8')
    p=subprocess.run([sys.executable,'-I','-X','utf8',str(CLI),'run',str(file),'--python',sys.executable],capture_output=True,encoding='utf-8',env=ENV,timeout=60)
    r=json.loads(p.stdout);assert bool(r['success'])==success,(r,p.stderr);assert p.returncode==(0 if success else 1)
    return r
def owned_app(exe,file):
    child=subprocess.Popen([str(exe),'-Automation'],creationflags=0x08000000,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        rot,context=pythoncom.GetRunningObjectTable(),pythoncom.CreateBindCtx(0);deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            for moniker in rot:
                if not moniker.GetDisplayName(context,None).startswith('!HwpObject.'):continue
                app=dynamic.Dispatch(rot.GetObject(moniker).QueryInterface(pythoncom.IID_IDispatch))
                if app.XHwpWindows.Count and win32process.GetWindowThreadProcessId(app.XHwpWindows.Item(0).WindowHandle)[1]==child.pid:
                    app.XHwpWindows.Item(0).Visible=False
                    for module in ['NaraeHangulFilePathChecker','FilePathCheckerModule']:
                        if app.RegisterModule('FilePathCheckDLL',module):break
                    assert app.Open(str(file),'HWPX' if file.suffix=='.hwpx' else 'HWP','')
                    return app,child
            pythoncom.PumpWaitingMessages();time.sleep(.1)
        raise RuntimeError('Owned Hancom process did not become available')
    except BaseException:
        if child.poll() is None:child.terminate();child.wait(timeout=5)
        raise
def close_owned(app,child):
    try:app.Clear(1);app.Quit()
    except Exception:pass
    try:child.wait(timeout=2)
    except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)

if __name__=='__main__':
    paths=sorted(Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)')).glob('Hnc/Office */HOffice*/Bin/Hwp.exe'),reverse=True)
    exe=Path(os.environ['NARAE_HWP_EXE']) if os.environ.get('NARAE_HWP_EXE') else paths[0]
    pythoncom.CoInitialize();results=[]
    try:
      for ext in ['hwpx','hwp']:
        file=HOME/f'합성.{ext}'
        job({'action':'create','format':ext,'output':str(file),'blocks':[{'type':'paragraph','text':'원래 한 문단입니다.'}]})
        app,child=owned_app(exe,file)
        try:
            read=job({'action':'live-read','input':str(file)})
            job({'action':'live-edit','input':str(file),'liveRevision':'stale','operations':[{'op':'replace_text','text':'원래 한 문단입니다.','replacement':'실패해야 합니다.'}]},False)
            edited=job({'action':'live-edit','input':str(file),'liveRevision':read['liveRevision'],'operations':[{'op':'replace_text','text':'원래 한 문단입니다.','replacement':'열린 한글 수정 완료입니다.'},{'op':'set_style','text':'열린 한글 수정 완료입니다.','font_size':17,'bold':True}]})
            assert edited['saved'] and Path(edited['backup']).is_file()
            saved=job({'action':'inspect','input':str(file)});target=next(p['id'] for p in saved['paragraphs'] if p['text']=='열린 한글 수정 완료입니다.')
            result=job({'action':'edit','input':str(file),'output':str(file),'overwrite_source':True,'expected_input_sha256':saved['input_sha256'],'operations':[{'op':'set_text','target':target,'text':'파일 편집 뒤 같은 창으로 돌아왔습니다.'}]})
            assert result['native_document_reopened'];assert str(Path(str(app.Path)).resolve())==str(file)
            assert win32process.GetWindowThreadProcessId(app.XHwpWindows.Item(0).WindowHandle)[1]==child.pid
            assert app.XHwpDocuments.Count==1 and app.XHwpWindows.Count==1
            reread=job({'action':'live-read','input':str(file)});assert '같은 창으로' in reread['text']
            results.append({'format':ext,'liveEdit':True,'staleRevisionRefused':True,'backup':True,'fileEditSamePid':True,'noSpareBlank':True})
        finally:close_owned(app,child)
      result={'passed':True,'home':str(HOME),'python':sys.executable,'hancom':str(exe),'results':results};(HOME/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(result,ensure_ascii=False))
    finally:pythoncom.CoUninitialize()
