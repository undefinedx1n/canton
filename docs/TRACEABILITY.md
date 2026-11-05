# 需求 → 实现 → 测试 追踪表

> 基线：《排班自动化系统：需求与技术设计》v1.0（下称"需求文档"）
> 代码版本：`D:\workbuddy-projects\park-scheduling`（本地，未部署）
> 测试命令：`cd backend && ../.venv/Scripts/python.exe -m pytest tests -q`
> 结果：**92 passed, 1 xfailed（共 93 项）**，可重复执行

图例：✅ 已实现且有自动化测试 · ⚠️ 已实现但仅部分覆盖 · ❌ 未实现/未覆盖

---

## 1. 硬规则（需求文档 §12「硬约束」、§15「MVP 验收标准」）

| # | 需求 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| H1 | 每岗位人数 ≤ 名额 | `services/solver.py:392`（覆盖与缺口，`sum(vars) <= target*covered`）<br>`services/validation.py:176`（OVER_CAPACITY） | `test_rules.py::test_assignment_never_exceeds_target`<br>`test_validation_rules.py::test_over_capacity` | ✅ |
| H2 | 每员工每日最多 1 个岗位 | `services/solver.py:384`<br>`services/validation.py:49` | `test_rules.py::test_one_position_per_employee_per_day`<br>`test_validation_rules.py::test_duplicate_assignment`<br>`test_api_flow.py::test_manual_add_rejects_duplicate_employee_day` | ✅ |
| H3 | 必须持有岗位资格 | `services/solver.py:319 is_eligible()`<br>`validation.py:92`（NO_SKILL） | `test_rules.py::test_employee_without_skill_is_never_assigned`<br>`test_rules.py::test_skill_marked_not_allowed_is_ineligible`<br>`test_validation_rules.py::test_no_skill` | ✅ |
| H4 | A3/B3 仅长期员工或合资格主管 | `positions.allowed_employment_types=["long_term"]`<br>`services/solver.py:319`<br>`validation.py:106`（TYPE_RESTRICTED） | `test_rules.py::test_part_time_cannot_take_a3_even_with_skill`<br>`test_rules.py::test_intern_cannot_take_b3`<br>`test_rules.py::test_supervisor_is_eligible_for_a3`<br>`test_solver.py::test_a3_b3_only_long_term`<br>`test_validation_rules.py::test_type_restricted` | ✅ |
| H5 | 运营角色只能由主管担任 | `services/solver.py:392-440`（`role_vars` 只对 `is_supervisor` 开放）<br>`validation.py:139`（ROLE_NOT_SUPERVISOR / UNKNOWN_ROLE） | `test_rules.py::test_mentor_is_not_supervisor`<br>`test_fairness_pairing.py`（角色相关断言）<br>`test_validation_rules.py::test_role_not_supervisor`、`test_unknown_role` | ✅ |
| H6 | 导师不自动视为主管 | `employees.is_supervisor` 独立字段；`is_mentor` 不参与角色判定 | `test_rules.py::test_mentor_is_not_supervisor`<br>`test_solver.py::test_mentor_is_not_supervisor` | ✅ |
| H7 | 运营角色必须落在绑定岗位 | `roles.bound_position_code` + `solver.py:392` | `test_rules.py::test_operational_role_bound_to_position`<br>`test_solver.py::test_supervisor_role_requires_supervisor_position`<br>`test_validation_rules.py::test_role_position_mismatch_is_warning`（错位仅提醒） | ✅ |
| H8 | 已批准请假/不可用不可排 | `solver.py:207-218`（approved → `leaves`）<br>`validation.py:120`（ON_LEAVE） | `test_rules.py::test_approved_leave_is_hard_constraint`<br>`test_rules.py::test_shift_scoped_leave_blocks_only_that_shift`<br>`test_validation_rules.py::test_on_leave`、`test_selective_leave_only_blocks_that_shift`<br>`test_fairness_pairing.py::test_fairness_never_overrides_leave` | ✅ |
| H9 | 已锁定安排不得被移动 | `solver.py:229`（读取锁定）+ `443`（固定变量） | `test_rules.py::test_locked_assignment_is_preserved`<br>`test_solver.py::test_locked_assignment_is_preserved`<br>`test_api_flow.py::test_lock_then_reoptimize_keeps_locked_row` | ✅ |
| H10 | 主管角色与岗位名额关系可配置 | `app_settings.role_slot_rule`（是否额外名额、能否兼岗） | ⚠️ 配置项已建模并在「系统配置」页可改，但尚无自动化用例覆盖"额外名额"分支 | ⚠️ |
| H11 | 每天必须有当值主管 | `B2.supervisor_role_code=duty_supervisor` + 覆盖约束 | `test_rules.py::test_each_day_has_duty_supervisor`<br>`test_conflict_cases.py::test_missing_supervisor_role_is_diagnosed` | ✅ |
| H12 | 未批准申请默认不阻止 | `app_settings.pending_leave_policy`；`solver.py:183` | `test_rules.py::test_pending_leave_is_soft_by_default`<br>`test_rules.py::test_pending_leave_blocks_when_configured`<br>`test_solver.py::test_pending_leave_not_blocking_by_default` | ✅ |
| H13 | 停用/离职员工不参与排班 | `solver.py:190`（`is_active` 过滤）<br>`validation.py:79`（INACTIVE_EMPLOYEE） | `test_rules.py::test_inactive_employee_is_excluded`<br>`test_validation_rules.py::test_inactive_employee` | ✅ |
| H14 | 员工/岗位不存在时不得静默通过 | `validation.py:70`（UNKNOWN_ENTITY） | `test_validation_rules.py::test_unknown_entity` | ✅ |

