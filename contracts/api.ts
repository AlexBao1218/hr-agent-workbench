// Shared wire contract 0.1.0. Brain-owned. ISO timestamps, camelCase JSON.
export type ID = string;
export type Grade = 'A' | 'B' | 'C' | 'pending';
export interface GradeReason {
  code: 'strong_match' | 'partial_match' | 'insufficient_evidence' | 'explicit_mismatch' | 'pending_review';
  label: string; explanation: string; criterionIds: ID[];
}
export type Mode = 'live' | 'fixture';
export type Coverage = 'supported' | 'partial' | 'unknown' | 'contradiction';
export interface Version { id: ID; number: number; confirmedAt: string | null; confirmedBy: string | null }
export interface Criterion { id: ID; title: string; description: string; group: 'must' | 'bonus'; priority: number; jdQuote: string }
export interface Standards { version: Version; jdText: string; criteria: Criterion[] }
export interface HatRule { id: ID; title: string; text: string; enabled: boolean }
export interface Hat { version: Version; rules: HatRule[] }
export interface Workspace {
  jobTitle: string; standards: Standards | null; draftStandards: Standards | null;
  hat: Hat; draftHat: Hat | null; mode: Mode;
  provider: { configured: boolean; model: string | null }; activeRunId: ID | null;
}
export interface Box { x: number; y: number; width: number; height: number }
// Box uses normalized 0..1 coordinates on the corresponding rendered page.
export interface SourceBlock { id: ID; page: number; text: string; box: Box | null; method: 'native' | 'ocr'; confidence: number | null }
export interface DocumentPage { number: number; width: number; height: number; imageUrl: string | null }
export interface ParsedDocument {
  id: ID; filename: string; mimeType: string; status: 'ready' | 'needs_ocr' | 'failed';
  originalUrl: string; previewKind: 'original' | 'rendered' | 'text';
  pages: DocumentPage[]; blocks: SourceBlock[]; warnings: string[];
}
export interface EvidenceRef { blockId: ID; quote: string; explanation?: string }
export interface Education { school: string; major: string; degree: string; level: 'bachelor' | 'master' | 'doctorate' | 'other'; status: 'studying' | 'graduated' | 'unknown'; start: string | null; end: string | null; evidence: EvidenceRef[] }
export interface Experience { id: ID; organization: string; role: string; kind: 'internship' | 'employment' | 'project'; start: string | null; end: string | null; summary: string; companyGroup: string | null; evidence: EvidenceRef[] }
export interface CandidateProfile { name: string; education: Education[]; experiences: Experience[]; languages: string[]; availability: string | null }
export interface Observation { criterionId: ID; coverage: Coverage; explanation: string; evidence: EvidenceRef[] }
export interface AnalysisPoint { id: ID; title: string; criterionIds: ID[]; jdRequirement: string; explanation: string; evidence: EvidenceRef[]; kind: 'match' | 'unknown' | 'gap' | 'verify' }
export interface InterviewQuestion { id: ID; question: string; purpose: string; pointIds: ID[]; criterionIds: ID[]; evidence: EvidenceRef[] }
export interface Assessment {
  id: ID; candidateId: ID; runId: ID; standardsVersion: ID; hatVersion: ID;
  mode: Mode; status: 'current' | 'stale' | 'review_required'; grade: Grade; rankValue: number;
  summary: string; keyUnknown: string | null; observations: Observation[];
  matches: AnalysisPoint[]; concerns: AnalysisPoint[]; questions: InterviewQuestion[];
  reviewed: boolean; createdAt: string;
  gradeReason?: GradeReason;
}
export type HrDecision = 'priority_interview' | 'consider' | 'not_now';
export interface HrReview { decision: HrDecision | null; note: string; updatedAt: string | null }
export interface HrReviewUpdate { decision: HrDecision | null; note: string }
export interface CandidateCard {
  gradeReason?: GradeReason;
  hrReview?: HrReview;
  id: ID; name: string; fictional: boolean; documentId: ID; profile: CandidateProfile | null;
  stage: 'uploaded' | 'parsing' | 'analyzing' | 'ready' | 'needs_attention' | 'failed';
  grade: Grade; rank: number | null; matchSummary: string; keyUnknown: string | null;
  assessmentStatus: 'current' | 'stale' | 'review_required' | 'none';
  display?: CardDisplay;
  standardsVersion: ID | null; hatVersion: ID | null; error: string | null;
}
export interface ChatMessage { id: ID; role: 'user' | 'assistant'; text: string; evidence: EvidenceRef[]; createdAt: string }
export interface Feedback { id: ID; candidateId: ID; text: string; pointId: ID | null; status: 'recorded' | 'rechecking' | 'applied' | 'needs_review'; createdAt: string }
export interface CandidateDetail { candidate: CandidateCard; document: ParsedDocument; assessment: Assessment | null; chat: ChatMessage[]; feedback: Feedback[]; display?: DetailDisplay }
export interface RunStep { id: ID; role: 'jd' | 'cv' | 'match' | 'review' | 'interview' | 'rank'; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'skipped'; candidateId: ID | null; standardsVersion: ID | null; hatVersion: ID; attempt: number; summary: string; startedAt: string | null; completedAt: string | null }
export interface Run { id: ID; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'; mode: Mode; standardsVersion: ID | null; hatVersion: ID; steps: RunStep[]; error: string | null; createdAt: string }
export interface ApiError { error: { code: string; message: string; details?: unknown } }
export interface VersionHistory { standards: Standards[]; hats: Hat[] }
export interface CandidateList { candidates: CandidateCard[]; standardsVersion: ID | null; hatVersion: ID; activeRunId: ID | null }
export interface ReorderRequest { standardsVersion: ID; must: ID[]; bonus: ID[] }
export interface UploadResult { candidateIds: ID[]; runId: ID | null; errors: { filename: string; message: string }[] }
// 0.1.1 addition: HR-owned filter metadata, excluded from model/rank inputs.
export interface CompanyGroupMapping { organization: string; group: string }
export interface CompanyGroupConfig { version: Version; mappings: CompanyGroupMapping[] }
export interface CompanyGroupUpdate { expectedVersion: ID; actor: string; mappings: CompanyGroupMapping[] }

// 0.1.2 addition: read-only presentation of existing verified facts, never a new assessment.
export interface MatchDisplay {
  pointId: ID; title: string; shortReason: string; shortFacts: string[]; coverage: 'supported' | 'partial'; additionalFactCount: number;
}
export interface CardDisplay { summary: string; supports: MatchDisplay[]; keyUnknown: {pointId: ID; text: string} | null }
export interface DetailDisplay { assessmentId: ID; matches: MatchDisplay[]; card: CardDisplay }

// 0.1.3: explicit confirmed move changes criterion meaning; reruns evaluation.
export interface GroupMoveRequest { standardsVersion: ID; criterionId: ID; targetGroup: 'must' | 'bonus'; position: number; actor: string }
export interface GroupMoveResult { standards: Standards; runId: ID | null }

// Agent workbench: versioned settings, future-run snapshots and read-only diagnostics.
export type AgentRole = 'jd' | 'cv' | 'match' | 'review' | 'interview';
export interface AgentRoleSettings { systemPrompt:string; model:string|null; reasoningEffort:'none'|'low'|'medium'|'high'; maxOutputTokens:number; temperature:number|null; timeoutSeconds:number }
export interface AgentConfig { version:Version; roles:Record<AgentRole,AgentRoleSettings>; flow:{matchRepairs:number;interviewRepairs:number} }
export interface AgentRuntime { model:string|null;provider:string;mode:Mode;maxOutputTokens:number;maxInputBytes:number|null;maxCalls:number|null;timeoutSeconds:number }
export interface AgentSnapshot extends AgentConfig { effectivePrompts:Record<AgentRole,string>;promptHashes:Record<AgentRole,string>;runtime:AgentRuntime }
export interface AgentWorkbench { active:AgentConfig;draft:AgentConfig;draftRevision:string;history:AgentConfig[];defaults:AgentConfig;lockedRules:string;lockedRulesByRole?:Record<AgentRole,string>;schemas:Record<AgentRole,unknown>;runtime:AgentRuntime }
export interface AgentRunSummary { id:ID;status:Run['status'];createdAt:string;candidateNames:string[];settingsVersion:string|null;hasSnapshot:boolean }
export interface AgentRunDebug { run:Run;settings:AgentSnapshot|null;historicalWarning:string|null;artifacts:{reference:string;role:string;attempt:number;kind:string;data:Record<string,unknown>}[];truncated:boolean }
