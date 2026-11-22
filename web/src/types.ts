// Shapes returned by the student-facing API (see backend/app/main.py safe_* projections).

export interface CaseSummary {
  public_id: string; version: number; title: string; specialty: string; chief_complaint: string; review_status: string;
  patient?: { age: number | null; sex: string | null };
}
export interface SessionSummary {
  session_id: string; case: CaseSummary; sealed: boolean; sim_time_minutes: number; event_count: number;
  summary_id: string; score_status: string | null;
}
export interface TrainingEvent {
  event_id: string; request_id: string; seq: number; action: string; intent: string | null; sim_time: number;
  result: {
    answer?: string; advanced_minutes?: number; state?: string; executed?: boolean; planned?: boolean;
    exam_results?: { name: string; result: string | null }[]; clinical_updates?: string[]; diagnosis?: string;
    candidates?: DifferentialDraft[]; treatment?: { treatment_id: string; confirm_execution: boolean };
  };
}
export interface Order {
  state: string; exam_name: string; category?: string; cost?: number; catalog_id?: string | null; ordered_sim_time: number; sample_sim_time: number; return_sim_time: number;
  result: string | null; viewed: boolean;
}
export interface DifferentialDraft {
  candidate_id: string; supporting_evidence: string[]; opposing_evidence: string[]; evidence_event_ids: string[];
}
export interface TrainingState {
  session_id: string; workflow: string; clinical_state: string; sim_time_minutes: number; case_notes: string;
  submissions: {
    diagnosis: string | null; differential: DifferentialDraft[];
    treatment: { treatment_id: string; treatment_name?: string; confirm_execution: boolean } | null;
  };
  disclosed_fact_count: number; clinical_updates: string[]; orders: Order[]; events: TrainingEvent[];
  sealed: boolean; seal_seq: number | null; state_version: number; exam_cost_total?: number;
}
export interface Coverage {
  question_count: number; covered: number; total: number; notice: string;
  domains: { domain: string; label: string; asked: boolean; question_count: number; hint: string }[];
  hpi_elements: { label: string; asked: boolean }[];
}
export interface SessionDetail {
  state: TrainingState; case: CaseSummary; history_coverage: Coverage;
  options: {
    exams: CatalogExam[];
    treatments: { treatment_id: string; name: string }[];
    differentials: { candidate_id: string; label: string }[];
  };
}
export interface CatalogExam { exam_id: string; name: string; category: string; cost: number; minutes: number; baseline?: boolean; }
export interface ScoreItem {
  item_id: string; stage: string; hit: boolean; score: number; max_score: number;
  evidence_event_ids: string[]; reason: string; improvement: string;
}
export interface ScoreSnapshot { session_id: string; total_score: number; max_score: number; status: string; items: ScoreItem[]; }

export const STAGES = ["病史采集", "检查选择", "鉴别诊断", "诊断", "治疗"] as const;
export const patientLine = (c: CaseSummary) =>
  [c.patient?.sex, c.patient?.age != null ? `${c.patient.age}岁` : null].filter(Boolean).join("  ") || "成人";
export const CLINICAL_STATE: Record<string, { label: string; tone: string }> = {
  initial: { label: "平稳", tone: "ok" }, stabilized: { label: "好转", tone: "ok" },
  deteriorating: { label: "恶化中", tone: "bad" }, deteriorated: { label: "危重", tone: "bad" },
};
export const ITEM_LABEL: Record<string, string> = { exam_stewardship: "检查合理性" };

export const ORDER_STATE: Record<string, string> = {
  pending: "等待回报", ready: "已回报", failed: "检查失败", not_configured: "未配置结果", not_ordered: "尚未开立",
};
export const REVIEW_STATUS: Record<string, string> = {
  draft: "草稿", validated: "结构校验通过", under_review: "待医学审核", published: "已发布",
};
