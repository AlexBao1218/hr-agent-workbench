"""Exact source addressing only. Never rewrite candidate facts, coverage or goals.

A quoted span may cross consecutive PDF text blocks. Resolve it only when its
entire normalized text is found contiguously, anchored in the named first block.
A disjoint excerpt of the SAME confirmed requirement may point at its complete
verbatim criterion instead. Foreign words/criteria and invented CV text fail.
Raw model outputs remain in their original role_output diagnostics.
"""
from copy import deepcopy
import re
from .evidence import normalize_evidence


def _tokens(text):
    return re.findall(r'[A-Za-z0-9]+|[\u4e00-\u9fff]|[^\s]',normalize_evidence(text).casefold())


def _search(text):
    return normalize_evidence(text).translate(str.maketrans({'’':"'",'‘':"'",'“':'"','”':'"'}))


def _subsequence(needles,values):
    values=iter(values)
    return all(any(word==value for value in values) for word in needles)


def resolve_row_references(data,document,standards):
    result=deepcopy(data);repairs=[]
    criteria={c['id']:c for c in standards['criteria']}
    blocks=document['blocks'];positions={b['id']:i for i,b in enumerate(blocks)}
    for ri,row in enumerate(result.get('rows',[])):
        criterion=criteria.get(row.get('criterionId'))
        if not criterion:continue
        for section in ('evidence','unknowns'):
            for ii,item in enumerate(row.get(section,[])):
                quote=item.get('requirementQuote','');norm=normalize_evidence(quote)
                originals=[criterion['description'],criterion['jdQuote']]
                if norm and not any(norm in normalize_evidence(source) for source in originals):
                    words=_tokens(quote)
                    exact=next((source for source in originals if len(words)>=3 and _subsequence(words,_tokens(source))),None)
                    if exact:
                        item['requirementQuote']=exact
                        repairs.append({'kind':'confirmed_requirement_reference','path':f'rows/{ri}/{section}/{ii}/requirementQuote','original':quote,'resolved':exact})
                resolved=[]
                for ref in item.get('evidence',[]):
                    i=positions.get(ref['blockId']);q=normalize_evidence(ref['quote'])
                    if i is None or not q or q in normalize_evidence(blocks[i]['text']):
                        resolved.append(ref);continue
                    chosen=None;search=_search(ref['quote'])
                    kind='contiguous_multiblock_quote'
                    if search in _search(blocks[i]['text']):
                        chosen=[{'blockId':blocks[i]['id'],'quote':blocks[i]['text']}]
                    # PDF readers may emit a standalone bullet before its text.
                    # Resolve only the immediately adjacent same-page text when
                    # the entire quotation occurs there; never search elsewhere.
                    if (not chosen and re.fullmatch(r'[•·●▪◦‣⁃*\-]',blocks[i]['text'].strip())
                            and i+1<len(blocks) and blocks[i+1].get('page')==blocks[i].get('page')
                            and search in _search(blocks[i+1]['text'])):
                        chosen=[{'blockId':blocks[i+1]['id'],'quote':blocks[i+1]['text']}]
                        kind='adjacent_bullet_quote'
                    for end in range(i+1,min(i+8,len(blocks))):
                        if chosen:break
                        if blocks[end].get('page')!=blocks[i].get('page'):break
                        joined=' '.join(_search(b['text']) for b in blocks[i:end+1])
                        start=joined.find(search)
                        if 0<=start<len(_search(blocks[i]['text'])):
                            chosen=[{'blockId':b['id'],'quote':b['text']} for b in blocks[i:end+1] if b['text'].strip()];break
                    if chosen:
                        resolved.extend(chosen)
                        repairs.append({'kind':kind,'path':f'rows/{ri}/{section}/{ii}/evidence','original':ref,'resolved':deepcopy(chosen)})
                    else:resolved.append(ref)
                item['evidence']=resolved
    return result,repairs
