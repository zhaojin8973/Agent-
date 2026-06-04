"""测试进度回调系统。"""

import threading
import time

import pytest

from hermes_core.progress import ProgressEvent, ProgressReporter


@pytest.mark.unit
class TestProgressEvent:
    """验证 ProgressEvent 数据类的创建和使用。"""

    def test_default_values(self):
        """默认值：pct=0, is_error=False, is_warning=False。"""
        event = ProgressEvent(
            stage="test",
            step=1,
            total_steps=10,
            message="测试消息",
        )
        assert event.pct == 0.0
        assert not event.is_error
        assert not event.is_warning

    def test_error_flag(self):
        """is_error=True 时正确创建。"""
        event = ProgressEvent(
            stage="error_stage",
            step=3,
            total_steps=10,
            message="出错了",
            pct=30.0,
            is_error=True,
        )
        assert event.is_error
        assert event.stage == "error_stage"

    def test_warning_flag(self):
        """is_warning=True 时正确创建。"""
        event = ProgressEvent(
            stage="warn_stage",
            step=5,
            total_steps=10,
            message="警告",
            is_warning=True,
        )
        assert event.is_warning


@pytest.mark.unit
class TestProgressReporterBasic:
    """验证 ProgressReporter 基本功能。"""

    def test_initial_state(self):
        """初始状态：step=0, 阶段栈为空。"""
        reporter = ProgressReporter(total_steps=10)
        assert reporter.current_step == 0
        assert reporter.current_stage is None
        assert reporter.pct == 0.0

    def test_pct_calculation(self):
        """pct 属性正确计算百分比。"""
        reporter = ProgressReporter(total_steps=8)
        reporter.advance("步骤1")
        assert reporter.pct == 12.5  # 1/8 * 100
        reporter.advance("步骤2")
        assert reporter.pct == 25.0  # 2/8 * 100
        reporter.advance("步骤3")
        reporter.advance("步骤4")
        assert reporter.pct == 50.0  # 4/8 * 100

    def test_pct_max_100(self):
        """pct 不超过 100.0。"""
        reporter = ProgressReporter(total_steps=5)
        for _ in range(10):
            reporter.advance("step")
        assert reporter.pct == 100.0

    def test_total_steps_min_1(self):
        """total_steps 最小为 1，避免除零。"""
        reporter = ProgressReporter(total_steps=0)
        assert reporter.total_steps == 1

    def test_step_explicit_number(self):
        """step() 方法设置确切的步骤编号。"""
        reporter = ProgressReporter(total_steps=10)
        reporter.step(5, "跳到第5步")
        assert reporter.current_step == 5
        assert reporter.pct == 50.0


@pytest.mark.unit
class TestProgressReporterCallbacks:
    """验证回调注册和触发。"""

    def test_single_callback_receives_event(self):
        """注册单个回调，advance 时回调被调用。"""
        reporter = ProgressReporter(total_steps=10)
        events: list[ProgressEvent] = []

        reporter.on_progress(events.append)
        reporter.advance("测试消息")

        assert len(events) == 1
        assert events[0].message == "测试消息"
        assert events[0].step == 1

    def test_multiple_callbacks(self):
        """注册多个回调，全部被调用。"""
        reporter = ProgressReporter(total_steps=5)
        results_a: list[str] = []
        results_b: list[str] = []

        reporter.on_progress(lambda e: results_a.append(e.message))
        reporter.on_progress(lambda e: results_b.append(e.message))
        reporter.advance("hello")

        assert results_a == ["hello"]
        assert results_b == ["hello"]

    def test_callback_exception_does_not_propagate(self):
        """回调抛出异常不影响其他回调。"""
        reporter = ProgressReporter(total_steps=5)
        results: list[str] = []

        def bad_callback(_event: ProgressEvent) -> None:
            raise RuntimeError("回调错误")

        reporter.on_progress(bad_callback)
        reporter.on_progress(lambda e: results.append(e.message))
        reporter.advance("应该到达")

        assert results == ["应该到达"]