---

## 2. 软规则（需求文档 §6/§7/§8/§12「软约束」）

| # | 需求 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| S1 | 主管总当值天数均衡 | `solver.py:463-475` + `:656 _add_fairness`（极差最小化） | `test_fairness_pairing.py::test_duty_fairness_total_spread`<br>`test_stats_reports.py::test_supervisor_fairness_fields` | ✅ |
| S2 | 周末当值均衡 | `solver.py:477-490`（`weekend_fairness`） | `test_fairness_pairing.py::test_weekend_duty_fairness` | ✅ |
| S3 | 法定节假日当值均衡 | `solver.py:477-490`（`holiday_fairness`） | `test_fairness_pairing.py::test_holiday_duty_is_tracked`<br>`test_stats_reports.py::test_holiday_duty_reported_separately` | ✅ |
| S4 | 旺季当值均衡 | `solver.py:477-490`（`peak_fairness`） | ❌ 无专门用例（逻辑与节假日同构，但未单独验证） | ❌ |
| S5 | 公平不违反请假/资格/覆盖 | `_add_fairness` 仅进入目标函数 | `test_fairness_pairing.py::test_fairness_never_overrides_leave`<br>`test_fairness_pairing.py::test_pairing_cannot_break_hard_rules` | ✅ |
| S6 | 员工班次偏好 | `solver.py:452-460`（`shift_preference`） | `test_fairness_pairing.py::test_shift_preference_is_honoured_when_weighted`<br>`test_fairness_pairing.py::test_shift_preference_does_not_break_coverage` | ✅ |
| S7 | 岗位优先级 | `solver.py:452`（`position_priority`） | ⚠️ 仅在 `test_objective_breakdown_covers_soft_goals` 中断言"目标值存在"，未验证方向性 | ⚠️ |
| S8 | 搭班负向避让（避免同班） | `solver.py:529-570` | `test_fairness_pairing.py::test_pairing_avoid_keeps_employees_apart` | ✅ |
| S9 | 搭班正向偏好（希望同班） | 同上 | `test_fairness_pairing.py::test_pairing_prefer_brings_employees_together` | ✅ |
| S10 | 搭班作用范围（同班次/同岗位/同日） | `solver.py:529` 按 `scope` 分支 | `test_fairness_pairing.py::test_pairing_scope_same_position`（+ same_shift 用例） | ✅ |
| S11 | 旺季能力匹配（高能力优先） | `solver.py:460`（`proficiency`，仅 `is_peak` 日生效） | ❌ 未覆盖 | ❌ |
| S12 | 尽量减少相对历史的剧烈变化 | `objective_breakdown["stability"]` 桶 | ❌ 未覆盖（权重为 4，影响很小） | ❌ |
| S13 | 长期员工月休 8 天（周期口径可配） | `solver.py:493-510`（`rest_balance`）+ `stats.py:104`（`period_rest_days` / `rest_gap`） | `test_stats_reports.py::test_rest_target_only_for_formal`<br>`test_stats_reports.py::test_employee_rows_are_consistent` | ⚠️ 报表侧已覆盖，求解侧"补足应休"未单独断言 |
| S14 | 工作量均衡 | `solver.py:510-528`（班次量极差） | `test_objective_breakdown_covers_soft_goals` | ⚠️ 仅目标值存在性 |
| S15 | 员工每周最多上班天数 | `employees.max_days_per_week` 字段 | `test_rules.py::test_max_days_per_week_is_enforced`（**xfail**） | ❌ 字段未进入约束（见 R-03） |

