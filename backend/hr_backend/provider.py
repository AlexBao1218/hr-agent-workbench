"""No credentials or raw provider errors are ever included in public errors."""
import json
import os
import re
from typing import Type, get_args
from pydantic_core import ErrorType
from pydantic import BaseModel, ValidationError

class ProviderError(RuntimeError):
    def __init__(self, code, message, schema_issues=None):
        super().__init__(message)
        self.code, self.message = code, message
        self.schema_issues = schema_issues

def structured_output_error(exc,schema):
    """Only declared field names, list offsets and built-in error codes; never values/messages."""
    fields=set()
    def collect(node):
        if isinstance(node,dict):
            fields.update(node.get('properties',{}))
            for value in node.values():collect(value)
        elif isinstance(node,list):
            for value in node:collect(value)
    collect(schema.model_json_schema())
    if isinstance(exc,ValidationError):
        known_types=set(get_args(ErrorType));issues=[]
        for error in exc.errors(include_input=False,include_context=False,include_url=False)[:8]:
            location=[part if isinstance(part,int) or part in fields else '<field>' for part in error['loc'][:12]]
            issues.append({'loc':location,'type':error['type'] if error['type'] in known_types else 'validation_error'})
    else:issues=[{'loc':[],'type':'json_invalid'}]
    details='; '.join(('.'.join(map(str,e['loc'])) or '<root>')+': '+e['type'] for e in issues)
    return ProviderError('model_invalid_output','Model structure invalid at '+details+'. Correct these fields; no assessment published.',issues)


from .prompts import SYSTEM, role_instructions

class OpenAIProvider:
    mode = 'live'
    def __init__(self):
        self.model = os.getenv('OPENAI_MODEL') or None
        self._key = os.getenv('OPENAI_API_KEY')
        self._base = os.getenv('OPENAI_BASE_URL') or None
        self.configured = bool(self.model and self._key)

    def request_arguments(self,role,payload,schema):
        config=payload.get('_agentRequest',{})
        arguments=dict(model=config.get('model') or self.model,store=False,
                    max_output_tokens=config.get('maxOutputTokens',12000),
                    input=[{'role':'system','content':config.get('systemPrompt') or role_instructions(role)},
                           {'role':'user','content':json.dumps({k:v for k,v in payload.items() if k!='_agentRequest'},ensure_ascii=False)}],text_format=schema)
        if config:
            effort=config['reasoningEffort'];arguments['reasoning']={'effort':effort}
            if effort=='none' and config.get('temperature') is not None:arguments['temperature']=config['temperature']
        return arguments

    def generate(self, role: str, payload: dict, schema: Type[BaseModel]) -> dict:
        if not self.configured:
            raise ProviderError('provider_unconfigured', 'Configure OPENAI_API_KEY and OPENAI_MODEL in the server environment, then retry.')
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._key, base_url=self._base, timeout=payload.get('_agentRequest',{}).get('timeoutSeconds',90), max_retries=0)
            response = client.responses.parse(**self.request_arguments(role,payload,schema))
            if response.status!='completed':
                raise ProviderError('model_incomplete','Model response did not complete; review configuration before a new run. No automatic repair.')
            if response.output_parsed is None:
                raise ProviderError('model_refused', 'Model refused or returned no final structured answer. No automatic repair.')
            return response.output_parsed.model_dump()
        except ProviderError:
            raise
        except (ValidationError,json.JSONDecodeError) as exc:
            raise structured_output_error(exc,schema) from None
        except Exception:
            raise ProviderError('model_failed', 'Model request failed. Check server model configuration, API access and connectivity, then retry.') from None

