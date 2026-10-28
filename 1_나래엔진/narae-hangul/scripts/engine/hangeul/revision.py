"""Keep raw file offsets away from live Hancom revision text.

Hancom documents HWPTAG_TRACK_CHANGE/AUTHOR as DocInfo tags 96/97:
https://forum.developer.hancom.com/t/hwp-binary-format/1963
Our native regression reproduces duplicated deleted/inserted text in BodyText.
Do not flatten, accept, or discard that history to make a file edit possible.
"""
import struct,zipfile,zlib
from xml.etree import ElementTree as ET

GUIDANCE='변경 추적 정보가 있는 문서입니다. 파일 엔진의 텍스트/서식 ID는 현재 보이는 내용과 다를 수 있습니다. 나래한글 CLI live-read로 현재 내용을 읽고 live-edit로 편집하세요. 변경 추적은 보존했습니다.'

def revision_state(file):
    evidence=[]
    if file.suffix.lower()=='.hwp':
        import olefile
        if not olefile.isOleFile(file):return None
        with olefile.OleFileIO(file) as archive:
            header=archive.openstream('FileHeader').read()
            if len(header)<40:return None  # HWP 3 is handled by its own reader.
            flags=struct.unpack_from('<I',header,36)[0]
            if flags & (1<<14):evidence.append('FileHeader.trackChanges')
            if archive.exists('DocInfo'):
                raw=archive.openstream('DocInfo').read()
                if flags&1:
                    decoder=zlib.decompressobj(-15);raw=decoder.decompress(raw,64*1024*1024)
                    if not decoder.eof:raise ValueError('DocInfo 압축 정보가 잘못되었거나 너무 큽니다.')
                offset=0
                while offset+4<=len(raw):
                    header=struct.unpack_from('<I',raw,offset)[0];offset+=4;tag=header&1023;size=header>>20
                    if size==4095:
                        if offset+4>len(raw):raise ValueError('DocInfo 레코드가 잘렸습니다.')
                        size=struct.unpack_from('<I',raw,offset)[0];offset+=4
                    if offset+size>len(raw):raise ValueError('DocInfo 레코드 크기가 잘못되었습니다.')
                    if tag in (96,97):evidence.append('DocInfo.'+str(tag))
                    offset+=size
    elif file.suffix.lower()=='.hwpx':
        with zipfile.ZipFile(file) as archive:
            for item in archive.infolist():
                if item.filename!='Contents/header.xml' and not (item.filename.startswith('Contents/section') and item.filename.endswith('.xml')):continue
                if item.file_size>64*1024*1024:raise ValueError('HWPX XML이 너무 큽니다.')
                for node in ET.fromstring(archive.read(item)).iter():
                    local=node.tag.rsplit('}',1)[-1]
                    if local in {'insertBegin','insertEnd','deleteBegin','deleteEnd','trackChange','trackChangeAuthor'}:
                        evidence.append(item.filename+':'+local)
    if evidence:return {'requiresNative':True,'evidence':sorted(set(evidence)),'guidance':GUIDANCE}
    return None
