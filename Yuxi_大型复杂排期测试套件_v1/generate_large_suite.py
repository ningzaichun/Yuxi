from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "Yuxi_大型复杂排期测试套件_v1"
CORE_PATH = ROOT / "work" / "msp_complex_test_suite" / "generate_suite.py"


def load_core():
    spec = importlib.util.spec_from_file_location("schedule_oracle_core", CORE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load oracle core: {CORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load_core()


def resource(index: int, name: str, max_units: float = 1.0, rate: float = 100.0, kind: str = "WORK") -> dict[str, Any]:
    return {
        "resource_id": f"resource:r{index:02d}",
        "name": name,
        "type": kind,
        "max_units": max_units,
        "standard_rate_per_hour": rate,
    }


def assignment(task_id: str, resource_id: str, units: float, suffix: str) -> dict[str, Any]:
    return {
        "assignment_id": f"assign:{task_id.split(':')[-1]}:{suffix}",
        "task_id": task_id,
        "resource_id": resource_id,
        "units": units,
    }


def decorate_case(case: dict[str, Any], purpose: str, scale_target: str, coverage: list[str]) -> None:
    case["test_profile"] = {
        "purpose": purpose,
        "scale_target": scale_target,
        "coverage": coverage,
        "oracle_scope": "DETERMINISTIC_REFERENCE_IMPLEMENTATION",
        "microsoft_project_observation": False,
    }


def add_root_boundaries(case: dict[str, Any]) -> tuple[str, str]:
    start_id, finish_id = "task:project-start", "task:project-finish"
    case["tasks"].append(core.task(start_id, "项目开工", 0, "task:root", "MILESTONE", wbs="1.0", outline_level=2, boundary_role="PROJECT_START"))
    case["tasks"].append(core.task(finish_id, "项目完工", 0, "task:root", "MILESTONE", wbs="1.99", outline_level=2, boundary_role="PROJECT_FINISH"))
    case["boundary_whitelist"] = {"open_start_task_ids": [start_id], "open_finish_task_ids": [finish_id]}
    return start_id, finish_id


def standard_resources() -> list[dict[str, Any]]:
    names = [
        ("项目管理组", 2.0, 180.0, "WORK"), ("设计一组", 2.0, 160.0, "WORK"),
        ("设计二组", 2.0, 160.0, "WORK"), ("采购工程师", 2.0, 150.0, "WORK"),
        ("土建一班", 1.0, 120.0, "WORK"), ("土建二班", 1.0, 120.0, "WORK"),
        ("安装一班", 1.0, 135.0, "WORK"), ("安装二班", 1.0, 135.0, "WORK"),
        ("电气班组", 1.0, 140.0, "WORK"), ("调试班组", 1.0, 170.0, "WORK"),
        ("汽车吊", 1.0, 380.0, "EQUIPMENT"), ("检测设备", 1.0, 260.0, "EQUIPMENT"),
    ]
    return [resource(index + 1, *item) for index, item in enumerate(names)]


def stamp_baseline(case: dict[str, Any]) -> dict[str, Any]:
    initial = core.calculate(case)
    if initial["status"] == "VALIDATION_FAILED":
        raise RuntimeError(f"cannot stamp baseline for {case['case_id']}: {initial['issues']}")
    dates = {item["task_id"]: item for item in initial["task_dates"]}
    for item in case["tasks"]:
        if not item.get("summary") and item["task_id"] in dates:
            item["baseline_0"] = {
                "exists": True,
                "start": dates[item["task_id"]]["start"],
                "finish": dates[item["task_id"]]["finish"],
            }
    return initial


def build_l01_epc() -> dict[str, Any]:
    case = core.base_case("L01_EPC_FULL_LIFECYCLE", "大型水处理厂 EPC 全生命周期计划")
    case["project"]["planned_start"] = "2027-01-04T08:00:00+08:00"
    case["tasks"][0].update({"wbs": "1", "outline_level": 1})
    start_id, finish_id = add_root_boundaries(case)
    case["resources"] = standard_resources()
    phases = ["勘察设计", "长周期采购", "土建施工", "设备安装", "系统调试", "竣工移交"]
    package_names = ["主工艺包", "辅助系统包", "电气自控包", "公用工程包", "质量安全包"]
    action_names = ["策划与输入", "深化与会审", "执行与生产", "检查与整改", "批准与移交"]
    previous_gate = start_id
    for phase_no, phase_name in enumerate(phases, start=1):
        phase_id = f"task:p{phase_no:02d}"
        case["tasks"].append(core.task(phase_id, phase_name, None, "task:root", "SUMMARY", wbs=f"1.{phase_no}", outline_level=2))
        package_last_ids = []
        for package_no, package_name in enumerate(package_names, start=1):
            package_id = f"task:p{phase_no:02d}-pkg{package_no:02d}"
            case["tasks"].append(core.task(package_id, package_name, None, phase_id, "SUMMARY", wbs=f"1.{phase_no}.{package_no}", outline_level=3))
            previous = None
            for action_no, action_name in enumerate(action_names, start=1):
                task_id = f"task:p{phase_no:02d}-pkg{package_no:02d}-a{action_no:02d}"
                duration = 480 * (2 + (phase_no + package_no + action_no) % 5)
                calendar_id = None
                case["tasks"].append(core.task(
                    task_id, f"{package_name}-{action_name}", duration, package_id, "TASK",
                    wbs=f"1.{phase_no}.{package_no}.{action_no}", outline_level=4, calendar_id=calendar_id,
                ))
                if action_no == 1:
                    case["dependencies"].append(core.dep(previous_gate, task_id, "FS", (package_no - 1) * 240))
                else:
                    relation_pattern = {2: ("FS", 0), 3: ("SS", 480), 4: ("FS", -480), 5: ("FF", 0)}
                    kind, lag = ("SF", 480) if action_no == 5 and package_no == 5 else relation_pattern[action_no]
                    case["dependencies"].append(core.dep(previous, task_id, kind, lag))
                previous = task_id
                primary_resource = f"resource:r{((phase_no * 3 + package_no + action_no) % 10) + 1:02d}"
                support_resource = "resource:r11" if action_no in {3, 4} else "resource:r01"
                case["assignments"].append(assignment(task_id, primary_resource, 1.0, "primary"))
                case["assignments"].append(assignment(task_id, support_resource, 0.5, "support"))
            package_last_ids.append(previous)
        gate_id = f"task:p{phase_no:02d}-gate"
        case["tasks"].append(core.task(gate_id, f"{phase_name}阶段门", 0, phase_id, "MILESTONE", wbs=f"1.{phase_no}.99", outline_level=3))
        for last_id in package_last_ids:
            case["dependencies"].append(core.dep(last_id, gate_id))
        previous_gate = gate_id
    case["dependencies"].append(core.dep(previous_gate, finish_id))
    initial = stamp_baseline(case)
    case["project"]["required_finish"] = initial["project_dates"]["finish"]
    decorate_case(case, "验证大型 EPC WBS、混合依赖、Lag、基线、资源和成本", "约 190 项", ["4级WBS", "FS/SS/FF/SF", "正负Lag", "Baseline 0", "300条Assignment", "资源超配", "成本"])
    return case


def build_l02_multisite() -> dict[str, Any]:
    standard = core.standard_calendar()
    six_day = core.standard_calendar("calendar:six-day")
    six_day["name"] = "六天施工日历"
    six_day["weekly_pattern"]["SATURDAY"] = {"day_type": "WORKING", "intervals": [{"start": "08:00", "finish": "12:00"}, {"start": "13:00", "finish": "17:00"}]}
    case = core.base_case("L02_MULTI_SITE_PARALLEL", "八标段多作业面并行施工计划", [standard, six_day])
    case["project"]["planned_start"] = "2027-03-01T08:00:00+08:00"
    case["tasks"][0].update({"wbs": "1", "outline_level": 1})
    start_id, finish_id = add_root_boundaries(case)
    case["resources"] = standard_resources()[:10]
    site_gates = []
    for site_no in range(1, 9):
        site_id = f"task:s{site_no:02d}"
        case["tasks"].append(core.task(site_id, f"第{site_no}标段", None, "task:root", "SUMMARY", wbs=f"1.{site_no}", outline_level=2))
        zone_last_ids = []
        for zone_no in range(1, 4):
            zone_id = f"task:s{site_no:02d}-z{zone_no:02d}"
            case["tasks"].append(core.task(zone_id, f"作业面{zone_no}", None, site_id, "SUMMARY", wbs=f"1.{site_no}.{zone_no}", outline_level=3))
            previous = None
            for action_no in range(1, 9):
                task_id = f"task:s{site_no:02d}-z{zone_no:02d}-a{action_no:02d}"
                duration = 480 * (1 + (site_no + zone_no + action_no) % 6)
                calendar_id = "calendar:six-day" if site_no % 2 == 0 else None
                case["tasks"].append(core.task(task_id, f"标段{site_no}-作业面{zone_no}-工序{action_no}", duration, zone_id, "TASK", wbs=f"1.{site_no}.{zone_no}.{action_no}", outline_level=4, calendar_id=calendar_id))
                if action_no == 1:
                    case["dependencies"].append(core.dep(start_id, task_id, "FS", (site_no + zone_no) * 120))
                else:
                    kind = "SS" if action_no in {3, 6} else "FS"
                    lag = 480 if kind == "SS" else (0 if action_no != 5 else -240)
                    case["dependencies"].append(core.dep(previous, task_id, kind, lag))
                previous = task_id
                resource_id = f"resource:r{((zone_no * 2 + action_no) % 8) + 1:02d}"
                case["assignments"].append(assignment(task_id, resource_id, 1.0, "shared"))
            zone_last_ids.append(previous)
        gate_id = f"task:s{site_no:02d}-gate"
        case["tasks"].append(core.task(gate_id, f"第{site_no}标段完工", 0, site_id, "MILESTONE", wbs=f"1.{site_no}.99", outline_level=3))
        for last_id in zone_last_ids:
            case["dependencies"].append(core.dep(last_id, gate_id))
        site_gates.append(gate_id)
    for left, right in zip(site_gates, site_gates[1:]):
        # Cross-site FF links create additional coordination without fully serializing sites.
        case["dependencies"].append(core.dep(left, right, "FF", -480))
    for gate_id in site_gates:
        case["dependencies"].append(core.dep(gate_id, finish_id))
    decorate_case(case, "验证多标段并行、跨标段协调、共享资源和多日历", "约 230 项", ["8标段", "24作业面", "并行DAG", "跨标段FF", "共享资源", "资源冲突", "双日历"])
    return case


def build_calendars() -> list[dict[str, Any]]:
    standard = core.standard_calendar()
    standard["exceptions"] = [
        {"exception_id": "holiday", "name": "春节停工", "start_date": "2027-02-08T00:00:00+08:00", "finish_date": "2027-02-12T23:59:59+08:00", "working": False, "intervals": []},
        {"exception_id": "makeup", "name": "周日补班", "start_date": "2027-02-21T00:00:00+08:00", "finish_date": "2027-02-21T23:59:59+08:00", "working": True, "intervals": [{"start": "08:00", "finish": "12:00"}, {"start": "13:00", "finish": "17:00"}]},
    ]
    six_day = core.standard_calendar("calendar:six-day")
    six_day["name"] = "六天班"
    six_day["weekly_pattern"]["SATURDAY"] = {"day_type": "WORKING", "intervals": [{"start": "07:00", "finish": "12:00"}, {"start": "13:00", "finish": "18:00"}]}
    night = core.standard_calendar("calendar:night")
    night["name"] = "夜班日历"
    for day in night["weekly_pattern"].values():
        day.update({"day_type": "WORKING", "intervals": [{"start": "20:00", "finish": "04:00"}]})
    continuous = core.standard_calendar("calendar:24x7")
    continuous["name"] = "24x7连续运行"
    for day in continuous["weekly_pattern"].values():
        day.update({"day_type": "WORKING", "intervals": [{"start": "00:00", "finish": "00:00"}]})
    continuous["exceptions"] = [{"exception_id": "maintenance", "name": "系统维护停机", "start_date": "2027-02-18T00:00:00+08:00", "finish_date": "2027-02-18T23:59:59+08:00", "working": False, "intervals": []}]
    double_shift = core.standard_calendar("calendar:double-shift")
    double_shift["name"] = "双班制"
    for day in double_shift["weekly_pattern"].values():
        day.update({"day_type": "WORKING", "intervals": [{"start": "06:00", "finish": "14:00"}, {"start": "14:00", "finish": "22:00"}]})
    return [standard, six_day, night, continuous, double_shift]


def build_l03_calendar_stress() -> dict[str, Any]:
    calendars = build_calendars()
    case = core.base_case("L03_CALENDAR_SHIFT_STRESS", "多日历与跨班次排程压力计划", calendars)
    case["project"]["planned_start"] = "2027-02-01T08:00:00+08:00"
    case["tasks"][0].update({"wbs": "1", "outline_level": 1})
    start_id, finish_id = add_root_boundaries(case)
    group_gates = []
    for group_no, calendar in enumerate(calendars, start=1):
        group_id = f"task:cal{group_no:02d}"
        case["tasks"].append(core.task(group_id, calendar["name"], None, "task:root", "SUMMARY", wbs=f"1.{group_no}", outline_level=2))
        previous = start_id
        for action_no in range(1, 25):
            task_id = f"task:cal{group_no:02d}-a{action_no:02d}"
            duration = 180 + ((group_no * 97 + action_no * 53) % 1800)
            case["tasks"].append(core.task(task_id, f"{calendar['name']}-作业{action_no}", duration, group_id, "TASK", wbs=f"1.{group_no}.{action_no}", outline_level=3, calendar_id=calendar["calendar_id"]))
            kind = "SS" if action_no % 7 == 0 else ("FF" if action_no % 11 == 0 else "FS")
            lag = -240 if action_no % 9 == 0 else (480 if action_no % 5 == 0 else 0)
            case["dependencies"].append(core.dep(previous, task_id, kind, lag))
            previous = task_id
        gate_id = f"task:cal{group_no:02d}-gate"
        case["tasks"].append(core.task(gate_id, f"{calendar['name']}完成", 0, group_id, "MILESTONE", wbs=f"1.{group_no}.99", outline_level=3, calendar_id=calendar["calendar_id"]))
        case["dependencies"].append(core.dep(previous, gate_id))
        group_gates.append(gate_id)
    # Cross-calendar handoffs produce multiple predecessors with different calendars.
    for group_no in range(1, 5):
        case["dependencies"].append(core.dep(f"task:cal{group_no:02d}-a12", f"task:cal{group_no + 1:02d}-a18", "FS", 360))
    for gate_id in group_gates:
        case["dependencies"].append(core.dep(gate_id, finish_id))
    decorate_case(case, "验证跨午夜夜班、24x7、双班制、停工、补班和跨日历Lag", "约 130 项", ["5种日历", "跨午夜", "24x7", "节假日", "补班", "正负Lag", "跨日历多前置"])
    return case


def build_l04_progress() -> dict[str, Any]:
    case = core.base_case("L04_BASELINE_PROGRESS_FORECAST", "大型基线与进度更新预测计划")
    case["project"]["planned_start"] = "2027-04-05T08:00:00+08:00"
    case["tasks"][0].update({"wbs": "1", "outline_level": 1})
    start_id, finish_id = add_root_boundaries(case)
    case["resources"] = standard_resources()[:8]
    gate_ids = []
    activity_groups: list[list[str]] = []
    for stream_no in range(1, 7):
        stream_id = f"task:w{stream_no:02d}"
        case["tasks"].append(core.task(stream_id, f"专业工作流{stream_no}", None, "task:root", "SUMMARY", wbs=f"1.{stream_no}", outline_level=2))
        ids = []
        previous = start_id
        for action_no in range(1, 26):
            task_id = f"task:w{stream_no:02d}-a{action_no:02d}"
            duration = 480 * (1 + (stream_no + action_no) % 5)
            case["tasks"].append(core.task(task_id, f"工作流{stream_no}-任务{action_no}", duration, stream_id, "TASK", wbs=f"1.{stream_no}.{action_no}", outline_level=3))
            case["dependencies"].append(core.dep(previous, task_id, "FS", 0 if action_no % 8 else -240))
            previous = task_id
            ids.append(task_id)
            resource_id = f"resource:r{((stream_no + action_no) % 8) + 1:02d}"
            case["assignments"].append(assignment(task_id, resource_id, 1.0, "owner"))
        gate_id = f"task:w{stream_no:02d}-gate"
        case["tasks"].append(core.task(gate_id, f"专业{stream_no}完成", 0, stream_id, "MILESTONE", wbs=f"1.{stream_no}.99", outline_level=3))
        case["dependencies"].append(core.dep(previous, gate_id))
        gate_ids.append(gate_id)
        activity_groups.append(ids)
    for gate_id in gate_ids:
        case["dependencies"].append(core.dep(gate_id, finish_id))
    initial = stamp_baseline(case)
    baseline_dates = {item["task_id"]: item for item in initial["task_dates"]}
    status_candidates = [core.dt(baseline_dates[group[6]]["finish"]) for group in activity_groups]
    case["project"]["status_date"] = core.iso(max(status_candidates))
    # Freeze completed and in-progress states; one delayed actual deliberately propagates conflicts.
    standard_cal = core.WorkCalendar(case["calendars"][0])
    for stream_no, group in enumerate(activity_groups, start=1):
        for action_no, task_id in enumerate(group[:6], start=1):
            item = next(value for value in case["tasks"] if value["task_id"] == task_id)
            item["status"] = "COMPLETED"
            item["actual_start"] = baseline_dates[task_id]["start"]
            finish = core.dt(baseline_dates[task_id]["finish"])
            if stream_no == 1 and action_no == 4:
                finish = standard_cal.add(finish, 480)
            item["actual_finish"] = core.iso(finish)
        in_progress_id = group[6]
        in_progress = next(value for value in case["tasks"] if value["task_id"] == in_progress_id)
        in_progress["status"] = "IN_PROGRESS"
        in_progress["actual_start"] = baseline_dates[in_progress_id]["start"]
        in_progress["remaining_duration_minutes"] = max(480, int(in_progress["duration_minutes"] * 0.6))
    start_task = next(value for value in case["tasks"] if value["task_id"] == start_id)
    start_task.update({"status": "COMPLETED", "actual_start": case["project"]["planned_start"], "actual_finish": case["project"]["planned_start"]})
    baseline_finish = core.dt(initial["project_dates"]["finish"])
    case["project"]["required_finish"] = core.iso(baseline_finish)
    for index, gate_id in enumerate(gate_ids):
        gate = next(value for value in case["tasks"] if value["task_id"] == gate_id)
        gate["deadline"] = core.iso(baseline_finish - timedelta(days=5 - min(index, 4)))
    decorate_case(case, "验证大规模 Baseline、状态日期、实际完成、进行中剩余工期和预测偏差", "约 160 项", ["Baseline 0", "状态日期", "36项已完成", "6项进行中", "实际网络冲突", "Deadline", "负浮时", "资源"])
    return case


def build_l05_invalid() -> dict[str, Any]:
    case = core.base_case("L05_BULK_INVALID_GUARDRAILS", "大规模错误输入与防御性校验")
    case["tasks"][0].update({"wbs": "1", "outline_level": 1})
    for group_no in range(1, 7):
        group_id = f"task:g{group_no:02d}"
        case["tasks"].append(core.task(group_id, f"错误数据组{group_no}", None, "task:root", "SUMMARY", wbs=f"1.{group_no}", outline_level=2))
        previous = None
        for action_no in range(1, 21):
            task_id = f"task:g{group_no:02d}-a{action_no:02d}"
            kind = "MILESTONE" if action_no in {4, 8} else "TASK"
            duration = 480 if kind == "MILESTONE" else 480 * (1 + action_no % 4)
            parent = "task:missing-parent" if action_no in {6, 12} else group_id
            calendar_id = "calendar:missing" if action_no in {5, 15} else None
            case["tasks"].append(core.task(task_id, f"错误任务{group_no}-{action_no}", duration, parent, kind, wbs=f"1.{group_no}.{action_no}", outline_level=3, calendar_id=calendar_id))
            if previous:
                case["dependencies"].append(core.dep(previous, task_id))
            previous = task_id
        # Back edge creates a cycle inside every group.
        case["dependencies"].append(core.dep(f"task:g{group_no:02d}-a20", f"task:g{group_no:02d}-a03"))
        case["dependencies"].append(core.dep(f"task:g{group_no:02d}-a10", f"task:not-found-{group_no}"))
    case["assignments"] = [
        {"assignment_id": f"assign:bad:{index}", "task_id": f"task:g{((index - 1) % 6) + 1:02d}-a01", "resource_id": f"resource:missing-{index}", "units": 1.0}
        for index in range(1, 13)
    ]
    decorate_case(case, "验证大量错误同时存在时引擎必须拒绝排程并完整报告", "127 项", ["6组循环", "12个错误里程碑", "12个无效日历", "12个孤儿父级", "6条悬空依赖", "12条无效Assignment"])
    return case


def build_l06_scale() -> dict[str, Any]:
    case = core.base_case("L06_SCALE_500_DAG", "500 活动大型 DAG 性能计划")
    case["project"]["planned_start"] = "2027-01-04T08:00:00+08:00"
    case["tasks"][0].update({"wbs": "1", "outline_level": 1})
    start_id, finish_id = add_root_boundaries(case)
    case["resources"] = [resource(index, f"共享资源{index}", 1.0, 80.0 + index * 5) for index in range(1, 21)]
    previous_phase_gate = start_id
    for phase_no in range(1, 11):
        phase_id = f"task:scale-p{phase_no:02d}"
        case["tasks"].append(core.task(phase_id, f"阶段{phase_no}", None, "task:root", "SUMMARY", wbs=f"1.{phase_no}", outline_level=2))
        package_last = []
        for package_no in range(1, 6):
            package_id = f"task:scale-p{phase_no:02d}-pkg{package_no:02d}"
            case["tasks"].append(core.task(package_id, f"阶段{phase_no}-包{package_no}", None, phase_id, "SUMMARY", wbs=f"1.{phase_no}.{package_no}", outline_level=3))
            previous = previous_phase_gate
            for action_no in range(1, 11):
                task_id = f"task:scale-p{phase_no:02d}-pkg{package_no:02d}-a{action_no:02d}"
                duration = 240 + ((phase_no * 131 + package_no * 71 + action_no * 43) % 1200)
                case["tasks"].append(core.task(task_id, f"P{phase_no}-包{package_no}-活动{action_no}", duration, package_id, "TASK", wbs=f"1.{phase_no}.{package_no}.{action_no}", outline_level=4))
                kind = "SS" if action_no in {4, 8} else "FS"
                lag = 240 if kind == "SS" else (120 if action_no == 6 else 0)
                case["dependencies"].append(core.dep(previous, task_id, kind, lag))
                previous = task_id
                resource_id = "resource:r01" if action_no == 1 else f"resource:r{((package_no * 3 + action_no) % 20) + 1:02d}"
                case["assignments"].append(assignment(task_id, resource_id, 1.0, "scale"))
            package_last.append(previous)
        gate_id = f"task:scale-p{phase_no:02d}-gate"
        case["tasks"].append(core.task(gate_id, f"阶段{phase_no}完成", 0, phase_id, "MILESTONE", wbs=f"1.{phase_no}.99", outline_level=3))
        for last_id in package_last:
            case["dependencies"].append(core.dep(last_id, gate_id))
        previous_phase_gate = gate_id
    case["dependencies"].append(core.dep(previous_phase_gate, finish_id))
    decorate_case(case, "验证500活动、四级WBS、大型DAG、批量资源和校验性能", "573 项 / 500活动", ["500活动", "四级WBS", "约550条依赖", "500条Assignment", "20资源", "资源超配", "性能", "确定性排序"])
    return case


def case_metrics(case: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    return {
        "tasks": len(case["tasks"]),
        "summary_tasks": sum(bool(item.get("summary")) for item in case["tasks"]),
        "leaf_tasks": sum(not item.get("summary") for item in case["tasks"]),
        "milestones": sum(bool(item.get("milestone")) for item in case["tasks"]),
        "dependencies": len(case["dependencies"]),
        "dependency_types": {kind: sum(item["type"] == kind for item in case["dependencies"]) for kind in ("FS", "SS", "FF", "SF")},
        "nonzero_lags": sum(int(item.get("lag_minutes", 0)) != 0 for item in case["dependencies"]),
        "calendars": len(case["calendars"]),
        "resources": len(case["resources"]),
        "assignments": len(case["assignments"]),
        "baseline_tasks": sum(bool(item.get("baseline_0", {}).get("exists")) for item in case["tasks"]),
        "completed_tasks": sum(item.get("status") == "COMPLETED" for item in case["tasks"]),
        "in_progress_tasks": sum(item.get("status") == "IN_PROGRESS" for item in case["tasks"]),
        "expected_status": expected["status"],
        "expected_issues": len(expected["issues"]),
        "expected_resource_conflicts": len(expected["resource_conflicts"]),
        "expected_baseline_variances": len(expected["baseline_variances"]),
    }


def actual_template(case_id: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "status": "<SUCCEEDED|SUCCEEDED_WITH_ISSUES|VALIDATION_FAILED>",
        "project_dates": None,
        "task_dates": [],
        "summary_dates": [],
        "issues": [],
        "resource_conflicts": [],
        "assignment_costs": [],
        "baseline_variances": [],
    }


def main() -> None:
    cases = [build_l01_epc(), build_l02_multisite(), build_l03_calendar_stress(), build_l04_progress(), build_l05_invalid(), build_l06_scale()]
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    manifest_cases = []
    coverage = []
    for case in cases:
        expected = core.calculate(case)
        metrics = case_metrics(case, expected)
        case_dir = OUT / case["case_id"]
        core.dump(case_dir / "input.json", case)
        core.dump(case_dir / "expected.json", expected)
        core.dump(case_dir / "actual_template.json", actual_template(case["case_id"]))
        core.dump(case_dir / "case_profile.json", {"case_id": case["case_id"], "title": case["title"], "test_profile": case["test_profile"], "metrics": metrics})
        files = []
        for path in sorted(case_dir.iterdir()):
            files.append({"path": path.name, "size_bytes": path.stat().st_size, "sha256": core.sha256(path)})
        manifest_cases.append({"case_id": case["case_id"], "title": case["title"], **metrics, "files": files})
        coverage.append({"case_id": case["case_id"], "title": case["title"], "coverage": case["test_profile"]["coverage"], "metrics": metrics})

    support_source = ROOT / "outputs" / "Yuxi_复杂排期测试套件_v1"
    for name in ("verify_suite.py", "verify_package.py", "adapter_mapping.json"):
        shutil.copy2(support_source / name, OUT / name)
    shutil.copy2(Path(__file__).with_name("README.md"), OUT / "README.md")
    shutil.copy2(Path(__file__), OUT / "generate_large_suite.py")
    core.dump(OUT / "coverage_matrix.json", {"suite_id": "YUXI_LARGE_COMPLEX_SCHEDULE_SUITE_V1", "cases": coverage})
    root_files = []
    for name in ("README.md", "adapter_mapping.json", "coverage_matrix.json", "generate_large_suite.py", "verify_package.py", "verify_suite.py"):
        path = OUT / name
        root_files.append({"path": name, "size_bytes": path.stat().st_size, "sha256": core.sha256(path)})
    core.dump(OUT / "manifest.json", {"suite_id": "YUXI_LARGE_COMPLEX_SCHEDULE_SUITE_V1", "oracle_type": "DETERMINISTIC_REFERENCE_IMPLEMENTATION", "case_count": len(cases), "root_files": root_files, "cases": manifest_cases})
    print(json.dumps({"output": str(OUT), "cases": len(cases), "tasks": sum(item["tasks"] for item in manifest_cases)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
