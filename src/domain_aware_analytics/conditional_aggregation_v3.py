"""Bounded AST semantics for declared aggregate populations, no SQL/I/O/recipes.

Declarations are expectations, not evidence of question understanding. A proof
binds SQL to them; independent business review is still mandatory. Missing
declarations or trustworthy join keys cannot be replaced by guessing from SQL.
"""
from datetime import date,datetime
from collections import Counter
from decimal import Decimal
import re
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlglot import exp
from .structured_temporal_v2 import _literal as _calendar_literal, _Unsupported as _CalendarUnsupported

_REF=r'^[A-Za-z_][\w]*\.[A-Za-z_][\w]*$'
_NAME=r'^[A-Za-z_][\w]*$'


class _Model(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,strict=True,allow_inf_nan=False)


class ScalarPredicate(_Model):
    column:str=Field(pattern=_REF)
    operator:Literal['eq','ne','gt','ge','lt','le','is_null','not_null']
    value:str|int|float|bool|None

    @model_validator(mode='after')
    def explicit_null_semantics(self):
        if (self.operator in {'is_null','not_null'}) != (self.value is None):
            raise ValueError('NULL tests require null; ordinary comparisons require a non-null literal')
        return self


class AggregateTerm(_Model):
    function:Literal['count','sum','avg']
    argument:str=Field(pattern=r'^(?:\*|1|[A-Za-z_][\w]*\.[A-Za-z_][\w]*)$')
    distinct:bool=False
    condition:list[ScalarPredicate]=Field(default_factory=list)
    else_value:Literal['null','zero']='null'
    coalesce_zero:bool=False


class AggregateOutput(_Model):
    name:str=Field(pattern=_NAME)
    numerator:AggregateTerm
    denominator:AggregateTerm|None=None
    scale:Literal[1,100]=1
    round_digits:int|None=Field(default=None,ge=0,le=12)


class JoinColumns(_Model):
    left:str=Field(pattern=_REF)
    right:str=Field(pattern=_REF)


class AggregatePopulationContract(_Model):
    row_source:str=Field(pattern=_NAME)
    entity_grain:Literal['physical_row','distinct_entity']='physical_row'
    entity_key:str|None=Field(default=None,pattern=_REF)
    base_filters:list[ScalarPredicate]=Field(default_factory=list)
    group_by:list[str]=Field(default_factory=list)
    joins:list[JoinColumns]=Field(default_factory=list)
    denominator_population:Literal['base_rows','matched_inner_rows']='base_rows'
    outputs:list[AggregateOutput]=Field(min_length=1,max_length=20)

    @model_validator(mode='after')
    def unique_names_and_grain(self):
        if len({o.name for o in self.outputs})!=len(self.outputs):raise ValueError('Output names must be unique')
        if self.entity_grain=='distinct_entity' and self.entity_key is None:raise ValueError('Distinct entity grain requires an explicit physical key')
        if any(re.fullmatch(_REF,c) is None for c in self.group_by):raise ValueError('Grouping requires direct physical references')
        return self


class PopulationProofError(Exception):
    def __init__(self,status,code,message):self.status,self.code,self.message=status,code,message


def _fail(code,message,status='unsupported'):raise PopulationProofError(status,code,message)


def _unparen(n):
    while isinstance(n,exp.Paren):n=n.this
    return n


def _ands(n):
    n=_unparen(n)
    if isinstance(n,exp.And):return _ands(n.this)+_ands(n.expression)
    return [] if n is None else [n]


def _dtype(ref,schema):
    table,column=ref.split('.')
    value=re.sub(r'\([^)]*\)','',str(schema.get(table,{}).get(column,''))).strip().lower()
    if value in {'smallint','integer','bigint','int','int2','int4','int8'}:return 'integer'
    if value in {'numeric','decimal'}:return 'decimal'
    if value in {'real','double precision'}:return 'float'
    if value in {'text','varchar','character varying','character','char'}:return 'string'
    if value in {'boolean','bool'}:return 'boolean'
    if value in {'date','timestamp without time zone'}:return value
    _fail('CONDITIONAL_TYPE_UNPROVEN','Aggregate population reasoning requires known builtin scalar types.')