def fixture_profile(blocks):
    """Conservative section/date parser, independent of candidate and school identity.

    This adapter accepts conventional CV headings; unfamiliar layouts remain empty
    rather than synthesizing history. Every extracted record retains all source blocks.
    """
    from datetime import date
    lines=[]
    for block in blocks:
        for text in block['text'].splitlines():
            if text.strip(): lines.append((text.strip(),block))
    def refs(items):
        seen=set(); result=[]
        for _,block in items:
            if block['id'] not in seen:
                seen.add(block['id'])
                result.append({'blockId':block['id'],'quote':block['text'],'explanation':''})
        return result
    profile={'name':lines[0][0][:100] if lines else '待确认','education':[],'experiences':[], 'languages':[], 'availability':None}
    sections={'education':[],'internship':[],'employment':[],'project':[]}
    current=None
    headings={'education':'education','academic background':'education','教育背景':'education',
              'internships':'internship','internship experience':'internship','实习经历':'internship',
              'experience':'employment','experience and projects':'employment','additional work':'project','work experience':'employment','professional experience':'employment',
              'projects':'project','project experience':'project','项目经历':'project'}
    for line_index,(text,block) in enumerate(lines):
        key=text.lower().strip(' :：')
        if key in headings:
            current=headings[key]; continue
        if key in ('skills','skills and languages','languages','availability','summary','personal information','技能','语言'):
            current=None
        if current: sections[current].append((text,block))
        if re.search(r'available|availability|可到岗|可实习',text,re.I) and re.search(r'\d',text):
            profile['availability']=text
        elif re.fullmatch(r'(Available Period|Availability)\s*[:：]?',text,re.I):
            date_line=next((t for t,_ in lines[line_index+1:line_index+7] if re.search(r'\b20\d{2}\b',t) and re.search(r'[–—-]',t)),None)
            profile['availability']=date_line
        if re.search(r'English|Mandarin|Cantonese',text) and re.search(r'proficien|native|fluent|conversational|working',text,re.I):
            profile['languages'].append(text)
    # Explicit degree lines only. No inference from university reputation or name.
    edu=sections['education']
    if edu:
        degree_index=next((i for i,(text,_) in enumerate(edu) if re.search(r'Bachelor|BEng|BSc|\bBS\b|BBA|BA |Master|MSc|PhD|Doctor',text,re.I)),None)
        if degree_index is not None:
            degree_line=edu[degree_index][0]
            years=re.findall(r'\b(?:19|20)\d{2}\b',degree_line)
            level='bachelor' if re.search(r'Bachelor|BEng|BSc|\bBS\b|BBA|BA ',degree_line,re.I) else 'master' if re.search(r'Master|MSc',degree_line,re.I) else 'doctorate'
            major=next((re.sub(r'^Major\s*[:：]\s*','',text,flags=re.I).split('Major GPA')[0].strip(' ,') for text,_ in edu if re.match(r'^Major\s*[:：]',text,re.I)),'')
            status='studying' if re.search(r'pursuing|expected|in progress|current',degree_line,re.I) or (years and int(years[-1])>date.today().year) else 'graduated' if re.search(r'graduated|completed',degree_line,re.I) else 'unknown'
            school=next((text for text,_ in reversed(edu[:degree_index]) if not re.match(r'^(Degree|Qualification)\s*[:：]?$',text,re.I)), '')
            if '|' in degree_line:
                parts=[part.strip() for part in degree_line.split('|')]
                school=parts[0];degree_line=parts[1];major=parts[1]
            profile['education'].append({'school':school,'major':major,'degree':re.split(r'\s{2,}',re.sub(r'^Degree\s*[:：]\s*','',degree_line,flags=re.I))[0],'level':level,'status':status,'start':years[0] if years else None,'end':years[-1] if len(years)>1 else None,'evidence':refs(edu)})
    for kind in ('internship','employment','project'):
        items=sections[kind]
        # Pipe-separated organization | role | dates is another explicit conventional layout.
        pipe_markers=[i for i,(text,_) in enumerate(items) if text.count('|')>=2 and re.search(r'\b20\d{2}\b',text)]
        if pipe_markers:
            for n,index in enumerate(pipe_markers):
                chunk=items[index:pipe_markers[n+1] if n+1<len(pipe_markers) else len(items)]
                parts=[part.strip() for part in items[index][0].split('|')]
                actual_kind='internship' if re.search(r'\bintern\b|trainee',parts[1],re.I) else 'project' if kind=='project' or re.search(r'project|co.?founder',parts[0]+' '+parts[1],re.I) else kind
                years=re.findall(r'\b20\d{2}\b',parts[-1])
                profile['experiences'].append({'id':f'experience-{len(profile["experiences"])+1}','organization':parts[0],'role':parts[1],'kind':actual_kind,'start':years[0] if years else None,'end':years[-1] if years else None,'summary':' '.join(t for t,_ in chunk[1:])[:600],'companyGroup':None,'evidence':refs(chunk)})
            continue
        # Each conventional role/date line begins a record after its organization.
        markers=[i for i,(text,_) in enumerate(items) if re.search(r'\b(?:19|20)\d{2}\b',text) and re.search(r'\s[–—-]\s',text) and not re.match(r'^[-•]',text)]
        for n,index in enumerate(markers):
            start=max(0,index-1)
            end=max(start+1,markers[n+1]-1) if n+1<len(markers) else len(items)
            chunk=items[start:end]
            role=items[index][0]
            years=re.findall(r'\b(?:19|20)\d{2}\b',role)
            organization=items[start][0] if start<index else ''
            if not organization: continue
            # Generic experience headings do not turn a founder/project into internship.
            actual_kind=kind
            if kind=='employment' and re.search(r'\bintern\b|trainee',role,re.I): actual_kind='internship'
            if re.search(r'co.?founder|hackathon|course project',role+' '+organization,re.I): actual_kind='project'
            profile['experiences'].append({'id':f'experience-{len(profile["experiences"])+1}','organization':organization,'role':re.split(r'\s{2,}',role)[0],'kind':actual_kind,'start':years[0] if years else None,'end':years[-1] if len(years)>1 else None,'summary':' '.join(t for t,_ in chunk[2:])[:600],'companyGroup':None,'evidence':refs(chunk)})
    return profile

