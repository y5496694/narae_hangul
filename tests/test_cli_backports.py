"""Synthetic round trips for independent CLI safety and Office backports."""
import hashlib,json,os,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'1_나래엔진/narae-hangul';sys.path.insert(0,str(SKILL/'scripts/engine'))
from hangeul.automation import run_job
from hangeul.job_safety import file_lock,native_guard

class BackportTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.root=Path(self.folder.name)
        self.file=self.root/'합성.hwpx'
        run_job({'action':'create','format':'hwpx','output':str(self.file),'blocks':[
            {'type':'paragraph','text':'검증할 한글 원문'},
            {'type':'table','columns_mm':[50,80],'rows':[[{'text':'첫 셀'},{'text':'둘째 셀'}],[{'text':'셋째 셀'},{'text':'넷째 셀'}]]}]})
    def tearDown(self):self.folder.cleanup()
    def cli(self,job,*options):
        request=self.root/'job.json';request.write_text(json.dumps(job,ensure_ascii=False),'utf-8')
        env={**os.environ,'PYTHONNOUSERSITE':'1','PYTHONUTF8':'1'};env.pop('NARAE_OFFICE_ROOT',None);env.pop('NARAE_HANGUL_EXE',None)
        process=subprocess.run([sys.executable,'-I','-X','utf8',str(SKILL/'scripts/narae.py'),'run',str(request),'--python',sys.executable,*options],env=env,capture_output=True,encoding='utf-8',timeout=60)
        result=json.loads(process.stdout);self.assertEqual(process.returncode,0 if result['success'] else 1,process.stderr);return result
    def test_style_fix_required_hash_and_compact_read(self):
        read=self.cli({'action':'inspect','input':str(self.file)})
        self.assertNotIn('runs',read['paragraphs'][0])
        target=read['paragraphs'][0]['id'];output=self.root/'edited.hwpx'
        job={'action':'edit','input':str(self.file),'output':str(output),'operations':[{'op':'format','target':target,'text_style':{'size':17,'bold':True}}]}
        self.assertFalse(self.cli(job)['success']);self.assertFalse(output.exists())
        job['expected_input_sha256']='0'*64;self.assertFalse(self.cli(job)['success']);self.assertFalse(output.exists())
        job['expected_input_sha256']=read['input_sha256'];self.assertTrue(self.cli(job)['success'])
        after=self.cli({'action':'inspect','input':str(output)});style=after['paragraphs'][0]['text_style']
        self.assertEqual(style['size'],17);self.assertTrue(style['bold'])
        self.assertIn('runs',self.cli({'action':'inspect','input':str(output)},'--full')['paragraphs'][0])
    def test_row_insert_retains_header_and_real_style_references(self):
        output=self.root/'rows.hwpx';before=hashlib.sha256(self.file.read_bytes()).hexdigest()
        result=self.cli({'action':'edit','input':str(self.file),'output':str(output),'expected_input_sha256':before,
                         'operations':[{'op':'insert_row','table_index':0,'row_index':1,'height_mm':8}]})
        self.assertTrue(result['success'],result)
        with zipfile.ZipFile(self.file) as a,zipfile.ZipFile(output) as b:
            self.assertEqual(a.namelist(),b.namelist())
            for name in a.namelist():
                if name!='Contents/section0.xml':self.assertEqual(a.read(name),b.read(name),name)
        read=self.cli({'action':'inspect','input':str(output)});self.assertEqual(read['tables'][0]['rows'],3)
        self.assertEqual(hashlib.sha256(self.file.read_bytes()).hexdigest(),before)
    def test_revision_marker_refuses_raw_edit_and_never_reports_empty_success(self):
        marked=self.root/'tracked.hwpx'
        with zipfile.ZipFile(self.file) as a,zipfile.ZipFile(marked,'w') as b:
            for info in a.infolist():
                data=a.read(info.filename)
                if info.filename=='Contents/header.xml':data=data.replace(b'</hh:head>',b'<hh:trackChangeAuthor/></hh:head>')
                b.writestr(info,data)
        from hangeul.revision import revision_state
        self.assertIsNotNone(revision_state(marked))
        read=self.cli({'action':'inspect','input':str(marked)});self.assertFalse(read['textDetailsLoaded']);self.assertTrue(read['revisionState']['requiresNative'])
        edited=self.cli({'action':'edit','input':str(marked),'output':str(self.root/'refused.hwpx'),'expected_input_sha256':hashlib.sha256(marked.read_bytes()).hexdigest(),'operations':[]})
        self.assertFalse(edited['success']);self.assertFalse((self.root/'refused.hwpx').exists())
    def test_lock_releases_after_error_and_unsaved_running_document_is_refused(self):
        with patch.dict(os.environ,{'NARAE_HANGUL_HOME':str(self.root/'home')}):
            with file_lock(self.file):
                with self.assertRaises(ValueError):
                    with file_lock(self.file):pass
            with file_lock(self.file):pass
        if os.name=='nt':
            class Doc:Modified=True
            with patch('hangeul.job_safety.open_match',return_value=(object(),Doc())):
                with self.assertRaisesRegex(ValueError,'저장하지 않은'):
                    with native_guard(self.file):pass

if __name__=='__main__':unittest.main()