def _ref(node,aliases,schema):
    node=_unparen(node)
    if not isinstance(node,exp.Column) or node.table not in aliases:_fail('CONDITIONAL_COLUMN_LINEAGE_UNPROVEN','Only direct unambiguous physical columns are supported.')
    ref=aliases[node.table]+'.'+node.name
    _dtype(ref,schema)
    return ref


def _literal(n):
    n=_unparen(n)
    if isinstance(n,exp.Null):return None
    if isinstance(n,exp.Boolean):return bool(n.this)
    if isinstance(n,exp.Literal):return n.this if n.is_string else Decimal(n.this)
    if isinstance(n,exp.Neg) and isinstance(n.this,exp.Literal) and not n.this.is_string:return -Decimal(n.this.this)
    _fail('CONDITIONAL_PREDICATE_UNSUPPORTED','Only typed scalar literal comparisons and NULL tests are supported.')


def _zero(n):
    value=_literal(n)
    # Python bool is numerically equal to0/1; PostgreSQL boolean is NOT numeric.
    return isinstance(value,Decimal) and value==0


def _value(value,ref,schema):
    kind=_dtype(ref,schema)
    if value is None:_fail('CONDITIONAL_NULL_COMPARISON_UNPROVEN','Use explicit IS NULL/IS NOT NULL, not equality to NULL.')
    if kind in {'integer','decimal','float'}:
        if isinstance(value,bool) or not isinstance(value,(int,float,Decimal)):_fail('CONDITIONAL_TYPE_MISMATCH','Numeric comparisons need numeric constants.','violated')
        d=Decimal(str(value))
        if not d.is_finite():_fail('CONDITIONAL_LITERAL_UNPROVEN','Nonfinite numeric literals are not proved.')
        # Decimal.normalize() uses the ambient precision (usually28) and can
        # falsely equate different long PostgreSQL numeric constants. Canonical
        # tuples remove only exact trailing zeros; never round the value.
        if not d:return ('number',(0,(0,),0))
        sign,digits,exponent=d.as_tuple();digits=list(digits)
        while digits[-1]==0:digits.pop();exponent+=1
        return ('number',(sign,tuple(digits),exponent))
    if kind=='boolean':
        if not isinstance(value,bool):_fail('CONDITIONAL_TYPE_MISMATCH','Boolean comparisons need boolean constants.','violated')
        return ('boolean',value)
    if not isinstance(value,str):_fail('CONDITIONAL_TYPE_MISMATCH','Text/calendar comparisons need explicit string constants.','violated')
    if kind=='date':
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value) is None:_fail('CONDITIONAL_CALENDAR_LITERAL_UNPROVEN','Calendar literals require unambiguous ISO dates.')
        value=date.fromisoformat(value).isoformat()
    if kind=='timestamp without time zone':
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)?',value) is None:
            _fail('CONDITIONAL_CALENDAR_LITERAL_UNPROVEN','Naive ISO timestamps require at most6 fractional digits; timezone/precision coercions are not proved.')
        d=datetime.fromisoformat(value)
        if d.tzinfo is not None:_fail('CONDITIONAL_TIMEZONE_UNPROVEN','Timezone conversions are outside conditional predicate proofs.')
        value=d.isoformat()
    return (kind,value)


_OPS={exp.EQ:'eq',exp.NEQ:'ne',exp.GT:'gt',exp.GTE:'ge',exp.LT:'lt',exp.LTE:'le'}
_REVERSE={'eq':'eq','ne':'ne','gt':'lt','ge':'le','lt':'gt','le':'ge'}


def _predicate_value(node,ref,schema):
    """Normalize only proven calendar literal casts, never arbitrary coercions.

    PostgreSQL TIMESTAMP '...' and ::timestamp are CAST AST nodes. Reuse the
    temporal proof's ISO/precision/timezone policy; do not strip a cast from a
    column, nested expression or numeric value. NULL comparisons stay explicit.
    """
    node=_unparen(node)
    if type(node) is exp.Cast and _dtype(ref,schema) in {'date','timestamp without time zone'}:
        calendar_date=_dtype(ref,schema)=='date'
        try:
            boundary=_calendar_literal(node,calendar_date=calendar_date)
        except _CalendarUnsupported:
            _fail('CONDITIONAL_CALENDAR_LITERAL_UNPROVEN','Only direct ISO DATE/naive TIMESTAMP literals without timezone, precision or nested coercions are proved.')
        value=boundary.date().isoformat() if calendar_date else boundary.isoformat()
        return _value(value,ref,schema)
    return _value(_literal(node),ref,schema)


