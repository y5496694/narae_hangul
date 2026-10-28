"""Conservative HWPX table patches; preserve all ZIP entries and outer XML.

Caller validates bounds and rejects partial overlaps with merged cells. Keep
style references from real cells, update row counts/coordinates, and preserve
merged text unless the user explicitly supplies replacement text.
"""
import copy,io,zipfile
from xml.etree import ElementTree as ET
HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'
def mm(value):return round(value*7200/25.4)
def integer(element,key):
    if element is None:raise ValueError('필수 표 크기/좌표가 없습니다.')
    return int(element.get(key,'0'))
def cells(table):
    return [(tr,tc) for tr in table.findall(HP+'tr') for tc in tr.findall(HP+'tc')]
def coordinate(tc):
    addr,span=tc.find(HP+'cellAddr'),tc.find(HP+'cellSpan')
    return integer(addr,'rowAddr'),integer(addr,'colAddr'),integer(span,'rowSpan'),integer(span,'colSpan')
def size(tc):
    sz=tc.find(HP+'cellSz');return integer(sz,'width'),integer(sz,'height')
def change_total(table,key,delta):
    sz=table.find(HP+'sz')
    if sz is not None:sz.set(key,str(max(0,integer(sz,key)+delta)))
def paragraph(sublist,text,used_ids):
    old=sublist.find(HP+'p');attrs=dict(old.attrib) if old is not None else {}
    next_id=max(used_ids,default=0)+1
    if next_id>0xffffffff:raise ValueError('새 문단 ID를 배정할 수 없습니다.')
    used_ids.add(next_id);attrs.update(id=str(next_id),pageBreak='0',columnBreak='0',merged='0')
    run=old.find(HP+'run') if old is not None else None
    if run is None:raise ValueError('실제 셀의 글자 서식 참조를 찾지 못했습니다.')
    result=ET.Element(HP+'p',attrs)
    new_run=ET.SubElement(result,HP+'run',{'charPrIDRef':run.get('charPrIDRef','0')})
    ET.SubElement(new_run,HP+'t').text=text
    return result