class FixtureProvider:
    """Explicit offline adapter: literal evidence retrieval, never scenario/name rankings.

    It intentionally does not claim semantic assessment or extract complex CV histories.
    Live acceptance must exercise the real provider independently.
    """
    mode, configured, model = 'fixture', True, 'deterministic-fixture-v1'
    legacy_evidence_fixture = True  # Explicit offline adapter, never a live-output fallback.

    def generate(self, role, payload, schema):
        if role == 'jd':
            lines = [s.strip() for s in payload['jdText'].splitlines() if len(s.strip()) > 30 and '@' not in s]
            result = {'criteria':[{'id':f'criterion-{i+1}','title':s[:72], 'description':s,
                'group':'bonus' if re.search(r'prefer|advantage|加分|优先',s,re.I) else 'must',
                'priority':i,'jdQuote':s} for i,s in enumerate(lines)]}
        elif role == 'cv':
            result = fixture_profile(payload['document']['blocks'])
        elif role == 'match':
            observations, matches, concerns = [], [], []
            for c in payload['standards']['criteria']:
                # Literal overlap is only contextual evidence; never proves a whole requirement.
                tokens = set(re.findall(r'\b[A-Za-z][A-Za-z0-9+-]{2,}\b', (c['title']+' '+c['description']).lower()))
                tokens -= {'the','and','with','for','from','that','this','our','you','will','are','have','work','your','into','their','through','across','where','can','team','teams','strong','current','multiple','more','than','such','existing'}
                concepts = {'data':{'analysis','gps','milp','dashboards','optimization'},'llm':{'rag','retrieval','workflow','python'},'agents':{'rag','retrieval','workflow','automation'},'adoption':{'pilot','users','feedback','training'},'performance':{'tested','test','retrieval','failure','quality'}}
                for concept,terms in concepts.items():
                    if concept in tokens: tokens |= terms
                refs = [{'blockId':b['id'],'quote':b['text'],'explanation':'字面相关原文，能力范围仍需确认'}
                        for b in payload['document']['blocks'] if any(re.search(r'\b'+re.escape(t)+r'\b',b['text'],re.I) for t in tokens)]
                explanation = '本地测试模式：发现相关原文，尚未进行语义能力验证。' if refs else '界面测试模式尚未完成这一项的语义评估，尚未关联原文。'
                observations.append({'criterionId':c['id'],'coverage':'partial' if refs else 'unknown','explanation':explanation,'evidence':refs})
                point = {'id':'point-'+c['id'], 'title':c['title'],'criterionIds':[c['id']], 'jdRequirement':c['description'], 'explanation':explanation,'evidence':refs,'kind':'verify' if refs else 'unknown'}
                concerns.append(point)
            # Display only concrete implementation passages as partial support points.
            # This is generic retrieval, not a simulated semantic model or preassigned score.
            capabilities=[('LLM Workflow',r'LLM|LangGraph|language model|agent workflow',r'LLM|agent|framework|workflow'),
                          ('RAG',r'RAG|retrieval|vector search',r'LLM|agent|RAG|framework|workflow'),
                          ('Python',r'Python',r'build|develop|agent|data|workflow'),
                          ('数据分析',r'GPS|MILP|dashboard|data analy|optimization|energy-saving',r'data|insight|performance|workflow')]
            action=r'\b(built|developed|designed|analyzed|implemented|created|automated|added|tested|evaluated|compared|used|proposed|formulated|researched|classified)\b'
            for title,source_pattern,jd_pattern in capabilities:
                source_refs=[{'blockId':b['id'],'quote':b['text'],'explanation':'本地规则提取的具体技术活动；仅支持所引述范围。'} for b in payload['document']['blocks'] if re.search(source_pattern,b['text'],re.I) and re.search(action,b['text'],re.I)]
                related=[c for c in payload['standards']['criteria'] if re.search(jd_pattern,c['title']+' '+c['description'],re.I)]
                related=[c for c in related if next(o for o in observations if o['criterionId']==c['id'])['coverage']=='partial']
                if source_refs and related:
                    matches.append({'id':'capability-'+str(len(matches)+1),'title':title,'criterionIds':[c['id'] for c in related],'jdRequirement':'；'.join(c['title'] for c in related),'explanation':'岗位要求相关技术交付；简历有以下具体活动记录，提供部分支持。离线基线未验证其质量、HR场景迁移或完整要求，需面试确认。','evidence':source_refs,'kind':'match'})
            result = {'observations':observations,'matches':matches,'concerns':concerns[:4],'summary':'本地测试模式：已定位具体技术活动与相关证据；完整能力判断待确认。','keyUnknown':'实际能力与成果范围需要语义评估和面试确认。'}
        elif role == 'review':
            result = {'approved':not payload.get('validationErrors'), 'issues':payload.get('validationErrors',[])}
        elif role == 'interview':
            questions=[]
            for i,p in enumerate(payload['questionPlan']):
                prefix=(p['experience']['organization']+'：') if p['experience'] else ''
                questions.append({'id':f'question-{i+1}','question':prefix+['你亲自完成了哪个部分？','你怎样验证结果？','你遇到了什么限制？'][i],
                                  'purpose':p['primaryGoal'],'pointIds':[p['pointId']],'criterionIds':[p['criterionId']],'evidence':p['allowedEvidence']})
            result={'questions':questions}
        elif role == 'standards_chat':
            result={'reply':'本地测试模式：请在标准编辑器手动调整要求并确认；测试适配器不会猜测新标准。','draft':None}
        elif role == 'chat':
            result={'text':'本地测试模式：请通过相关原文核验结论；修改全局要求请打开岗位标准并确认新版本。候选人纠正请使用纠正功能，保存后重新审核。','evidence':[]}
        else:
            raise ProviderError('unsupported_role','Unsupported workflow role.')
        if role=='match':
            from .evidence import point_capabilities
            result['pointGrounding']=[{'pointId':p['id'],'focus':'capability' if point_capabilities(p) else 'other',
                'capabilities':point_capabilities(p),'scopeReason':'Explicit fixture lexical scope; not semantic inference',
                'assessment':p.pop('explanation'),
                'claims':[{'claim':'简历记录了相关技术活动','evidence':[p['evidence'][0]]}]} for p in result['matches']]
        if role=='match':
            # Explicit offline adapter only; live generation must supply its own reviewed plan.
            from .evidence import _fixture_interview_plan
            plans=_fixture_interview_plan(result,payload['standards'],payload.get('profile',{'experiences':[]}))
            goals=['核实个人贡献','核实结果验证方法','核实活动边界']
            result['verificationTargets']=[{'id':f't{i}','pointId':p['pointId'],'criterionId':p['criterionId'],
                'singleGoal':goals[i],'decisionReason':'Explicit fixture target; not semantic selection',
                'experienceId':p['experience']['id'] if p['experience'] else None,'evidence':p['allowedEvidence'] if p['experience'] else []} for i,p in enumerate(plans)]
        return schema.model_validate(result).model_dump()

def create_provider(mode=None):
    mode = mode or os.getenv('HR_MODEL_MODE','live')
    if mode == 'fixture':
        return FixtureProvider()
    if mode != 'live':
        raise ProviderError('invalid_mode','HR_MODEL_MODE must be live or fixture.')
    provider=os.getenv('HR_MODEL_PROVIDER','openai')
    if provider=='deepseek':
        from .deepseek import DeepSeekProvider
        return DeepSeekProvider()
    if provider!='openai':raise ProviderError('invalid_provider','HR_MODEL_PROVIDER must be openai or deepseek.')
    return OpenAIProvider()