def _predicates(node,aliases,schema):
    result=[]
    for p in _ands(node):
        p=_unparen(p);neg=isinstance(p,exp.Not);inner=_unparen(p.this) if neg else p
        if isinstance(inner,exp.Is) and isinstance(inner.expression,exp.Null):
            ref=_ref(inner.this,aliases,schema);op='not_null' if neg!=bool(inner.args.get('negate')) else 'is_null';result.append((ref,op,None));continue
        if neg or type(p) not in _OPS:_fail('CONDITIONAL_PREDICATE_UNSUPPORTED','Conditional predicates support conjunctions of direct comparisons and NULL tests only.')
        left,right=p.this,p.expression;op=_OPS[type(p)]
        if not isinstance(_unparen(left),exp.Column):left,right,op=right,left,_REVERSE[op]
        ref=_ref(left,aliases,schema);result.append((ref,op,_predicate_value(right,ref,schema)))
    return frozenset(result)


def _expected_predicates(predicates,schema):
    return frozenset((p.column,p.operator,None if p.operator in {'is_null','not_null'} else _value(p.value,p.column,schema)) for p in predicates)


def _numeric_cast(node):
    node=_unparen(node);cast=False
    if isinstance(node,exp.Cast):
        dtype=node.args['to']
        if dtype.this.value not in {'DECIMAL','NUMERIC'} or dtype.expressions:_fail('CONDITIONAL_COERCION_UNPROVEN','Only an unconstrained numeric aggregate cast is proved; narrowing/float casts need explicit reasoning.')
        node=_unparen(node.this);cast=True
    return node,cast


def _scale(node):
    value=_literal(node)
    if not isinstance(value,Decimal) or value not in {Decimal(1),Decimal(100)}:
        _fail('CONDITIONAL_SCALE_UNSUPPORTED','Each scale literal must be exactly1 or100; arbitrary arithmetic is not normalized.')
    return value


def _aggregate(node,aliases,schema,consumed):
    node,cast=_numeric_cast(node);coalesce=False
    if isinstance(node,exp.Coalesce):
        if len(node.expressions)!=1 or not _zero(node.expressions[0]):_fail('CONDITIONAL_COALESCE_UNSUPPORTED','Only an explicit aggregate COALESCE(...,0) is supported.')
        node=node.this;coalesce=True
    condition=frozenset();row_else='null'
    if isinstance(node,exp.Filter):
        consumed.add(id(node));condition=_predicates(node.expression.this,aliases,schema);node=node.this
    if not isinstance(node,(exp.Count,exp.Sum,exp.Avg)):_fail('CONDITIONAL_AGGREGATE_UNSUPPORTED','Only direct COUNT/SUM/AVG are proved, not nested/window/other aggregates.')
    if node.expressions:_fail('CONDITIONAL_ARGUMENT_UNSUPPORTED','Multiple aggregate arguments are not supported.')
    function=node.sql_name().lower();arg=node.this;distinct=False
    if isinstance(arg,exp.Distinct):
        if len(arg.expressions)!=1:_fail('CONDITIONAL_DISTINCT_UNSUPPORTED','Only one direct DISTINCT entity column is supported.')
        arg=arg.expressions[0];distinct=True
    if isinstance(arg,exp.Case):
        if condition or arg.this is not None or len(arg.args.get('ifs',[]))!=1:_fail('CONDITIONAL_CASE_UNSUPPORTED','Only one searched CASE branch inside an aggregate is supported.')
        consumed.add(id(arg));branch=arg.args['ifs'][0];consumed.add(id(branch));condition=_predicates(branch.this,aliases,schema)
        default=arg.args.get('default')
        if default is not None and not isinstance(default,exp.Null):
            if not _zero(default):_fail('CONDITIONAL_CASE_UNSUPPORTED','CASE fallback must be NULL or numeric zero.')
            row_else='zero'
        arg=branch.args['true']
    arg=_unparen(arg)
    if isinstance(arg,exp.Column):argument=_ref(arg,aliases,schema);dtype=_dtype(argument,schema)
    elif isinstance(arg,exp.Star) and function=='count':argument='*';dtype='integer'
    elif isinstance(arg,exp.Literal) and not arg.is_string and Decimal(arg.this)==1:argument='*' if function=='count' else '1';dtype='integer'
    else:_fail('CONDITIONAL_ARGUMENT_UNSUPPORTED','Aggregate inputs must be a direct column,COUNT(*) or literal1; no nested aggregate or expression is inferred.')
    if function in {'sum','avg'} and dtype not in {'integer','decimal','float'}:_fail('CONDITIONAL_TYPE_MISMATCH','SUM/AVG need numeric physical inputs.','violated')
    if distinct and (function!='count' or argument=='*'):_fail('CONDITIONAL_DISTINCT_UNSUPPORTED','Only COUNT(DISTINCT physical_column) is supported.')
    if row_else=='zero' and function!='sum':_fail('CONDITIONAL_NULL_SEMANTICS_UNSUPPORTED','ELSE0 is not interchangeable with a filtered COUNT or AVG; this form remains unproved.')
    if function=='count':coalesce=False
    signature=(function,argument,distinct,condition,row_else,coalesce)
    result_type='decimal' if cast or function=='avg' and dtype!='float' or function=='sum' and dtype=='decimal' else 'float' if dtype=='float' else 'integer'
    return signature,result_type