@pytest.mark.unit
class TestProgressReporterStages:
    """验证阶段栈管理。"""

    def test_stage_start_and_done(self):
        """阶段开始入栈，完成后出栈。"""
        reporter = ProgressReporter(total_steps=10)
        reporter.stage_start("gain_staging", "开始增益分级")
        assert reporter.current_stage == "gain_staging"
        reporter.stage_done("gain_staging")
        assert reporter.current_stage is None

    def test_nested_stages(self):
        """嵌套阶段正确入栈出栈。"""
        reporter = ProgressReporter(total_steps=10)
        reporter.stage_start("outer")
        reporter.stage_start("inner")
        assert reporter.current_stage == "inner"
        reporter.stage_done("inner")
        assert reporter.current_stage == "outer"
        reporter.stage_done("outer")
        assert reporter.current_stage is None

    def test_stage_mismatch_warning(self):
        """阶段名称不匹配时仅记录警告，不影响继续使用。"""
        reporter = ProgressReporter(total_steps=10)
        reporter.stage_start("gain_staging")
        # 尝试结束错误的阶段名 — 不应崩溃
        reporter.stage_done("wrong_stage")
        assert reporter.current_stage == "gain_staging"
        reporter.stage_done("gain_staging")
        assert reporter.current_stage is None

    def test_stage_done_on_empty_stack(self):
        """空栈上调用 stage_done 不崩溃。"""
        reporter = ProgressReporter(total_steps=10)
        reporter.stage_done("nothing")  # 不应崩溃
        assert reporter.current_stage is None

    def test_events_reflect_current_stage(self):
        """advance/step 时事件中包含当前阶段名。"""
        reporter = ProgressReporter(total_steps=5)
        events: list[ProgressEvent] = []

        reporter.on_progress(events.append)
        reporter.stage_start("render", "开始渲染")
        reporter.advance("处理中")

        # 最后一个事件应该反映当前阶段
        last_event = events[-1]
        assert last_event.stage == "render"


@pytest.mark.unit
class TestProgressReporterWarningError:
    """验证警告和错误报告。"""

    def test_warning_event(self):
        """warning() 发出 is_warning=True 的事件。"""
        reporter = ProgressReporter(total_steps=5)
        events: list[ProgressEvent] = []
        reporter.on_progress(events.append)
        reporter.warning("磁盘空间不足")
        assert len(events) == 1
        assert events[0].is_warning
        assert not events[0].is_error

    def test_error_event(self):
        """error() 发出 is_error=True 的事件。"""
        reporter = ProgressReporter(total_steps=5)
        events: list[ProgressEvent] = []
        reporter.on_progress(events.append)
        reporter.error("渲染失败")
        assert len(events) == 1
        assert events[0].is_error
        assert not events[0].is_warning

    def test_warning_does_not_change_step(self):
        """warning() 不改变当前步骤。"""
        reporter = ProgressReporter(total_steps=10)
        reporter.advance("第一步")
        assert reporter.current_step == 1
        reporter.warning("警告消息")
        assert reporter.current_step == 1  # 不变

    def test_error_does_not_change_step(self):
        """error() 不改变当前步骤。"""
        reporter = ProgressReporter(total_steps=10)
        reporter.advance("第一步")
        assert reporter.current_step == 1
        reporter.error("错误消息")
        assert reporter.current_step == 1  # 不变


@pytest.mark.unit
class TestProgressReporterThreadSafety:
    """验证线程安全。"""

    def test_concurrent_advance(self):
        """多线程同时 advance 不会导致数据竞争。"""
        reporter = ProgressReporter(total_steps=100)
        events: list[ProgressEvent] = []
        reporter.on_progress(events.append)

        def worker() -> None:
            for _ in range(50):
                reporter.advance("并发步骤")

        threads = [
            threading.Thread(target=worker)
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有回调都被调用
        assert len(events) == 200  # 4 线程 × 50 次

    def test_concurrent_stage_operations(self):
        """多线程阶段操作不会崩溃。"""
        reporter = ProgressReporter(total_steps=10)

        def stage_worker() -> None:
            for i in range(20):
                stage = f"stage_{i}"
                reporter.stage_start(stage)
                reporter.stage_done(stage)

        threads = [
            threading.Thread(target=stage_worker)
            for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 不应崩溃
        assert reporter.current_step == 0


@pytest.mark.unit
class TestProgressReporterAdvance:
    """验证 advance 递增和回调接收完整事件。"""

    def test_advance_increments_step(self):
        """每次 advance 步骤 +1。"""
        reporter = ProgressReporter(total_steps=5)
        reporter.advance("a")
        reporter.advance("b")
        reporter.advance("c")
        assert reporter.current_step == 3

    def test_advance_events_have_correct_step_numbers(self):
        """advance 发出的事件 step 字段递增。"""
        reporter = ProgressReporter(total_steps=5)
        events: list[ProgressEvent] = []
        reporter.on_progress(events.append)
        reporter.advance("一")
        reporter.advance("二")
        reporter.advance("三")
        assert [e.step for e in events] == [1, 2, 3]

    def test_advance_pct_progression(self):
        """advance 的 pct 逐步增长到 100%。"""
        reporter = ProgressReporter(total_steps=4)
        pcts: list[float] = []
        reporter.on_progress(lambda e: pcts.append(e.pct))
        for msg in ["a", "b", "c", "d"]:
            reporter.advance(msg)
        assert pcts == [25.0, 50.0, 75.0, 100.0]
