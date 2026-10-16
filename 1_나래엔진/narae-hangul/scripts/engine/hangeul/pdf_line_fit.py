"""Logical word/character spacing in native text frames."""
import unicodedata


def _divide(value,count):
    return -(abs(value)//count) if value<0 else value//count


def expand_line(text,advances,untracked,target,all_gaps=False):
    """Expand a continued justified line, excluding outer blank gaps.

    The caller supplies the visible final advance without terminal tracking.
    Positive space is divided over words, or characters when there are no
    word gaps. Integer remainder stays unused, as with native condensation.
    """
    if len(text)!=len(advances) or len(text)!=len(untracked):raise ValueError('line-fit-width-count')
    space=lambda c:c in ' \u3000'
    first=next((i for i,c in enumerate(text) if not space(c) and untracked[i]),None)
    last=next((i for i in range(len(text)-1,-1,-1) if not space(text[i]) and untracked[i]),None)
    if first is None:return None
    remaining=target-sum(advances[:last+1])
    if remaining<=0:return None
    start=0 if all_gaps else first
    words=[i for i in range(start,last) if space(text[i]) and untracked[i]]
    characters=[i for i in range(start,last) if not space(text[i]) and untracked[i]
                and not unicodedata.category(text[i]).startswith('M')]
    if not words and not characters:return None
    word=remaining//len(words) if words else 0
    character=remaining//len(characters) if not words else 0
    result=list(advances)
    for i in words:result[i]+=word
    for i in characters:result[i]+=character
    return dict(advances=result,word_adjustment=word,character_adjustment=character,
                word_gaps=len(words),character_gaps=len(characters),remaining=remaining)


def condense_line(text,advances,untracked,target,all_gaps=False):
    """Distribute overflow over word spaces, then eligible character gaps.

    Source widths and the frame are HWPUNIT integers. Division truncates
    toward zero; the remaining few units must not be rescaled or centered.
    """
    if len(text)!=len(advances) or len(text)!=len(untracked):raise ValueError('line-fit-width-count')
    space=lambda c:c in ' \u3000'
    first=next((i for i,c in enumerate(text) if not space(c) and untracked[i]),None)
    last=next((i for i in range(len(text)-1,-1,-1) if not space(text[i]) and untracked[i]),None)
    if first is None:return None
    used=sum(advances[:last+1]);overflow=target-used
    if overflow>=0:return None
    start=0 if all_gaps else first
    words=[i for i in range(start,last+1) if space(text[i]) and untracked[i]]
    characters=[i for i in range(start,last) if not space(text[i]) and untracked[i]
                and not unicodedata.category(text[i]).startswith('M')]
    word=_divide(overflow,len(words)) if words else 0;character=0
    minimum=min((untracked[i] for i in range(last+1) if space(text[i]) and untracked[i]),default=0);cap=(minimum+4)//5
    if characters and (not words or abs(word)>cap):
        word=-cap if words else 0
        character=_divide(overflow-word*len(words),len(characters))
    result=list(advances)
    for i in words:result[i]+=word
    for i in characters:result[i]+=character
    return dict(advances=result,word_adjustment=word,character_adjustment=character,
                word_gaps=len(words),character_gaps=len(characters),overflow=overflow)