def _expected_term(t,schema):
    arg='*' if t.function=='count' and t.argument=='1' else t.argument
    if arg not in {'*','1'}:_dtype(arg,schema)
    return (t.function,arg,t.distinct,_expected_predicates(t.condition,schema),t.else_value,False if t.function=='count' else t.coalesce_zero)


def _metric(node,aliases,schema,consumed):
    node=_unparen(node);digits=None;scale=Decimal(1)
    if isinstance(node,exp.Round):
        d=_literal(node.args.get('decimals'))
        if not isinstance(d,Decimal) or d!=int(d) or not 0<=d<=12:_fail('CONDITIONAL_ROUND_UNSUPPORTED','ROUND requires explicit integer decimal precision0..12.')
        digits=int(d);node=_unparen(node.this)
    # Only these algebraic forms are proved; preserve division BEFORE a trailing
    # multiplier, because integer division cannot be repaired by multiplying later.
    if isinstance(node,exp.Mul):
        if isinstance(_unparen(node.this),exp.Literal):scale=_scale(node.this);node=_unparen(node.expression)
        elif isinstance(_unparen(node.expression),exp.Literal):scale=_scale(node.expression);node=_unparen(node.this)
        else:_fail('CONDITIONAL_EXPRESSION_UNSUPPORTED','Only a literal ratio scale is supported.')
    if not isinstance(node,exp.Div):
        if scale!=1:_fail('CONDITIONAL_EXPRESSION_UNSUPPORTED','Scaled standalone aggregates are not supported.')
        signature,_=_aggregate(node,aliases,schema,consumed);return signature,None,1,digits
    numerator=_unparen(node.this);denominator=_unparen(node.expression);promoted=False
    if isinstance(numerator,exp.Mul):
        if isinstance(_unparen(numerator.this),exp.Literal):literal=numerator.this;numerator=numerator.expression
        elif isinstance(_unparen(numerator.expression),exp.Literal):literal=numerator.expression;numerator=numerator.this
        else:_fail('CONDITIONAL_EXPRESSION_UNSUPPORTED','Only literal-times-aggregate numerators are supported.')
        scale*= _scale(literal);promoted='.' in literal.this or 'e' in literal.this.lower()
    if not isinstance(denominator,exp.Nullif) or not _zero(denominator.expression):_fail('CONDITIONAL_ZERO_DIVISION_UNPROVEN','Ratios require explicit NULLIF(denominator,0).')
    num,nt=_aggregate(numerator,aliases,schema,consumed);den,dt=_aggregate(denominator.this,aliases,schema,consumed)
    if nt=='integer' and dt=='integer' and not promoted:_fail('CONDITIONAL_INTEGER_DIVISION','Integer division cannot prove a decimal ratio.','violated')
    if nt=='float' or dt=='float':_fail('CONDITIONAL_FLOAT_RATIO_UNPROVEN','Approximate float-ratio coercions are outside this exact-decimal subset.')
    if scale not in {1,100}:_fail('CONDITIONAL_SCALE_UNSUPPORTED','Only unscaled proportions and explicit percentage scale100 are supported.')
    return num,den,int(scale),digits


