export type Shift = "early" | "late";

export interface Skill {
  position_code: string;
  priority: number;
  proficiency: number;
  allowed: boolean;
  note: string;
}

export interface Employee {
  id: number;
  employee_no: string | null;
  /** 钉钉成员ID，仅用于导出考勤排班表 */
  external_user_id?: string | null;
  name: string;
  employment_type: string;
  job_title: string;
  is_supervisor: boolean;
  is_mentor: boolean;
  is_active: boolean;
  shift_preference: "any" | "early" | "late";
  phone: string | null;
  hire_date: string | null;
  max_days_per_week: number | null;
  notes: string;
  skills: Skill[];
}

export interface Position {
  id: number;
  code: string;
  name: string;
  department: "sales" | "photography";
  area: string;
  shift: Shift;
  start_time: string;
  end_time: string;
  allowed_employment_types: string[];
  min_supervisors: number;
  supervisor_role_code: string | null;
  allow_concurrent: boolean;
  is_active: boolean;
  sort_order: number;
  color: string;
  notes: string;
}

export interface OperationalRole {
  id: number;
  code: string;
  name: string;
  shift: Shift;
  bound_position_code: string | null;
  counts_extra_slot: boolean;
  is_active: boolean;
  description: string;
  color: string;
}

export interface Assignment {
  id: number;
  work_date: string;
  employee_id: number;
  position_code: string;
  shift: Shift;
  role_code: string | null;
  is_locked: boolean;
  source: string;
  note: string;
  employee_name: string;
  employee_type: string;
  is_supervisor: boolean;
}

export interface CoverageRow {
  work_date: string;
  day_type: string;
  position_code: string;
  position_name: string;
  department: string;
  area: string;
  shift: Shift;
  target: number;
  min: number;
  assigned: number;
  shortage: number;
  below_min: boolean;
  supervisors: number;
  required_supervisors: number;
  role_code: string | null;
}

export interface Violation {
  level: "error" | "warn";
  code: string;
  work_date?: string;
  employee_id?: number;
  employee_name?: string;
  position_code?: string;
  message: string;
}

export interface Version {
  id: number;
  name: string;
  start_date: string;
  end_date: string;
  status: "draft" | "published" | "archived";
  version_no: number;
  parent_version_id: number | null;
  published_at: string | null;
  published_by: string;
  change_note: string;
  created_by: string;
  created_at: string;
}

export interface VersionDetail extends Version {
  assignments: Assignment[];
  coverage: CoverageRow[];
  violations: Violation[];
}

export interface ScheduleRun {
  id: number;
  start_date: string;
  end_date: string;
  status: string;
  solver: string;
  objective_value: number | null;
  objective_breakdown: Record<string, number>;
  diagnostics: {
    solver_status?: string;
    total_gap?: number;
    gaps?: any[];
    missing_roles?: any[];
    duplicate_assignments?: any[];
    assigned_count?: number;
  };
  message: string;
  duration_ms: number;
  created_by: string;
  created_at: string;
}

export interface DayStat {
  work_date: string;
  weekday: string;
  day_type: string;
  is_holiday: boolean;
  is_peak: boolean;
  label: string;
  total_people: number;
  early_count: number;
  late_count: number;
  sales_count: number;
  photography_count: number;
  by_type: Record<string, number>;
  positions: {
    position_code: string;
    position_name: string;
    department: string;
    area: string;
    shift: Shift;
    target: number;
    min: number;
    assigned: number;
    shortage: number;
    supervisors: number;
  }[];
  supervisors_on_duty: { employee_id: number; name: string; role_code: string; role_name: string }[];
}

export interface EmployeeStat {
  employee_id: number;
  name: string;
  employment_type: string;
  employment_type_name: string;
  is_supervisor: boolean;
  is_mentor: boolean;
  job_title: string;
  work_days: number;
  early_count: number;
  late_count: number;
  rest_days_in_window: number;
  period_work_days: number;
  period_rest_days: number;
  period_target_rest: number | null;
  rest_gap: number | null;
  positions: Record<string, number>;
  position_total: number;
}

