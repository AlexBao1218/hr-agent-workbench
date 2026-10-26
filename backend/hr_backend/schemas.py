"""Strict role outputs. Public field names mirror contracts/api.ts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class EvidenceRef(Strict):
    blockId: str
    quote: str
    @model_validator(mode='before')
    @classmethod
    def discard_legacy_annotation(cls, value):
        # Input compatibility for cached refs/fixtures; role schema never asks for prose here.
        if isinstance(value,dict):return {k:v for k,v in value.items() if k!='explanation'}
        return value

class Criterion(Strict):
    id: str
    title: str
    description: str
    group: Literal['must', 'bonus']
    priority: int = Field(ge=0)
    jdQuote: str

class JDResult(Strict):
    criteria: list[Criterion]

class Education(Strict):
    school: str
    major: str
    degree: str
    level: Literal['bachelor','master','doctorate','other']
    status: Literal['studying','graduated','unknown']
    start: str | None
    end: str | None
    evidence: list[EvidenceRef]

class Experience(Strict):
    id: str
    organization: str
    role: str
    kind: Literal['internship','employment','project']
    start: str | None
    end: str | None
    summary: str
    companyGroup: str | None
    evidence: list[EvidenceRef]

class CandidateProfile(Strict):
    name: str = Field(description="Exact source name only. Never invent or transliterate a name or alias absent from the document.")
    education: list[Education]
    experiences: list[Experience]
    languages: list[str]
    availability: str | None

class Observation(Strict):
    criterionId: str
    coverage: Literal['supported','partial','unknown','contradiction'] = Field(description='Contradiction requires an explicit source-backed conflict with this confirmed criterion. Missing mention or unrelated-domain experience is unknown/partial, never contradiction.')
    explanation: str = Field(description="Name the correct employer for each activity when combining experiences. Every asserted activity must have its own supporting quote in this observation evidence, not just elsewhere in the assessment.")
    evidence: list[EvidenceRef]

class PointFields(Strict):
    id: str
    title: str
    criterionIds: list[str]
    jdRequirement: str = Field(description='Accurate concise paraphrase of ONLY the linked confirmed criteria, not an unlinked sentence elsewhere in the JD or a new date/language requirement.')
    evidence: list[EvidenceRef]
    kind: Literal['match','unknown','gap','verify'] = Field(description='gap only for explicit contradictions: every linked criterion must have contradiction coverage with source evidence also cited here. Missing/partial/uncertain evidence uses unknown or verify and a 待确认 title; silence is not a gap.')

class MatchPoint(PointFields):
    criterionIds: list[str] = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(min_length=1)
    kind: Literal['match']

class AnalysisPoint(PointFields):
    criterionIds: list[str] = Field(min_length=1)
    explanation: str

class PointFact(Strict):
    claim: str = Field(min_length=1, description='Concise candidate fact rendered directly into the explanation. This is the SINGLE wording of that fact, not a copy/paraphrase of a separate narrative. Preserve source qualifications; split facts when sources differ.')
    evidence: list[EvidenceRef] = Field(min_length=1, description='Exact evidence supporting this clause; it must also be cited in this same point, not just elsewhere in the assessment.')

class PointGrounding(Strict):
    pointId: str
    focus: Literal['capability','workflow','other','uncertain'] = Field(description='capability only for a focused RAG/Python/data-analysis capability. General workflow discovery, system deployment or LLM automation is workflow, not data_analysis merely because it solves a problem or saves time. workflow/other/uncertain require capabilities=[].')
    assessment: str = Field(description='Concise connection to confirmed requirements and limits/unknowns only. Do NOT repeat or add candidate facts, metrics or factual domain assertions here; put those once in claims with local citations. Partial support need not prove every subclause.')
    capabilities: list[Literal['rag','python','data_analysis']] = Field(max_length=1, description='ONE primary capability only, selected by the central point meaning. RAG implemented with Python stays rag; Python is incidental, not an expansion scope. Broad quantitative/data problem solving uses data_analysis, which includes its Python data activities. [] for workflow/other/uncertain; explain why.')
    scopeReason: str = Field(min_length=1, description='Why these scopes fit this point and their limits; explain [] rather than guessing a capability.')
    claims: list[PointFact] = Field(min_length=1, description='Source-backed candidate facts that form the body of the public explanation, followed by assessment. Never enumerate all supplementary evidence merely because it is clickable.')

class VerificationTarget(Strict):
    id: str
    pointId: str
    criterionId: str
    singleGoal: str = Field(min_length=1, max_length=180, description='One concrete missing answer within the linked concern/criterion, not every JD subclause. Other unresolved components remain unknown.')
    decisionReason: str = Field(min_length=1, max_length=200, description='Why this selected component merits a slot given confirmed priorities; distinguish it from remaining unknowns. Its answer does not establish the whole criterion.')
    experienceId: str | None = Field(description='Only a genuinely relevant source activity; null for unanchored questions. Never choose an employer merely because a contextual quote exists.')
    evidence: list[EvidenceRef] = Field(description='Only exact sources relevant to this single goal. Empty for unanchored targets.')

class MatchResult(Strict):
    verificationTargets: list[VerificationTarget] = Field(default_factory=list, max_length=3, description="Private plan: select exactly three distinct single-purpose targets from all material unresolved observations, considering must/bonus and priority. Reviewed before interview; never expose as assessment facts.")
    pointGrounding: list[PointGrounding] = Field(default_factory=list, description='Private metadata: exactly one entry per match point, never concerns. Required even if capabilities is empty. Independent review checks scope meaning and factual-claim completeness.')
    observations: list[Observation]
    matches: list[MatchPoint] = Field(max_length=6, description="Zero to six genuinely supported points. Unknown-only criteria belong in observations/concerns; never fill a minimum count.")
    concerns: list[AnalysisPoint] = Field(max_length=4)
    summary: str = Field(description="Matching evidence and uncertainty only; no candidate name, translated name, alias or other identity narration.")
    keyUnknown: str | None


# Private model protocol v2. Public Assessment/AnalysisPoint shapes are projections.
# Legacy MatchResult remains available for historical diagnostics and explicit fixtures.
class GroundedFact(Strict):
    id: str
    claim: str = Field(min_length=1, max_length=180)
    evidence: list[EvidenceRef] = Field(min_length=1)

class RequirementComponent(Strict):
    id: str
    criterionId: str
    component: str = Field(min_length=1, max_length=160, description='One independently assessable meaning of the linked exact requirementQuote, phrased naturally. Do not combine independent objectives or add thresholds.')
    requirementQuote: str = Field(min_length=1, description='Exact contiguous quote from this confirmed description or jdQuote grounding the component; preserve source wording even when a list needs a concise atomic component label.')
    coverage: Literal['supported','partial','unknown','contradiction']
    factIds: list[str] = Field(description='Existing canonical facts supporting this component, or contextual facts for unknown. Unknown never means absence/inability. supported/partial/contradiction require facts.')

class CriterionComponents(Strict):
    criterionId: str
    coverage: Literal['supported','partial','unknown','contradiction'] = Field(description='One judgment of the FULL confirmed criterion, preserving alternatives/preferences. Do not treat every component as a new mandatory requirement; missing is never contradiction.')
    componentIds: list[str] = Field(min_length=1, description='Preserve all meaningful components and unknowns of the full confirmed criterion; narrative is projected from these components, not separately rewritten.')

class GroundedPoint(Strict):
    id: str
    title: str = Field(min_length=1, max_length=32)
    factIds: list[str] = Field(min_length=1)
    componentIds: list[str] = Field(min_length=1, description='Positive supported/partial component(s) and same-criterion limitations only. Do not attach a different criterion merely to narrate its unknowns.')
    focus: Literal['capability','workflow','other','uncertain']
    capabilities: list[Literal['rag','python','data_analysis']] = Field(max_length=1)
    scopeReason: str = Field(min_length=1)
    supplementaryEvidence: list[EvidenceRef] = Field(description='Relevant clickable context, not asserted facts and not required in the narrative. Never use context alone to invent an achievement.')

class VerificationFocus(Strict):
    id: str
    componentId: str = Field(description='Exactly ONE unresolved component. A single ID is not permission to bundle independent questions inside its component text.')
    label: str = Field(min_length=1, max_length=26, description='Short label for the same missing answer, not a second objective.')
    objective: Literal['existence','method','contribution','validation','limitations']
    experienceId: str | None
    evidence: list[EvidenceRef] = Field(description='Only sources for that explicitly anchored experience. Unanchored existence checks use null/[]; contextual facts do not force an anchor.')

class FocusConcern(Strict):
    id: str
    focusId: str = Field(description='Exactly one shared missing-answer focus; no independent free-form concern paragraph.')

class FocusTarget(Strict):
    id: str
    focusId: str
    decisionReason: str = Field(min_length=1, max_length=200, description='Why this single component merits one of three slots under confirmed priorities; other components remain unknown.')

class StructuredMatchResult(Strict):
    facts: list[GroundedFact] = Field(max_length=32)
    components: list[RequirementComponent] = Field(min_length=1, max_length=80)
    observations: list[CriterionComponents]
    matches: list[GroundedPoint] = Field(max_length=6)
    focuses: list[VerificationFocus] = Field(min_length=3, max_length=7)
    concerns: list[FocusConcern] = Field(max_length=4)
    verificationTargets: list[FocusTarget] = Field(min_length=3, max_length=3)

class SourceExclusion(Strict):
    pointId: str
    blockId: str
    reason: str = Field(min_length=1, description='Concrete source-specific reason this cited block is irrelevant or misleading for this point, not merely partial support.')

class SourceExclusionDecision(Strict):
    pointId: str
    blockId: str
    scopeToken: str
    retain: bool = Field(description='True only if the excluded source remains irrelevant/misleading under the CURRENT point prose and linked observations. False if now relevant; repair will restore it.')
    reason: str = Field(min_length=1, description='Current-scope justification, not blind repetition of the prior exclusion reason.')

class ReviewFinding(Strict):
    category: Literal['factual_error','citation_error','ownership_error','coverage_error','question_scope','presentation','program_context','other']
    severity: Literal['blocker','warning']
    field: str = Field(description='Affected output field or point ID; not private reasoning.')
    message: str = Field(min_length=1, description='Concrete issue and requested local correction.')

class ReviewResult(Strict):
    findings: list[ReviewFinding] = Field(default_factory=list, description='Classified review findings. Warnings do not block publication. Any blocker also belongs in issues; code enforces this. Do not classify cosmetic preferences or unused auxiliary hints as blockers.')
    sourceExclusionDecisions: list[SourceExclusionDecision] = Field(default_factory=list, description='Exactly one decision for each pendingSourceExclusions entry, copying its scopeToken. Required even if approved=true or prose edits seem cosmetic. Empty only when no pending exclusions.')
    sourceExclusions: list[SourceExclusion] = Field(default_factory=list, description='Only explicitly rejected current point/block pairs. New exclusions require approved=false and a blocking issue. Use [] when no source must be removed. Unlisted eligible sources remain required during repair.')
    approved: bool = Field(description='True only when no blocking defect remains. If true, issues MUST be an empty array. False when publication requires correction.')
    issues: list[str] = Field(description='Only concrete unresolved defects that BLOCK publication and require correction. No successful checks, praise, passing explanations, summaries, nonblocking suggestions or low-risk notes. approved=true requires issues=[]. approved=false requires specific actionable blocking defects.')

class GroundedReviewFinding(Strict):
    subjectType: Literal['fact','component','focus','supplementary_source']
    subjectId: str = Field(description='An actual current catalog ID; supplementary source IDs are supplied in the review projection.')
    issueType: Literal['unsupported_fact','criterion_scope','verification_scope','irrelevant_context','presentation']
    severity: Literal['blocker','warning']
    statement: str = Field(min_length=1, description='Full current fact/component/focus value being questioned. Preserve qualifications/negation; do not crop a phrase from a qualified claim or quote an earlier draft.')
    criterionId: str | None
    requirementQuote: str | None = Field(description='For coverage/goal issues, exact confirmed requirement text supporting the finding.')
    evidence: list[EvidenceRef] = Field(description='Exact original sources supporting a factual/citation/ownership objection. Do not fabricate a counterquote.')
    message: str = Field(min_length=1, description='Specific current defect and correction. Supplementary-source findings assess relevance, never demand every context quote appear in prose.')

class StructuredReviewResult(Strict):
    findings: list[GroundedReviewFinding]
    approved: bool
    sourceExclusions: list[SourceExclusion] = Field(default_factory=list)
    sourceExclusionDecisions: list[SourceExclusionDecision] = Field(default_factory=list)

class InterviewQuestion(Strict):
    id: str
    question: str = Field(description='One verification objective following its questionPlan slot and assigned experience. A brief example follow-up about the same activity is allowed regardless of question-mark count; never merge independent topics or employers.')
    purpose: str = Field(description='One primary verification objective from the assigned plan, not a bundle of unrelated requirements.')
    pointIds: list[str]
    criterionIds: list[str]
    evidence: list[EvidenceRef]

class InterviewResult(Strict):
    questions: list[InterviewQuestion] = Field(min_length=3, max_length=3)

class ChatResult(Strict):
    text: str
    evidence: list[EvidenceRef]

class StandardsDraft(Strict):
    jdText: str
    criteria: list[Criterion]

class StandardsChatResult(Strict):
    reply: str
    draft: StandardsDraft | None
