"""Install the independent Hangul skill without redirecting it to Office."""
import argparse,json,os,shutil,sys,time
from pathlib import Path

def install(source,directory,python,backup_root=None):
    source=source.resolve();directory=directory.resolve();directory.mkdir(parents=True,exist_ok=True)
    target=directory/'narae-hangul'
    if target.is_symlink() or target.resolve().parent!=directory:raise ValueError('Linked skill target refused')
    backup=Path(backup_root or directory.parent/'skill-backups')/('narae-hangul-'+time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()))
    files=[(p.relative_to(source),p.read_bytes()) for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc' and p.name!='runtime.json']
    files.append((Path('runtime.json'),json.dumps({'python':str(python.resolve())},ensure_ascii=False,indent=2).encode('utf-8')))
    changed=0
    for relative,data in files:
        output=target/relative
        if not output.resolve().is_relative_to(directory) or output.is_symlink():raise ValueError('Skill file path escapes installation')
        if output.exists():
            if output.read_bytes()==data:continue
            saved=backup/relative;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(output,saved)
        output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(data);changed+=1
    return {'success':True,'skill':str(target),'python':str(python),'changed':changed,'backup':str(backup) if backup.exists() else None}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--skills-directory',type=Path,default=Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'skills')
    parser.add_argument('--python',type=Path,default=Path(sys.executable))
    parser.add_argument('--backup-root',type=Path)
    args=parser.parse_args()
    print(json.dumps(install(args.source,args.skills_directory,args.python,args.backup_root),ensure_ascii=False))
