"""DeepSeek strict-schema compatibility without changing Pydantic validation.

The API requires an explicit type on each anyOf branch, including object refs.
Only inline those branches; ordinary refs and the semantic schema stay unchanged.
"""
from copy import deepcopy
from functools import lru_cache


def explicit_union_types(schema):
    result=deepcopy(schema);definitions=result.get('$defs',{})
    def walk(node,depth=0):
        if depth>32:raise ValueError('Structured schema nesting exceeds the supported bound')
        if isinstance(node,list):return [walk(value,depth+1) for value in node]
        if not isinstance(node,dict):return node
        value=deepcopy(node)
        if 'anyOf' in value:
            branches=[]
            for branch in value['anyOf']:
                if isinstance(branch,dict) and '$ref' in branch:
                    reference=branch['$ref'];prefix='#/$defs/'
                    if not reference.startswith(prefix) or reference[len(prefix):] not in definitions:
                        raise ValueError('Unsupported structured schema reference')
                    branch={**deepcopy(definitions[reference[len(prefix):]]),**{k:v for k,v in branch.items() if k!='$ref'}}
                branches.append(walk(branch,depth+1))
            value['anyOf']=branches
        return {key:(child if key=='anyOf' else walk(child,depth+1)) for key,child in value.items()}
    return walk(result)


@lru_cache(maxsize=32)
def response_schema(model):
    original=model.model_json_schema();compatible=explicit_union_types(original)
    if compatible==original:return model
    def model_json_schema(cls,*args,**kwargs):
        return explicit_union_types(model.model_json_schema(*args,**kwargs))
    return type(model.__name__,(model,),{'__module__':model.__module__,'model_json_schema':classmethod(model_json_schema)})
