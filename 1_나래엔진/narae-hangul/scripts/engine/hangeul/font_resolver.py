"""Installed SFNT faces and verified HFT outlines for the independent view.

Original installed TTF/OTF files and two normal Hanyang Hangul/Latin HFT pairs
are resolved. Inputs stay read-only; generated HFT OpenType CFF files live in
the user's Qt cache directory. Registration is process-local. Source fonts and
CFF caches are not distribution assets. The HFT charset is document-independent.
"""
from __future__ import annotations

import hashlib
import io
import json
import marshal
import os
from pathlib import Path
import struct
import tempfile
from types import FunctionType

from . import hft, hft_cff
from .hft import HftFont, HftError
from .hft_cff import convert_outline_fonts


CHARACTERS = tuple(chr(c) for c in range(32,127)) + tuple(chr(c) for c in range(0xac00,0xd7a4))
CHARSET = frozenset(map(ord,CHARACTERS))
FONT_PAIRS = {"한양중고딕": ("HGJGT.HFT","ENJGT.HFT"), "한양신명조": ("HGSMJ.HFT","ENSMJ.HFT")}
ALIASES = {"신명조":"한양신명조"}
MAX_MAP_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_OPERATIONS = 2_000_000
MAX_SFNT_BYTES = 64 * 1024 * 1024


def _read_bounded(path, limit):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("파일 크기 또는 형식이 올바르지 않습니다.")
    with path.open("rb") as stream:
        data = stream.read(limit+1)
    if len(data)>limit:
        raise ValueError("파일 크기 제한을 초과했습니다.")
    return data


def _inside(path, directory):
    return path == directory or directory in path.parents


