"""Small, stable agent output. --full retains the complete engine response."""
def compact(response, *, full=False, offset=0, limit=None):
    if full:return response
    if type(offset) is not int or offset<0 or (limit is not None and (type(limit) is not int or limit<0)):
        raise ValueError('offset과 limit는 0 이상의 정수로 지정하세요.')
    result=dict(response)
    if response.get('action') in ('create','edit'):
        for name in ('paragraphs','tables','images'):result.pop(name,None)
        return result
    if 'paragraphs' in response:
        original=response['paragraphs'];items=original[offset:] if limit is None else original[offset:offset+limit]
        def pick(value,names):return {name:value[name] for name in names if name in value}
        result['paragraphs']=[{**pick(p,('id','text','editable_text')),
                              'text_style':pick(next((run['text_style'] for run in p.get('runs',[]) if run.get('text')),p.get('text_style',{})),('font','size','bold','italic','underline','strike','color','background','superscript','subscript','stretch','letter_spacing')),
                              'paragraph_style':pick(p.get('paragraph_style',{}),('align','left','right','indent','before','after','line','line_type','page_break'))} for p in items]
        result.update(view='compact',offset=offset,nextOffset=offset+len(items),hasMore=offset+len(items)<len(original))
    return result