---

## 3. 页面（需求文档 §14）

| # | 需求 | 文件 | 验证方式 | 移动端（390px） | 状态 |
|---|---|---|---|---|---|
| P1 | 登录与角色权限 | `app/login/page.tsx` | 无头浏览器实跑；`test_api_flow.py::test_requires_authentication`、`test_wrong_password_rejected` | 单列卡片，可用 | ✅ |
| P2 | 排班总览（三天视图、按岗位分组、颜色仅辅助） | `app/page.tsx` | 截图 `docs/shots/02-overview.png`、`m-overview.png` | KPI 2×2、逐日堆叠 | ✅ |
| P3 | 员工档案与技能资格 | `app/employees/page.tsx` | 截图 `05-employees.png` | 表格横向滚动 + 首列冻结 | ✅ |
| P4 | 岗位配置与需求模板 | `app/positions/page.tsx` | 截图 `06-positions.png` | 同上 | ✅ |
| P5 | 每日需求（客流标签、目标/最低） | `app/demands/page.tsx` | 截图 `07-demands.png` | 同上 | ✅ |
| P6 | 请假与特殊申请审批 | `app/requests/page.tsx` | 截图 `08-requests.png` | 同上 | ✅ |
| P7 | 自动排班（进度、不可行诊断、公平说明） | `app/generate/page.tsx` | 截图 `03-generate.png` | 表单单列 | ✅ |
| P8 | 人工调整（下拉换人、锁定、即时校验、撤销） | `app/edit/page.tsx` | 截图 `04-edit.png`、`m-edit.png` | 三天列收窄 + 滑动提示 + 姓名列冻结 | ⚠️ 撤销/重做未实现（见 R-04） |
| P9 | 统计报表 | `app/stats/page.tsx` | 截图 `09-stats.png`、`m-stats.png` | 隐藏冗余列，一屏可读 | ✅ |
| P10 | 发布历史、版本对比、Excel 导出 | `app/versions/page.tsx` + `routers/excel.py` | 截图 `10-versions.png`；`test_api_flow.py` 3 项 | 表格滚动 | ✅ |
| P11 | 系统配置（周期/标签/权重/角色关系） | `app/settings/page.tsx` | 截图 `11-settings.png` | 分组单列 | ✅ |
| P12 | 界面以中文为主，适配桌面与手机查看 | `app/globals.css`、`components/AppShell.tsx` | `frontend/ui-audit.mjs` 三档视口巡检（390/820/1440），全部页面文档级横向溢出 0px | ✅（本轮新增） |

> P8 的"撤销/重做"需求文档 §14.8 有提及，当前版本仅提供"撤销更改"（放弃全部未保存修改），未实现逐步撤销栈。

---

## 4. 统计与报表（需求文档 §10）