def _join_proof(select,aliases,schema,c,unique_keys):
    pairs=[]
    for join in select.args.get('joins',[]):
        for p in _ands(join.args.get('on')):
            if not isinstance(p,exp.EQ) or not isinstance(p.this,exp.Column) or not isinstance(p.expression,exp.Column):_fail('CONDITIONAL_JOIN_UNPROVEN','Only explicit physical key equalities are supported in conditional INNER JOINs.')
            left,right=_ref(p.this,aliases,schema),_ref(p.expression,aliases,schema)
            if _dtype(left,schema)!=_dtype(right,schema):_fail('CONDITIONAL_JOIN_UNPROVEN','Join key coercion is not proven.')
            if _dtype(left,schema) not in {'integer','decimal','boolean'}:_fail('CONDITIONAL_JOIN_KEY_SEMANTICS_UNPROVEN','Text/collation and approximate key equality need metadata beyond scalar type names.')
            pairs.append(tuple(sorted((left,right))))
    wanted=[tuple(sorted((j.left,j.right))) for j in c.joins]
    if frozenset(pairs)!=frozenset(wanted):_fail('CONDITIONAL_JOIN_POPULATION_MISMATCH','Actual joins must match the declared population exactly.','violated')
    if not pairs:return {'status':'no_join','row_grain':'physical_row'}
    if c.denominator_population!='matched_inner_rows':_fail('CONDITIONAL_JOIN_COVERAGE_UNPROVEN','INNER JOIN may drop unmatched entities; base_rows requires a coverage/FK proof not implemented here.')
    reached={c.row_source};tables=set(aliases.values());proof=[]
    while reached!=tables:
        progress=False
        for target in sorted(tables-reached):
            keys=set()
            for a,b in pairs:
                ta,ca=a.split('.');tb,cb=b.split('.')
                if ta==target and tb in reached:keys.add(ca)
                if tb==target and ta in reached:keys.add(cb)
            if any(set(k)<=keys and k for k in (unique_keys or {}).get(target,[])):
                proof.append({'relation':target,'matched_unique_columns':sorted(keys)});reached.add(target);progress=True
        if not progress:_fail('CONDITIONAL_JOIN_CARDINALITY_UNPROVEN','A joined table lacks trusted nondeferrable unique-key evidence; entity multiplication cannot be excluded.')
    return {'status':'at_most_one_match_per_declared_input_row','population':'matched_inner_rows_not_all_unmatched_base_entities','unique_key_basis':'trusted_database_constraint_metadata_not_LLM_relationship_names','steps':proof}