def _font_map(path):
    """Read only UTF-16LE FontDB's two relevant script sections."""
    data = _read_bounded(path,MAX_MAP_BYTES)
    if not data.startswith(b"\xff\xfe"):
        raise ValueError("FontDB는 UTF-16LE 파일이어야 합니다.")
    text = data[2:].decode("utf-16le",errors="strict")
    result, section = {}, ""
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith(("#",";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section=line[1:-1].strip()
        elif section in ("Font Definition - Hangul","Font Definition - Latin") and "=" in line:
            name,value=line.split("=",1)
            name=name.strip()
            if name in FONT_PAIRS:
                filename=value.split(",",1)[0].strip()
                # Configurations cannot redirect a known family to other paths.
                if not filename or Path(filename).name!=filename or any(c in filename for c in ("/","\\",":")):
                    raise ValueError("FontDB 글꼴 경로는 파일 이름이어야 합니다.")
                result.setdefault(section,{})[name]=filename
    return result


def _installed_directories():
    directories=[]
    for base in dict.fromkeys((os.environ.get("ProgramFiles(x86)",r"C:\Program Files (x86)"),
                              os.environ.get("ProgramFiles",r"C:\Program Files"))):
        for year,version in ((2024,130),(2022,120),(2020,110),(2018,100),(2014,90)):
            office=Path(base)/"Hnc"/f"Office {year}"/f"HOffice{version}"
            directories.extend((office/"Bin"/"Shared"/"Fonts",office/"Shared"/"Fonts"))
    return directories


def _installed_sfnt_directories():
    directories=[Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts']
    for directory in _installed_directories():
        shared=directory.parent
        directories.extend((shared/'TTF'/'All',shared/'TTF'/'Hwp'))
    return list(dict.fromkeys(directories))


def _sfnt_names(source):
    from fontTools.ttLib import TTFont,TTLibError
    if isinstance(source,(str,Path)):
        with open(source,'rb') as stream:return _sfnt_names(stream)
    try:
        with TTFont(source,lazy=True) as font:
            names={record.toUnicode().strip() for record in font['name'].names if record.nameID in (1,16)}
            compact={name.replace(' ','').casefold() for name in names}
            names.update(record.toUnicode().strip() for record in font['name'].names
                         if record.nameID==6 and record.toUnicode().strip().casefold() in compact)
    except (TTLibError,AssertionError,IndexError) as error:
        raise ValueError('설치 글꼴 이름 테이블을 읽지 못했습니다.') from error
    return {name for name in names if name and not any(ord(c)<32 for c in name)}


class InstalledSfntResolver:
    """Register requested, already installed TTF/OTF faces for this process.

    Some Hancom fonts live in Shared/TTF rather than the Windows font database.
    Their localized name records are indexed once, and only requested families
    are read and registered. No installed font is changed, copied or packaged.
    """
    _indexes={}
    _registered={}

    def __init__(self,*,font_dirs=None):
        override=os.environ.get('NARAE_TTF_FONT_DIR')
        self._directories=[Path(p) for p in font_dirs] if font_dirs is not None else (
            [Path(override)] if override else _installed_sfnt_directories())
        self._resolved={}
        self.fonts={}
        self.warnings=[]

    def _index(self):
        key=tuple(str(p) for p in self._directories)
        if key in self._indexes:return self._indexes[key]
        result={}
        for directory in self._directories:
            if not directory.is_absolute() or str(directory).startswith('\\\\') or not directory.is_dir():continue
            directory=directory.resolve()
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() not in {'.ttf','.otf'}:continue
                try:
                    path=path.resolve(strict=True)
                    if not _inside(path,directory) or path.stat().st_size>MAX_SFNT_BYTES:continue
                    for name in _sfnt_names(str(path)):
                        result.setdefault(name,[]).append((path,directory))
                except (OSError,ValueError,UnicodeError,KeyError,struct.error):continue
        self._indexes[key]=result
        return result

    def resolve(self,family):
        from PySide6.QtGui import QGuiApplication,QFontDatabase
        if not isinstance(family,str) or not family.strip() or QGuiApplication.instance() is None:return None
        family=family.strip()
        if family in self._resolved:return self._resolved[family]
        records=[];selected=None
        for path,directory in self._index().get(family,[])[:16]:
            try:
                if not _inside(path.resolve(strict=True),directory):raise ValueError('설치 글꼴 경로가 폴더 밖입니다.')
                data=_read_bounded(path,MAX_SFNT_BYTES)
                if family not in _sfnt_names(io.BytesIO(data)):raise ValueError('설치 글꼴 이름이 변경됐습니다.')
                checksum=hashlib.sha256(data).hexdigest()
                key=(str(path),checksum)
                if key not in self._registered:
                    font_id=QFontDatabase.addApplicationFontFromData(data)
                    if font_id<0:raise ValueError('Qt가 설치 글꼴을 등록하지 못했습니다.')
                    families=QFontDatabase.applicationFontFamilies(font_id)
                    if not families:
                        QFontDatabase.removeApplicationFont(font_id)
                        raise ValueError('설치 글꼴의 Qt 이름이 없습니다.')
                    self._registered[key]=(font_id,tuple(families))
                _,families=self._registered[key]
                selected=selected or (family if family in families else families[0])
                records.append({'path':str(path),'sha256':checksum,'qt_families':list(families)})
            except (OSError,ValueError,UnicodeError,KeyError,struct.error) as error:
                message=family+' 설치 글꼴을 읽지 못했습니다: '+str(error)
                if message not in self.warnings:self.warnings.append(message)
        self._resolved[family]=selected
        if selected:
            self.fonts[family]={'source_family':family,'qt_family':selected,'source_fonts':records,
                                'registration':'process-local-memory','original_sfnt':True}
        return selected


def _compiler_digest():
    digest=hashlib.sha256()
    for module in (hft,hft_cff):
        path=Path(module.__file__)
        if path.is_file():
            digest.update(_read_bounded(path,1024*1024))
        else:  # PyInstaller stores Python modules inside its PYZ archive.
            functions={}
            for name,value in vars(module).items():
                if isinstance(value,FunctionType) and value.__module__==module.__name__:
                    functions[name]=value
                elif isinstance(value,type) and value.__module__==module.__name__:
                    for method_name,method in vars(value).items():
                        if isinstance(method,(staticmethod,classmethod)):
                            method=method.__func__
                        if isinstance(method,FunctionType):
                            functions[name+"."+method_name]=method
                elif name.isupper() and isinstance(value,(str,bytes,int,float,bool,type(None))):
                    digest.update(name.encode()+repr(value).encode())
            for name,function in sorted(functions.items()):
                digest.update(name.encode()+marshal.dumps(function.__code__))
    return digest.hexdigest()


COMPILER_DIGEST = _compiler_digest()


def _validate_cff(data, family, supported):
    """Bound the sfnt directory before allowing fontTools or Qt to read it."""
    from fontTools.ttLib import TTFont
    if len(data)<12 or len(data)>MAX_CACHE_BYTES or data[:4]!=b"OTTO":
        raise ValueError("캐시는 OpenType CFF 파일이어야 합니다.")
    count=struct.unpack_from(">H",data,4)[0]
    if not 1<=count<=32 or 12+count*16>len(data):
        raise ValueError("캐시 글꼴 테이블 수가 올바르지 않습니다.")
    tags=set()
    for index in range(count):
        tag,_,offset,length=struct.unpack_from(">4sIII",data,12+16*index)
        if tag in tags or offset<12+16*count or offset+length>len(data):
            raise ValueError("캐시 글꼴 테이블 범위가 올바르지 않습니다.")
        tags.add(tag)
    if not {b"CFF ",b"cmap",b"name",b"head",b"hmtx",b"maxp"}<=tags:
        raise ValueError("캐시 글꼴의 필수 테이블이 없습니다.")
    from fontTools.ttLib import TTLibError
    try:
        font = TTFont(io.BytesIO(data),lazy=False)
        with font:
            if font["head"].unitsPerEm!=1000 or font["maxp"].numGlyphs>len(CHARACTERS)+1:
                raise ValueError("캐시 글꼴 크기 또는 글리프 수가 올바르지 않습니다.")
            if font["name"].getDebugName(1)!=family:
                raise ValueError("캐시 글꼴 이름이 일치하지 않습니다.")
            cmap=font.getBestCmap() or {}
            if set(cmap)!=set(supported) or not set(cmap)<=CHARSET or not {32,65,0xac00,0xd55c}<=set(cmap):
                raise ValueError("캐시 글꼴 문자 범위가 일치하지 않습니다.")
            metrics=font["hmtx"].metrics
            if any(not 0<metrics[glyph][0]<=2000 for glyph in cmap.values()) or metrics[cmap[32]][0]!=500:
                raise ValueError("캐시 글꼴 글자 폭이 검증 조건과 다릅니다.")
    except (TTLibError, AssertionError, IndexError, KeyError, struct.error) as error:
        raise ValueError("캐시 글꼴 테이블을 해석할 수 없습니다.") from error
    return frozenset(cmap)


class InstalledHftResolver:
    """resolve(family) returns a private Qt family; supports checks its cmap.

    Missing/bitmap/unknown fonts return None with explicit fallback warnings.
    `fonts` contains one compact, validated record per canonical family. The
    source document's original family and HFT type are never modified.
    """
    _registered={}

    def __init__(self, *, font_dir=None, cache_dir=None):
        from PySide6.QtCore import QStandardPaths
        override=font_dir if font_dir is not None else os.environ.get("NARAE_HFT_FONT_DIR")
        self._font_dirs=[Path(override)] if override else _installed_directories()
        location=QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        self._cache_dir=Path(cache_dir) if cache_dir is not None else (Path(location)/"narae-hft" if location else None)
        self.warnings=[]
        self.fonts={}
        self._resolved={}
        self._supported={}
        self._failed=set()

    def _warn(self,message):
        if message not in self.warnings:
            self.warnings.append(message)

    def _sources(self,family):
        errors=[]
        for directory in self._font_dirs:
            try:
                if not directory.is_absolute() or str(directory).startswith("\\\\"):
                    raise ValueError("글꼴 폴더는 로컬 절대 경로여야 합니다.")
                if not directory.is_dir():
                    continue
                directory=directory.resolve(strict=True)
                if str(directory).startswith("\\\\"):
                    raise ValueError("글꼴 폴더는 로컬 절대 경로여야 합니다.")
                names=FONT_PAIRS[family]
                configs=[(directory/"FontDB"/"hftinfo.dat",directory),(directory/"hftinfo.dat",directory)]
                if directory.name.lower()=="fonts" and directory.parent.name.lower()=="shared":
                    base=directory.parent.parent
                    binary=base if base.name.lower()=="bin" else base/"Bin"
                    configs.insert(0,(binary/"FontDB"/"hftinfo.dat",binary/"FontDB"))
                selected=next(((p,allowed) for p,allowed in configs if p.is_file()),None)
                config_info=None
                if selected is not None:
                    config,allowed=selected
                    config=config.resolve(strict=True)
                    if not _inside(config,allowed):
                        raise ValueError("FontDB 경로가 허용된 설치 폴더 밖입니다.")
                    mapping=_font_map(config)
                    mapped=tuple(mapping.get(section,{}).get(family,"") for section in ("Font Definition - Hangul","Font Definition - Latin"))
                    if tuple(n.upper() for n in mapped)!=names:
                        raise ValueError("설치 설정의 글꼴 조합이 검증된 기본 조합과 다릅니다.")
                    config_info={"path":str(config),"sha256":hashlib.sha256(_read_bounded(config,MAX_MAP_BYTES)).hexdigest()}
                fonts=[]
                for name in names:
                    path=(directory/name).resolve(strict=True)
                    if not _inside(path,directory):
                        raise ValueError("글꼴 경로가 허용된 폴더 밖입니다.")
                    font=HftFont(path)
                    if not font.outline_count:
                        raise HftError("지원하는 외곽선이 없는 HFT입니다.")
                    fonts.append(font)
                return fonts,config_info,directory
            except (OSError,ValueError,UnicodeError) as error:
                errors.append(str(error))
        raise ValueError(errors[0] if errors else "검증된 설치 HFT 파일을 찾지 못했습니다.")

    def _settings(self,family,fonts):
        return {"version":1,"source_family":family,"compiler_sha256":COMPILER_DIGEST,
            "source_fonts":[{"path":str(f.path.resolve()),"sha256":f.sha256} for f in fonts],
            "charset":"modern-hangul-11172+ascii-32-126","quantization":"native-pdf","space_advance_em":.5,
            "max_total_operations":MAX_TOTAL_OPERATIONS}

    def _cache(self,settings,paths):
        key=hashlib.sha256(json.dumps(settings,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        qt_family="NaraeHFT-"+key[:20]
        if self._cache_dir is None or not self._cache_dir.is_absolute():
            raise ValueError("글꼴 캐시는 로컬 절대 경로여야 합니다.")
        root=self._cache_dir.resolve()
        if str(root).startswith("\\\\"):
            raise ValueError("글꼴 캐시는 로컬 절대 경로여야 합니다.")
        if any(_inside(root,p.parent) for p in paths):
            raise ValueError("설치 글꼴 폴더에는 캐시를 저장하지 않습니다.")
        root.mkdir(parents=True,exist_ok=True)
        cache,manifest=root/(key+".otf"),root/(key+".json")
        try:
            if not _inside(cache.resolve(),root) or not _inside(manifest.resolve(),root):
                raise ValueError("캐시 글꼴 경로가 캐시 폴더 밖입니다.")
            metadata=json.loads(_read_bounded(manifest,MAX_MAP_BYTES))
            if metadata.get("settings")!=settings or metadata.get("qt_family")!=qt_family:
                raise ValueError("캐시의 원본 글꼴 정보가 일치하지 않습니다.")
            data=_read_bounded(cache,MAX_CACHE_BYTES)
            if hashlib.sha256(data).hexdigest()!=metadata.get("cache_sha256"):
                raise ValueError("캐시 글꼴 SHA256이 일치하지 않습니다.")
            supported=_validate_cff(data,qt_family,metadata.get("supported_codepoints",[]))
            return cache,metadata,supported,True,data
        except (OSError,ValueError,KeyError,TypeError,AttributeError,ImportError,struct.error) as error:
            if cache.exists() or manifest.exists():
                self._warn(settings["source_family"]+" 글꼴 캐시 검증에 실패해 다시 생성합니다.")
        with tempfile.TemporaryDirectory(prefix="building-",dir=root) as directory:
            temporary=Path(directory)/"font.otf"
            generated=convert_outline_fonts(paths,temporary,characters=CHARACTERS,family=qt_family,
                quantization="native-pdf",space_advance_em=.5,max_total_operations=MAX_TOTAL_OPERATIONS)
            original=[(str(p),hashlib.sha256(_read_bounded(p,hft.MAX_FONT_BYTES)).hexdigest()) for p in paths]
            expected=[(f["path"],f["sha256"]) for f in settings["source_fonts"]]
            produced=[(str(Path(f["path"]).resolve()),f["sha256"]) for f in generated["source_fonts"]]
            if original!=expected or produced!=expected:
                raise ValueError("변환 도중 설치 글꼴이 변경됐습니다.")
            data=_read_bounded(temporary,MAX_CACHE_BYTES)
            supported=sorted(CHARSET-set(generated["unavailable_codepoints"]))
            _validate_cff(data,qt_family,supported)
            checksum=hashlib.sha256(data).hexdigest()
            if checksum!=generated["cache_sha256"]:
                raise ValueError("변환한 글꼴의 SHA256이 일치하지 않습니다.")
            metadata={"settings":settings,"qt_family":qt_family,"cache_sha256":checksum,
                "supported_codepoints":supported,"glyph_count":len(supported)}
            metadata_path=Path(directory)/"manifest.json"
            metadata_path.write_text(json.dumps(metadata,ensure_ascii=False,sort_keys=True),encoding="utf-8")
            # If interrupted between replacements, the next load rejects the
            # incomplete pair. The original HFT files are never write targets.
            os.replace(temporary,cache)
            os.replace(metadata_path,manifest)
        return cache,metadata,frozenset(supported),False,data

    def resolve(self,family):
        from PySide6.QtGui import QGuiApplication,QFontDatabase
        if not isinstance(family,str) or not family.strip():
            return None
        family=ALIASES.get(family.strip(),family.strip())
        if family in self._resolved:
            return self._resolved[family]
        if family in self._failed:
            return None
        if family not in FONT_PAIRS:
            self._failed.add(family)
            self._warn(family+" HFT는 독립 외곽선 변환을 지원하지 않아 대체 글꼴을 사용합니다.")
            return None
        if QGuiApplication.instance() is None:
            self._warn("HFT 글꼴 등록은 화면 초기화 후 가능합니다.")
            return None
        try:
            fonts,config,directory=self._sources(family)
            settings=self._settings(family,fonts)
            cache,metadata,supported,reused,data=self._cache(settings,[f.path for f in fonts])
            registered_key=(str(cache),metadata["cache_sha256"])
            if registered_key in self._registered:
                font_id,qt_family=self._registered[registered_key]
            else:
                # Register the bytes already checked above. Qt's Windows
                # file registration can lock disk caches, and reading the path
                # again would allow a replacement after SHA verification.
                font_id=QFontDatabase.addApplicationFontFromData(data)
                if font_id<0:
                    raise HftError("Qt가 검증한 외곽선 글꼴을 등록하지 못했습니다.")
                families=QFontDatabase.applicationFontFamilies(font_id)
                if metadata["qt_family"] not in families:
                    QFontDatabase.removeApplicationFont(font_id)
                    raise HftError("등록한 Qt 글꼴 이름이 검증 정보와 다릅니다.")
                qt_family=metadata["qt_family"]
                self._registered[registered_key]=(font_id,qt_family)
            self._resolved[family]=qt_family
            self._supported[family]=supported
            self.fonts[family]={"source_family":family,"qt_family":qt_family,
                "source_fonts":settings["source_fonts"],"config":config,"cache_path":str(cache),
                "cache_sha256":metadata["cache_sha256"],"cache_format_version":1,
                "registration":"process-local-memory","glyph_count":len(supported),
                "unavailable_standard_codepoints":len(CHARSET-supported),"space_advance_em":.5,
                "normal_outline_only":True,"native_hinting":False,"vertical_metrics_exact":False}
            self._warn(family+" HFT의 검증된 보통 외곽선을 사용합니다. 비KS 한글·한자·특수문자·굵게·기울임·힌팅은 원래 모양과 다를 수 있습니다.")
            return qt_family
        except (OSError,ValueError,KeyError,TypeError,AttributeError,ImportError,struct.error) as error:
            self._failed.add(family)
            self._warn(family+" HFT를 사용할 수 없어 대체 글꼴을 사용합니다: "+str(error))
            self.fonts[family]={"source_family":family,"status":"fallback","reason":str(error)}
            return None

    def supports(self,family,character):
        if not isinstance(character,str) or len(character)!=1 or not isinstance(family,str):
            return False
        family=ALIASES.get(family.strip(),family.strip())
        return bool(self.resolve(family) and ord(character) in self._supported[family])
