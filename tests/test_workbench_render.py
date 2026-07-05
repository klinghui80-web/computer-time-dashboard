from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_dashboard.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sync_dashboard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sample_history():
    return {
        "days": [
            {
                "date": "2026-07-01",
                "weekday": "周三",
                "total_active_seconds": 3600,
                "total_active_text": "1小时",
                "top_categories": ["设计", "学习/信息"],
                "categories": [
                    {"name": "设计", "seconds": 2400, "share": 0.6667, "color": "#f6bd16"},
                    {"name": "学习/信息", "seconds": 1200, "share": 0.3333, "color": "#61ddaa"},
                ],
                "switch_count": 12,
                "summary_line": "测试摘要",
                "notes": ["测试复盘提示"],
                "longest_focus": {"text": "20分", "range": "14:00–14:20", "activity": "Figma", "seconds": 1200},
                "energy": {"confidence": "中", "high_zone": "设计", "fragmented_zone": "学习/信息", "recovery_zone": "无", "evidence": ["证据"]},
                "hourly_seconds": {str(i): (600 if i == 14 else 0) for i in range(24)},
                "top_activities": [{"activity": "Figma", "text": "40分", "seconds": 2400}],
            }
        ],
        "weeks": [],
        "meta": {"generated_at": "2026-07-01T22:00:00+08:00", "delivery_time": "19:30", "db_path": "test.db"},
    }


def test_render_html_upgrades_time_dashboard_into_personal_workbench():
    module = load_module()
    html = module.render_html(sample_history())

    assert "个人工作台" in html
    assert "时间与精力" in html
    assert "待办中心" in html
    assert "今日复盘" in html
    assert "localStorage" in html
    assert "workbenchTasks" in html
    assert "workbenchReviews" in html
    assert "renderWorkbench" in html
    assert "每日 19:30" in html


def test_render_html_contains_editable_task_and_review_controls():
    module = load_module()
    html = module.render_html(sample_history())

    assert "addTask" in html
    assert "saveReview" in html
    assert "priority" in html
    assert "status" in html
    assert "今日重点" in html


def test_first_screen_is_a_minimal_pending_work_overview():
    module = load_module()
    html = module.render_html(sample_history())

    assert "overview-first-fold" in html
    assert "statusDonutStyle" in html
    assert "未完成事项" in html
    assert "紧急" in html
    assert "进行中" in html
    assert "待办" in html
    assert "待推进总数" in html


def test_add_task_form_is_modal_and_deadline_timeline_replaces_day_agenda():
    module = load_module()
    html = module.render_html(sample_history())

    assert "openTaskModal" in html
    assert "closeTaskModal" in html
    assert "task-modal-backdrop" in html
    assert "taskModal" in html
    assert "add-task-trigger" in html
    assert "任务截止时间轴" in html
    assert "renderDeadlineTimeline" in html
    assert "任务提醒" in html
    assert "工作台洞察" not in html
    assert "今日时间轴" not in html
    assert "add-task-panel" not in html


def test_deadline_view_can_toggle_between_list_and_calendar():
    module = load_module()
    html = module.render_html(sample_history())

    assert "deadlineViewMode" in html
    assert "setDeadlineView" in html
    assert "renderDeadlineCalendar" in html
    assert "deadline-calendar" in html
    assert "日历视图" in html
    assert "列表视图" in html
    assert "距截止" in html


def test_tasks_are_color_ranked_draggable_cards_with_progress_slider():
    module = load_module()
    html = module.render_html(sample_history())

    assert "priority-card-P0" in html
    assert "priority-card-P1" in html
    assert "priority-card-P2" in html
    assert "task-drag-zone" in html
    assert "grip-lines" in html
    assert html.count("grip-line") >= 3
    assert "draggable=\"true\"" in html
    assert "dragTaskId" in html
    assert "reorderTasks" in html
    assert "previewTaskProgress" in html
    assert "commitTaskProgress" in html
    assert "progress-slider" in html
    assert "type=\"range\"" in html
    assert "step=\"1\"" in html
    assert "oninput=\"previewTaskProgress" in html
    assert "onchange=\"commitTaskProgress" in html
    assert "0%" in html
    assert "100%" in html
    assert "updateTaskStatus('${task.id}', this.checked" not in html
    assert "task-card priority-card-${task.priority} ${task.status === 'done' ? 'done' : ''}\" draggable" not in html


def test_dragging_uses_priority_lanes_with_same_priority_and_cross_priority_feedback():
    module = load_module()
    html = module.render_html(sample_history())

    assert "priority-lane" in html
    assert "dropPriorityLane" in html
    assert "dragSourcePriority" in html
    assert "同优先级内排序" in html
    assert "松手切换为" in html
    assert "同一优先级可包含多个任务" in html
    assert "P0 火烧屁股" in html
    assert "P1 今日必完成" in html
    assert "P0 最高优先级" not in html
    assert "P1 高优先级" not in html
    assert "priorityForIndex" not in html


if __name__ == "__main__":
    test_render_html_upgrades_time_dashboard_into_personal_workbench()
    test_render_html_contains_editable_task_and_review_controls()
    test_first_screen_is_a_minimal_pending_work_overview()
    test_add_task_form_is_modal_and_deadline_timeline_replaces_day_agenda()
    test_deadline_view_can_toggle_between_list_and_calendar()
    test_tasks_are_color_ranked_draggable_cards_with_progress_slider()
    test_dragging_uses_priority_lanes_with_same_priority_and_cross_priority_feedback()
    print("workbench render tests passed")