def prove_conditional_population(scopes,schema,c,unique_keys=None):
    if not isinstance(c,AggregatePopulationContract):_fail('CONDITIONAL_POPULATION_UNSUPPORTED','FILTER/CASE require an explicit typed aggregate population contract, not a contract inferred from candidate SQL.')
    if len(scopes)!=1:_fail('CONDITIONAL_SCOPE_UNSUPPORTED','Conditional proofs currently require one physical aggregate SELECT; derived aggregate populations and multi-branch lineage remain unsupported.')
    scope=scopes[0];select=scope.expression
    if any(not isinstance(source,exp.Table) for _,source in scope.selected_sources.values()):_fail('CONDITIONAL_SCOPE_UNSUPPORTED','A direct physical population is required.')
    aliases={alias:source.name for alias,(_,source) in scope.selected_sources.items()}
    if c.row_source not in aliases.values():_fail('CONDITIONAL_ROW_SOURCE_MISMATCH','Declared input row source is absent.','violated')
    if select.args.get('having') or select.args.get('distinct') or any(select.find_all(exp.Window)):_fail('CONDITIONAL_AGGREGATION_STAGE_UNSUPPORTED','HAVING,window aggregates and SELECT DISTINCT need separate post-aggregation population proofs.')
    actual=_predicates(select.args['where'].this if select.args.get('where') else None,aliases,schema)
    if actual!=_expected_predicates(c.base_filters,schema):_fail('CONDITIONAL_BASE_POPULATION_MISMATCH','WHERE must equal the declared base population; numerator filters cannot silently narrow the denominator.','violated')
    groups=select.args.get('group')
    if groups and any(v for k,v in groups.args.items() if k!='expressions'):_fail('CONDITIONAL_GROUPING_UNSUPPORTED','Grouping sets/ROLLUP/CUBE are not proved.')
    actual_group=[_ref(n,aliases,schema) for n in groups.expressions] if groups else []
    if set(actual_group)!=set(c.group_by):_fail('CONDITIONAL_GROUP_GRAIN_MISMATCH','GROUP BY must match the declared output grain.','violated')
    if any(n>1 for n in Counter(p.alias_or_name for p in select.expressions).values()):
        _fail('CONDITIONAL_OUTPUT_ALIAS_AMBIGUOUS','Duplicate group/metric output aliases are not proved.')
    expected={o.name:o for o in c.outputs};seen=set();outputs=[];consumed=set();projected_groups=set()
    for projection in select.expressions:
        node=projection.this if isinstance(projection,exp.Alias) else projection
        if isinstance(node,exp.Column) and _ref(node,aliases,schema) in actual_group:
            projected_groups.add(_ref(node,aliases,schema));continue
        name=projection.alias_or_name
        if name in seen or name not in expected:_fail('CONDITIONAL_OUTPUT_MISMATCH','Every aggregate output requires one unambiguous declaration.','violated')
        seen.add(name);spec=expected[name];num,den,scale,digits=_metric(node,aliases,schema,consumed)
        expected_num=_expected_term(spec.numerator,schema);expected_den=_expected_term(spec.denominator,schema) if spec.denominator else None
        if (num,den,scale,digits)!=(expected_num,expected_den,spec.scale,spec.round_digits):_fail('CONDITIONAL_METRIC_MISMATCH','Aggregate inputs,predicates,DISTINCT,NULL/empty policies,denominator,scale or precision differ from the declared metric.','violated')
        if den and (num[:3]!=den[:3] or num[0] not in {'count','sum'} or not den[3].issubset(num[3])):
            _fail('CONDITIONAL_RATIO_POPULATION_UNPROVEN','A share requires the same count/measure and DISTINCT grain, with a numerator subset of its denominator; mixed-grain/conditional-mean ratios need another proof.')
        if c.entity_grain=='distinct_entity':
            for signature in [num]+([den] if den else []):
                if signature[0]!='count' or signature[1]!=c.entity_key or not signature[2]:_fail('CONDITIONAL_ENTITY_GRAIN_UNPROVEN','Distinct entity grain requires COUNT(DISTINCT declared_entity_key); SUM/AVG or row counts need further entity proofs.')
        def describe(t):return {'function':t[0],'argument':t[1],'distinct':t[2],'condition_atoms':[list(a) for a in sorted(t[3],key=repr)],'false_rows':'zero_contribution' if t[4]=='zero' else 'excluded_or_null','null_inputs':'excluded','empty_result':'zero' if t[0]=='count' or t[5] else 'null','coalesce_zero':t[5]}
        outputs.append({'name':name,'numerator':describe(num),'denominator':describe(den) if den else None,'denominator_scope':'same_declared_base_and_group' if den else None,'zero_division':'nullif_zero' if den else 'not_applicable','scale':scale,'round_digits':digits})
    if seen!=set(expected):_fail('CONDITIONAL_OUTPUT_MISMATCH','A required aggregate output is missing.','violated')
    if projected_groups!=set(actual_group):_fail('CONDITIONAL_GROUP_PROJECTION_MISSING','Declared grouping dimensions must be present in the output.','violated')
    if any(id(n) not in consumed for n in select.find_all(exp.Case,exp.If,exp.Filter)):_fail('CONDITIONAL_CASE_CONTEXT_UNSUPPORTED','CASE/FILTER outside the proved aggregate inputs is unsupported.')
    join=_join_proof(select,aliases,schema,c,unique_keys)
    return {'status':'satisfied','scope':'single_physical_population_aggregate','base_population':{'row_source':c.row_source,'where_atoms':[list(a) for a in sorted(actual,key=repr)],'denominator_population':c.denominator_population},'entity_grain':c.entity_grain,'entity_key':c.entity_key,'grouping_grain':actual_group,'operation_order':['FROM_JOIN','WHERE_base_population','GROUP_BY','FILTER_or_CASE_per_aggregate','aggregate','ORDER_BY','final_LIMIT'],'outputs':outputs,'join_cardinality':join,'declaration_source':'typed_plan_expectation_not_derived_from_candidate_SQL','question_semantics':'unverified_requires_independent_review','sql_rewritten':False}
