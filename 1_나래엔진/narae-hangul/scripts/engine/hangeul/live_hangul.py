"""Edit the requested running Hancom document without opening a spare app."""
import hashlib,json,shutil,sys,uuid
from pathlib import Path
import pythoncom
from .live_actions import application_for,selected_text,apply,style

def read(app,file):
    mode=app.GetMessageBoxMode()
    try:
        app.SetMessageBoxMode(0x00010000)
        text=app.GetTextFile('TEXT','')
    finally:app.SetMessageBoxMode(mode)
    return {'path':str(file),'text':text[:300000],'truncated':len(text)>300000,
            'liveRevision':hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'pages':int(app.PageCount),'modified':bool(app.XHwpDocuments.Active_XHwpDocument.Modified),
            'note':'현재 열린 한글의 텍스트입니다. 표와 서식은 실제 한글에서 보존합니다. 반복되는 문구는 expected_count와 occurrence로 지정하세요.'}

def select_text(app,text,expected,occurrence):
    if not isinstance(text,str) or not text or len(text)>10000 or '\n' in text or '\r' in text:
        raise ValueError('한 문단 안의 정확한 원문을 1~10000자로 지정하세요.')
    if type(expected) is not int or expected<1 or type(occurrence) is not int or not 0<=occurrence<expected:
        raise ValueError('expected_count와 0부터 시작하는 occurrence를 확인하세요.')
    app.HAction.Run('Cancel');app.MovePos(2,0,0)
    action=app.CreateAction('RepeatFind');params=action.CreateSet();action.GetDefault(params)
    for name,value in {'FindString':text,'Direction':0,'IgnoreMessage':1,'FindType':1}.items():params.SetItem(name,value)
    found=[];seen=set();mode=app.GetMessageBoxMode()
    try:
        app.SetMessageBoxMode(0x00020000)
        while len(found)<=expected and action.Execute(params):
            pos=tuple(app.GetSelectedPos())
            if len(pos)!=7 or not pos[0] or pos[1]!=pos[4]:raise ValueError('한 문단 안의 텍스트만 선택할 수 있습니다.')
            if pos in seen:break
            seen.add(pos)
            if selected_text(app)!=text:raise ValueError('찾은 한글 원문이 요청과 다릅니다.')
            found.append(pos)
    finally:app.SetMessageBoxMode(mode)
    if len(found)!=expected:raise ValueError(f'원문 후보 수가 다릅니다. 예상 {expected}, 실제 {len(found)}. 다시 읽으세요.')
    pos=found[occurrence];app.SetPos(*pos[1:4]);app.SelectText(pos[2],pos[3],pos[5],pos[6])
    if selected_text(app)!=text:raise ValueError('편집 직전 원문 확인 실패')
    return pos

def run(req):
    file=Path(req['path']).resolve()
    if file.suffix.lower() not in {'.hwp','.hwpx'}:raise ValueError('HWP/HWPX 문서만 지원합니다.')
    app,doc=application_for(file)
    current=read(app,file)
    if req['action']=='read':return current
    if req.get('liveRevision')!=current['liveRevision']:raise ValueError('열린 문서가 바뀌었습니다. 다시 읽은 뒤 편집하세요.')
    position=tuple(app.GetPos());selection=tuple(app.GetSelectedPos())
    backup=Path(req['backupDir'])/(uuid.uuid4().hex+'-'+file.name);backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,backup)
    complete=0
    try:
        operations=req.get('operations')
        if not isinstance(operations,list) or not 1<=len(operations)<=40:raise ValueError('편집 작업을 1~40개 지정하세요.')
        for op in operations:
            text=op.get('text','');pos=select_text(app,text,op.get('expected_count',1),op.get('occurrence',0))
            if op['op']=='replace_text':
                new=op.get('replacement')
                if not isinstance(new,str) or len(new)>200000:raise ValueError('바꿀 내용을 확인하세요.')
                apply(app,'InsertText',{'Text':new})
            elif op['op']=='set_style':
                # Reuse the native style validator with an exact captured range.
                target={'text':text,'occurrences':op.get('expected_count',1),'occurrence':op.get('occurrence',0),'position':pos[1:]}
                allowed={'font_size','font_family','bold','italic','underline','text_color','align'}
                step={key:value for key,value in op.items() if key in allowed}
                style(app,step,target)
            else:raise ValueError('지원하지 않는 네이티브 편집 작업입니다.')
            complete+=1
        if req.get('save',True) and not doc.Save(True):raise ValueError('저장 결과를 확인하지 못했습니다.')
        return {**read(app,file),'saved':req.get('save',True),'completed':complete,'backup':str(backup)}
    except Exception as error:
        raise ValueError(f'{error}\n완료한 작업: {complete}/{len(req.get("operations",[]))}. 열린 문서에 일부 변경이 남을 수 있습니다. 백업: {backup}') from error
    finally:
        app.HAction.Run('Cancel')
        try:
            app.SetPos(*position)
            if len(selection)==7 and selection[0] and selection[1]==selection[4]:app.SelectText(selection[2],selection[3],selection[5],selection[6])
        except pythoncom.com_error:pass

if __name__=='__main__':
    pythoncom.CoInitialize()
    try:print(json.dumps({'ok':True,'data':run(json.load(sys.stdin))},ensure_ascii=False))
    except Exception as error:print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False));sys.exit(1)
    finally:pythoncom.CoUninitialize()