def apply_advanced_hwpx_operations(data,operations):
    from .hwpx_native import _xml_nodes
    with zipfile.ZipFile(io.BytesIO(data)) as zin:
        if len([n for n in zin.namelist() if n.startswith('Contents/section') and n.endswith('.xml')])!=1:
            raise ValueError('보존 표 구조 편집은 한 구역 문서를 지원합니다. 다중 구역은 한컴 경로를 사용하세요.')
        if len(zin.namelist())!=len(set(zin.namelist())):raise ValueError('중복 ZIP 항목이 있는 문서입니다.')
        name='Contents/section0.xml';raw=zin.read(name)
        # The byte parser rejects DTD/external entities and gives exact spans.
        nodes=[n for n in _xml_nodes(raw) if n['name']=='tbl']
        namespaces={}
        for _,pair in ET.iterparse(io.BytesIO(raw),events=['start-ns']):
            if pair[0] in namespaces and namespaces[pair[0]]!=pair[1]:raise ValueError('중첩된 XML namespace 재정의는 한컴 도구로 편집하세요.')
            namespaces[pair[0]]=pair[1]
        for prefix,uri in namespaces.items():
            if prefix and not (prefix.startswith('ns') and prefix[2:].isdigit()):ET.register_namespace(prefix,uri)
        root=ET.fromstring(raw);tables=root.findall('.//'+HP+'tbl')
        if len(nodes)!=len(tables):raise ValueError('표 XML 경계를 하나로 식별할 수 없습니다.')
        used_ids={int(p.get('id')) for p in root.iter(HP+'p') if p.get('id','').isdigit()}
        if len(operations)!=1:raise ValueError('표 구조는 한 번에 한 작업씩 편집하세요.')
        op=operations[0];index=op.get('table_index',0)
        if type(index) is not int or not 0<=index<len(tables):raise ValueError('표 인덱스가 범위 밖입니다.')
        table=tables[index];node=nodes[index]
        kind=op['op'];rows=table.findall(HP+'tr');before=cells(table)
        row_count,col_count=int(table.get('rowCnt','0')),int(table.get('colCnt','0'))
        def integer_arg(name,limit,default=None):
            value=op.get(name,default)
            if type(value) is not int or not 0<=value<limit:raise ValueError('표 범위 밖의 '+name)
            return value
        if kind in {'insert_row','delete_row'}:
            integer_arg('row_index',row_count+(kind=='insert_row'))
            if kind=='delete_row' and row_count<=1:raise ValueError('표의 마지막 행은 삭제할 수 없습니다.')
            if any(coordinate(tc)[2]>1 for _,tc in before):raise ValueError('세로 병합된 표의 행 변경은 한컴 경로를 사용하세요.')
        else:
            row=integer_arg('start_row' if kind=='merge_cells' else 'row',row_count)
            col=integer_arg('start_col' if kind=='merge_cells' else 'col',col_count)
            if not any(coordinate(tc)[:2]==(row,col) for _,tc in before):raise ValueError('병합 셀의 시작 좌표를 지정하세요.')
            if kind=='merge_cells':
                rs=integer_arg('row_span',row_count+1,1);cs=integer_arg('col_span',col_count+1,1)
                if rs<1 or cs<1 or row+rs>row_count or col+cs>col_count:raise ValueError('병합 범위가 표를 벗어납니다.')
                for _,tc in before:
                    cr,cc,crs,ccs=coordinate(tc)
                    if cr<row+rs and cr+crs>row and cc<col+cs and cc+ccs>col and not (row<=cr and cr+crs<=row+rs and col<=cc and cc+ccs<=col+cs):
                        raise ValueError('기존 병합 셀의 일부를 다시 병합할 수 없습니다.')
        if 'vertical_align' in op and op['vertical_align'] not in {'TOP','CENTER','BOTTOM'}:raise ValueError('셀 세로 정렬을 확인하세요.')
        for dimension_name in ('width_mm','height_mm'):
            if dimension_name in op and (type(op[dimension_name]) not in (int,float) or not 0<op[dimension_name]<=2000):raise ValueError('셀 크기를 양수 mm로 지정하세요.')
        if 'padding_mm' in op and (not isinstance(op['padding_mm'],list) or len(op['padding_mm'])!=4 or any(type(v) not in (int,float) or not 0<=v<=100 for v in op['padding_mm'])):
            raise ValueError('셀 여백은 0~100mm 숫자 네 개로 지정하세요.')
        if len(rows)!=int(table.get('rowCnt','0')):raise ValueError('표 행 수와 XML 구조가 다릅니다. 한글에서 문서를 확인하세요.')
        if kind in {'insert_row','delete_row'}:
            pos=op['row_index']
            if kind=='insert_row':
                candidates=[tr for tr in rows if all(coordinate(tc)[2:]==(1,1) for tc in tr.findall(HP+'tc'))]
                if not candidates:raise ValueError('병합되지 않은 기준 행이 없어 한컴 표 편집이 필요합니다.')
                reference=min(candidates,key=lambda tr:abs(rows.index(tr)-min(pos,len(rows)-1)))
                new=copy.deepcopy(reference);height=mm(op.get('height_mm',8))
                for tc in new.findall(HP+'tc'):
                    sub=tc.find(HP+'subList')
                    if sub is None:raise ValueError('기준 셀 본문이 없습니다.')
                    empty=paragraph(sub,'',used_ids)
                    for child in list(sub):sub.remove(child)
                    sub.append(empty);sub.set('id','');sub.set('linkListIDRef','0');sub.set('linkListNextIDRef','0');sub.set('hasTextRef','0');sub.set('hasNumRef','0')
                    tc.find(HP+'cellSz').set('height',str(height))
                table.insert(list(table).index(rows[min(pos,len(rows)-1)])+(pos==len(rows)),new)
                change_total(table,'height',height+int(table.get('cellSpacing','0')))
            else:
                old=rows[pos];height=max(size(tc)[1] for tc in old.findall(HP+'tc'))
                table.remove(old);change_total(table,'height',-height-int(table.get('cellSpacing','0')))
            current_rows=table.findall(HP+'tr');table.set('rowCnt',str(len(current_rows)))
            for r,tr in enumerate(current_rows):
                for tc in tr.findall(HP+'tc'):tc.find(HP+'cellAddr').set('rowAddr',str(r))
        elif kind=='merge_cells':
            row,col=op['start_row'],op['start_col'];rs,cs=op.get('row_span',1),op.get('col_span',1)
            selected=[(tr,tc) for tr,tc in before if row<=coordinate(tc)[0]<row+rs and col<=coordinate(tc)[1]<col+cs]
            target=next(tc for _,tc in selected if coordinate(tc)[:2]==(row,col));sub=target.find(HP+'subList')
            width=sum(size(tc)[0] for _,tc in selected if coordinate(tc)[0]==row)
            heights=[]
            for r in range(row,row+rs):
                values=[size(tc)[1] for _,tc in before if coordinate(tc)[0]==r and coordinate(tc)[2]==1]
                if not values:raise ValueError('병합 범위의 행 높이가 모호합니다. 한컴 표 편집을 사용하세요.')
                heights.append(max(values))
            if 'text' in op:
                merged=paragraph(sub,op['text'],used_ids)
                for child in list(sub):sub.remove(child)
                sub.append(merged)
            else:
                for _,tc in selected:
                    if tc is not target:
                        source=tc.find(HP+'subList')
                        if source is not None:
                            for child in source:sub.append(copy.deepcopy(child))
            for tr,tc in selected:
                if tc is not target:tr.remove(tc)
            span=target.find(HP+'cellSpan');span.set('rowSpan',str(rs));span.set('colSpan',str(cs))
            sz=target.find(HP+'cellSz');sz.set('width',str(width));sz.set('height',str(sum(heights)))
            if 'vertical_align' in op:sub.set('vertAlign',op['vertical_align'])
        elif kind=='set_cell_style':
            row,col=op['row'],op['col'];target=next(tc for _,tc in before if coordinate(tc)[:2]==(row,col))
            r,c,rs,cs=coordinate(target)
            if 'vertical_align' in op:target.find(HP+'subList').set('vertAlign',op['vertical_align'])
            if 'padding_mm' in op:
                margin=target.find(HP+'cellMargin')
                if margin is None:margin=ET.SubElement(target,HP+'cellMargin')
                for key,value in zip(['left','right','top','bottom'],op['padding_mm']):margin.set(key,str(mm(value)))
                target.set('hasMargin','1')
            for field,dimension,span,key in [('width_mm','width',cs,'column'),('height_mm','height',rs,'row')]:
                if field not in op:continue
                if span!=1:raise ValueError('병합 셀 크기는 한컴 표 편집을 사용하세요.')
                old=size(target)[0 if dimension=='width' else 1];delta=mm(op[field])-old
                for _,tc in before:
                    cr,cc,crs,ccs=coordinate(tc)
                    covers=(cc<=col<cc+ccs) if key=='column' else (cr<=row<cr+crs)
                    if covers:
                        sz=tc.find(HP+'cellSz');value=integer(sz,dimension)+delta
                        if value<=0:raise ValueError('표 크기 변경으로 셀이 0 이하가 됩니다.')
                        sz.set(dimension,str(value))
                change_total(table,dimension,delta)
        else:raise ValueError('지원하지 않는 표 작업입니다.')
        replacement=ET.tostring(table,encoding='utf-8')
        modified=raw[:node['begin']]+replacement+raw[node['end']:];_xml_nodes(modified)
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as zout:
            zout.comment=zin.comment
            for info in zin.infolist():zout.writestr(info,modified if info.filename==name else zin.read(info))
        return output.getvalue()