export interface SupervisorStat {
  employee_id: number;
  name: string;
  window_duty: number;
  period_duty: number;
  window_weekend: number;
  period_weekend: number;
  window_holiday: number;
  period_holiday: number;
  window_peak: number;
  period_peak: number;
  deviation: number;
  weekend_deviation: number;
}

export interface VersionStats {
  version_id: number;
  status: string;
  period: { start: string; end: string; days: number; target_rest: number };
  days: DayStat[];
  employees: EmployeeStat[];
  supervisors: SupervisorStat[];
  summary: {
    dates: number;
    total_assignments: number;
    total_needed: number;
    total_gap: number;
    coverage_rate: number;
    people: number;
    avg_per_day: number;
  };
}

export interface AvailabilityRequest {
  id: number;
  employee_id: number;
  request_type: string;
  start_date: string;
  end_date: string;
  shift: string | null;
  target_position_code: string | null;
  reason: string;
  status: "pending" | "approved" | "rejected";
  reviewer: string;
  reviewed_at: string | null;
  employee?: Employee;
}

export interface SettingRow {
  key: string;
  value: Record<string, any>;
  description: string;
  updated_at: string | null;
}

export interface DemandDay {
  work_date: string;
  day_type: string;
  is_holiday: boolean;
  is_peak: boolean;
  label: string;
  positions: {
    position_code: string;
    position_name: string;
    department: string;
    area: string;
    shift: Shift;
    target_count: number;
    min_count: number;
    is_override: boolean;
    note: string;
  }[];
}

export interface Pairing {
  id: number;
  employee_a_id: number;
  employee_b_id: number;
  preference_type: "prefer" | "avoid";
  scope: string;
  weight: number;
  start_date: string | null;
  end_date: string | null;
  note: string;
  is_active: boolean;
}

export interface DemandTemplate {
  id: number;
  day_type: string;
  position_code: string;
  target_count: number;
  min_count: number;
}

export interface DemandTemplate {
  id: number;
  day_type: string;
  position_code: string;
  target_count: number;
  min_count: number;
}

/* --------------------- 人员 × 日期矩阵（对齐线下 Excel） --------------------- */
export type MatrixCellKind = "work" | "rest" | "leave" | "inactive";

export interface MatrixCell {
  kind: MatrixCellKind;
  assignment_id?: number;
  position_code?: string;
  position_name?: string;
  shift?: Shift;
  role_code?: string | null;
  role_name?: string;
  role_color?: string;
  source?: string;
  is_locked?: boolean;
  request_type?: string;
  reason?: string;
  range?: string;
}

export interface MatrixRow {
  employee_id: number;
  name: string;
  employee_no: string;
  employment_type: string;
  job_title: string;
  is_supervisor: boolean;
  is_mentor: boolean;
  is_active: boolean;
  work_days: number;
  rest_days: number;
  leave_days: number;
  early_count: number;
  late_count: number;
  duty_count: number;
  shift_switches: number;
  late_to_early_switches: number;
  max_rest_run: number;
  cells: Record<string, MatrixCell>;
}

export interface MatrixDay {
  date: string;
  weekday: string;
  day_type: string;
  is_holiday: boolean;
  is_peak: boolean;
  label: string;
  work_count: number;
  rest_count: number;
  leave_count: number;
}

export interface MatrixData {
  version_id: number;
  name: string;
  status: string;
  start_date: string;
  end_date: string;
  days: MatrixDay[];
  rows: MatrixRow[];
  summary: {
    employees: number;
    supervisors: number;
    dates: number;
    total_work: number;
    total_rest: number;
    total_leave: number;
    avg_work_days: number;
    avg_rest_per_day: number;
    total_shift_switches: number;
    avg_shift_switches: number;
    total_late_to_early: number;
    max_rest_run: number;
    rest_cap: number;
    over_rest_cap_staff: number;
  };
}