| # | 指标 | 实现 | 测试 | 状态 |
|---|---|---|---|---|
| R1 | 当天实际上班总人数（每人每日计一次） | `stats.py:36-50` | `test_stats_reports.py::test_total_people_counts_each_employee_once` | ✅ |
| R2 | 运营角色不重复计数 | `stats.py:74`、`:86` | `test_stats_reports.py::test_role_holder_not_double_counted` | ✅ |
| R3 | 早/晚班人数 | `stats.py:50-51` | `test_stats_reports.py::test_total_people_counts_each_employee_once` | ✅ |
| R4 | 销售 / 摄影人数 | `stats.py:52-53` | `test_stats_reports.py::test_sales_and_photography_split` | ✅ |
| R5 | 各岗位人数 / 需求 / 缺口 | `stats.py:55-74` | `test_stats_reports.py::test_coverage_rows_sum_to_assignments`、`test_stats_matches_database_after_gap`、`test_no_shortage_when_fully_staffed` | ✅ |
| R6 | 长期/兼职/实习生人数 | `stats.py:54`、`:98` | `test_stats_reports.py::test_employee_rows_are_consistent`（按类型统计） | ✅ |
| R7 | 员工早晚班次数、岗位次数、工作/休息天数 | `stats.py:112-137` | `test_stats_reports.py::test_employee_rows_are_consistent` | ✅ |
| R8 | 周期休息累计与目标差异 | `stats.py:104-141` | `test_stats_reports.py::test_rest_target_only_for_formal` | ✅ |
| R9 | 主管总当值/周末/节假日/旺季及偏差 | `stats.py:142-201` | `test_stats_reports.py::test_supervisor_fairness_fields`、`test_holiday_duty_reported_separately` | ✅ |
| R10 | 排班版本与修改记录 | `models.AuditLog` + `routers/audit.py` | `test_api_flow.py::test_audit_covers_generate_and_publish` | ✅ |
| R11 | 报表接口可用 | `routers/stats.py` | `test_stats_reports.py::test_api_stats_endpoints` | ✅ |

---

## 5. 待确认规则（需求文档 §17）的落地方式

| 待确认项 | 配置键 | 默认值 | 界面入口 |
|---|---|---|---|
| 月休 8 天的周期口径 | `rest_rule` | 自然月、8 天、仅长期员工 | 系统配置 |
| 主管公平统计周期 / 节假日维护 | `fairness_period` + `calendar_days` | 自然月、60 天回看 | 系统配置 / 每日需求 |
| A2/B2/B1 是否额外占名额、能否兼岗 | `role_slot_rule` | 视为岗位标记、不额外占名额、不兼岗 | 系统配置 |
| 销售岗位"普通员工"类型口径 | `normal_staff_definition` | 兼职计入、实习生不计 | 系统配置 |
| 目标 vs 最低人数、是否允许带缺口草稿 | `gap_policy` | 允许带缺口草稿 | 每日需求 / 系统配置 |
| 跨岗兼任、同班次内换岗 | `positions.allow_concurrent` | 关闭 | 岗位与需求 |
| 旺季/节假日标签维护 | `calendar_days` | 按日期逐日维护 | 每日需求 |
| 偏好权重默认值 | `solver_weights` | 见配置页 | 系统配置 |
| 请假批准前是否阻止排班 | `pending_leave_policy` | 不阻止 | 系统配置 |
| 旧表其他代码（B450 等）含义 | 导入预览中标记为"未知代码" | 不猜测 | 员工档案导入 |

---

## 6. 覆盖缺口速览

| 编号 | 缺口 | 影响 | 建议 |
|---|---|---|---|
| G1 | 旺季当值公平（S4）、旺季能力匹配（S11）无自动化用例 | 旺季标签启用后的行为缺少回归保护 | 下一轮补 2 个用例（构造 `is_peak` 日） |
| G2 | 岗位优先级（S7）、工作量均衡（S14）只验证"有目标值" | 权重调整可能失效而不被发现 | 补方向性断言 |
| G3 | `max_days_per_week` 未进入约束（S15，xfail） | 主管以为设了上限但系统不执行 | 要么实现，要么从界面移除该字段 |
| G4 | `role_slot_rule.counts_extra_slot` 分支未覆盖 | 若改为"额外名额"，覆盖统计可能重复计数 | 补一条用例并明确口径 |
| G5 | 前端仅有 UI 巡检脚本，无 E2E 断言库 | 交互回归依赖人工看截图 | 后续引入 Playwright 测试项目 |
