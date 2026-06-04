"""
伴奏处理引擎 — 伴奏总线压缩、频率互让和人声避让。

即使伴奏是立体声混音文件，也提供基础的总线处理和频率协调。
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ── 流派压缩预设 ────────────────────────────────────────────────

_GENRE_COMPRESSION = {
    "rock": {
        "plugin": "ReaComp",
        "threshold_db": -18.0,
        "ratio": 4.0,
        "attack_ms": 3.0,
        "release_ms": 80.0,
        "knee_db": 3.0,
        "makeup_db": 3.0,
    },
    "electronic": {
        "plugin": "ReaComp",
        "threshold_db": -16.0,
        "ratio": 4.0,
        "attack_ms": 5.0,
        "release_ms": 100.0,
        "knee_db": 4.0,
        "makeup_db": 2.5,
    },
    "pop": {
        "plugin": "ReaComp",
        "threshold_db": -20.0,
        "ratio": 3.0,
        "attack_ms": 10.0,
        "release_ms": 120.0,
        "knee_db": 6.0,
        "makeup_db": 2.0,
    },
    "folk": {
        "plugin": "ReaComp",
        "threshold_db": -22.0,
        "ratio": 2.0,
        "attack_ms": 15.0,
        "release_ms": 150.0,
        "knee_db": 8.0,
        "makeup_db": 1.5,
    },
    "ballad": {
        "plugin": "ReaComp",
        "threshold_db": -22.0,
        "ratio": 2.0,
        "attack_ms": 20.0,
        "release_ms": 200.0,
        "knee_db": 10.0,
        "makeup_db": 1.0,
    },
    "default": {
        "plugin": "ReaComp",
        "threshold_db": -20.0,
        "ratio": 3.0,
        "attack_ms": 10.0,
        "release_ms": 100.0,
        "knee_db": 5.0,
        "makeup_db": 2.0,
    },
}

# ── 频率互让预设 ────────────────────────────────────────────────

_FREQUENCY_POCKET = {
    "vocal_boost": {
        "freq_hz": 3000.0,
        "q": 1.2,
        "band_type": "bell",
    },
    "backing_cut": {
        "freq_hz": 3000.0,
        "q": 1.2,
        "band_type": "bell",
    },
}

# ── ReaComp 参数名映射（参数索引 → 名称） ────────────────────────
# ReaComp 参数布局（REAPER v7）：
#   0: Threshold (dB)
#   1: Ratio
#   2: Attack (ms)
#   3: Release (ms)
#   4: Knee (dB)
#   5: Wet (dB)
#   6: Dry (dB)

_REACOMP_PARAMS = {
    "threshold_db": 0,
    "ratio": 1,
    "attack_ms": 2,
    "release_ms": 3,
    "knee_db": 4,
    "makeup_db": 5,  # Wet = makeup gain in ReaComp
}

_REACOMP_PARAM_RANGES = {
    "threshold_db": (-60.0, 0.0),       # normalized range maps to -60..0 dB
    "ratio": (1.0, 100.0),
    "attack_ms": (0.0, 500.0),
    "release_ms": (0.0, 5000.0),
    "knee_db": (0.0, 24.0),
    "makeup_db": (-60.0, 24.0),          # Wet knob
}

# ── ReaEQ 参数布局 ──────────────────────────────────────────────
# band_type 对应的 ReaEQ type 值:
#   0 = Low Shelf, 1 = Band, 2 = High Shelf, 3 = Low Pass, 4 = High Pass

_REAEQ_BAND_TYPES = {
    "bell": 1,
    "high_shelf": 2,
    "low_shelf": 0,
    "hp": 4,
    "lp": 3,
}

# ReaEQ 每 band 参数偏移（band 0 freq 从参数 2 开始，每 band 3 个参数: freq, gain, q）
# Band 0: param 2=freq, 3=gain, 4=q
# Band 1: param 5=freq, 6=gain, 7=q ... 以此类推


class BackingProcessor:
    """伴奏处理器 — 总线压缩 + 频率协调。

    用法::

        processor = BackingProcessor(bridge, fx_manager)
        processor.apply_glue_compression(track_idx=1, genre="rock")
        processor.apply_frequency_pocket(vocal_idx=0, backing_idx=1)
    """

    def __init__(self, bridge, fx_manager):
        """初始化伴奏处理器。

        Args:
            bridge: ReaperBridge 实例，用于底层 API 访问。
            fx_manager: FxManager 实例，用于插件添加和参数控制。
        """
        self._bridge = bridge
        self._fx = fx_manager

    # ── 总线压缩 ──────────────────────────────────────────────

    def apply_glue_compression(self, track_idx: int, genre: str = "pop") -> dict:
        """在伴奏轨道上应用总线压缩（Glue Compression）。

        参数基于流派选择：
        - rock/electronic: 较高压缩比 (4:1)，较快启动
        - folk/ballad: 较低压缩比 (2:1)，较慢启动
        - pop: 中等设置 (3:1)

        Args:
            track_idx: 伴奏轨道索引。
            genre: 流派名称，决定压缩参数。未知流派回退到 "default"。

        Returns:
            dict with "success", "plugin", "settings" keys。
            - success: bool — 是否成功添加并配置压缩器。
            - plugin: str — 使用的插件名称。
            - settings: dict — 实际应用的压缩参数。
        """
        preset = _GENRE_COMPRESSION.get(genre, _GENRE_COMPRESSION["default"])
        plugin_name = preset["plugin"]

        # 添加压缩器插件
        fx_index = self._fx.add(track_idx, plugin_name)
        if fx_index < 0:
            log.warning(
                "无法在轨道 %d 上添加 %s（流派: %s）",
                track_idx, plugin_name, genre,
            )
            return {
                "success": False,
                "plugin": plugin_name,
                "settings": {},
            }

        # 配置压缩参数
        settings = {}
        for param_key, param_idx in _REACOMP_PARAMS.items():
            raw_value = preset[param_key]
            # 将原始值映射到 0.0-1.0 的归一化范围
            normalized = self._normalize_param(raw_value, param_key)
            ok = self._fx.set_param(track_idx, fx_index, param_idx, normalized)
            if ok:
                settings[param_key] = raw_value
            else:
                log.debug(
                    "设置 %s 参数 %s (index=%d) 失败",
                    plugin_name, param_key, param_idx,
                )

        log.info(
            "总线压缩已应用: 轨道 %d, 流派 %s, 压缩比 %.1f:1, 阈值 %.1f dB",
            track_idx, genre, preset["ratio"], preset["threshold_db"],
        )
        return {
            "success": True,
            "plugin": plugin_name,
            "settings": settings,
        }

    # ── 频率互让 ──────────────────────────────────────────────

    def apply_frequency_pocket(self, vocal_idx: int, backing_idx: int,
                               amount_db: float = 2.0) -> dict:
        """频率互让（Frequency Pocket）— 人声与伴奏在关键频段协调。

        在 2-5kHz（默认 3kHz）范围对人声做轻微提升，同时对伴奏做对应衰减，
        让人声在混音中更清晰而不需要提高整体音量。

        Args:
            vocal_idx: 人声轨道索引。
            backing_idx: 伴奏轨道索引。
            amount_db: 调整幅度（1-3 dB），默认 2.0 dB。

        Returns:
            dict with "success", "vocal_boost", "backing_cut" keys。
            - success: bool
            - vocal_boost: dict | None — 人声轨道 EQ 提升结果。
            - backing_cut: dict | None — 伴奏轨道 EQ 衰减结果。
        """
        amount_db = max(1.0, min(3.0, amount_db))

        vocal_config = _FREQUENCY_POCKET["vocal_boost"]
        backing_config = _FREQUENCY_POCKET["backing_cut"]

        # ── 人声轨道：轻微提升 ────────────────────────────────
        vocal_result = self._add_eq_band(
            track_idx=vocal_idx,
            freq_hz=vocal_config["freq_hz"],
            gain_db=amount_db,
            q=vocal_config["q"],
            band_type=vocal_config["band_type"],
        )

        # ── 伴奏轨道：对应衰减 ────────────────────────────────
        backing_result = self._add_eq_band(
            track_idx=backing_idx,
            freq_hz=backing_config["freq_hz"],
            gain_db=-amount_db,
            q=backing_config["q"],
            band_type=backing_config["band_type"],
        )

        success = (
            vocal_result is not None
            and vocal_result.get("fx_added", False)
            and backing_result is not None
            and backing_result.get("fx_added", False)
        )

        if success:
            log.info(
                "频率互让已应用: 人声轨道 %d (+%.1f dB @ %.0f Hz), "
                "伴奏轨道 %d (-%.1f dB @ %.0f Hz)",
                vocal_idx, amount_db, vocal_config["freq_hz"],
                backing_idx, amount_db, backing_config["freq_hz"],
            )
        else:
            log.warning(
                "频率互让部分失败: vocal=%s, backing=%s",
                vocal_result, backing_result,
            )

        return {
            "success": success,
            "vocal_boost": vocal_result,
            "backing_cut": backing_result,
        }

    # ── 内部辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _normalize_param(raw_value: float, param_key: str) -> float:
        """将物理参数值映射到 0.0-1.0 归一化范围。

        Args:
            raw_value: 物理单位的原始值（如 -18.0 dB）。
            param_key: 参数键名，用于查找范围。

        Returns:
            float: 0.0-1.0 之间的归一化值。
        """
        if param_key not in _REACOMP_PARAM_RANGES:
            return 0.5
        lo, hi = _REACOMP_PARAM_RANGES[param_key]
        if hi == lo:
            return 0.5
        normalized = (raw_value - lo) / (hi - lo)
        return max(0.0, min(1.0, normalized))

    def _add_eq_band(self, track_idx: int, freq_hz: float, gain_db: float,
                     q: float, band_type: str = "bell") -> dict | None:
        """在轨道上添加 EQ 并配置一个频段。

        如果轨道上已有 ReaEQ，则复用第一个 ReaEQ；否则添加新的。

        Args:
            track_idx: 轨道索引。
            freq_hz: 中心频率（Hz）。
            gain_db: 增益/衰减量（dB），正值为提升，负值为衰减。
            q: Q 值（带宽）。
            band_type: EQ 频段类型（"bell", "high_shelf", "low_shelf"）。

        Returns:
            dict with "eq_index", "band_num", "freq_hz", "gain_db", "q",
            "band_type", "fx_added" keys；失败返回 None。
        """
        # 查找或添加 ReaEQ
        eq_index = self._find_or_add_reaeq(track_idx)
        if eq_index < 0:
            log.warning("无法在轨道 %d 上添加 ReaEQ", track_idx)
            return None

        fx_added = eq_index >= 0

        # 确定使用哪个 band（0 或 1 等）
        # 简化实现：使用 band 0
        band_num = 0
        reaeq_type = _REAEQ_BAND_TYPES.get(band_type, 1)

        # ReaEQ band 参数偏移计算
        # Band 0: param 2 (freq), param 3 (gain), param 4 (q)
        # 但 ReaEQ 的 param 布局是：
        #   0: band 0 enabled
        #   1: band 0 type
        #   2: band 0 freq
        #   3: band 0 gain
        #   4: band 0 Q
        #   5: band 1 enabled, etc.
        base_param = band_num * 5

        # 启用 band
        self._fx.set_param(track_idx, eq_index, base_param + 0, 1.0)
        # 设置 type
        self._fx.set_param(track_idx, eq_index, base_param + 1,
                           reaeq_type / 4.0)  # type 值归一化到 0-1

        # 设置频率（ReaEQ freq range: 20Hz - 24000Hz）
        freq_normalized = self._normalize_freq(freq_hz)
        self._fx.set_param(track_idx, eq_index, base_param + 2, freq_normalized)

        # 设置增益（ReaEQ gain range: -18..+18 dB）
        gain_normalized = (gain_db + 18.0) / 36.0
        gain_normalized = max(0.0, min(1.0, gain_normalized))
        self._fx.set_param(track_idx, eq_index, base_param + 3, gain_normalized)

        # 设置 Q（ReaEQ Q range: 0.05 - 8.0）
        q_normalized = (q - 0.05) / (8.0 - 0.05)
        q_normalized = max(0.0, min(1.0, q_normalized))
        self._fx.set_param(track_idx, eq_index, base_param + 4, q_normalized)

        return {
            "eq_index": eq_index,
            "band_num": band_num,
            "freq_hz": freq_hz,
            "gain_db": gain_db,
            "q": q,
            "band_type": band_type,
            "fx_added": fx_added,
        }

    @staticmethod
    def _normalize_freq(freq_hz: float) -> float:
        """将对数频率归一化到 0.0-1.0 范围（20Hz - 24kHz）。"""
        import math
        lo = math.log10(20.0)
        hi = math.log10(24000.0)
        val = math.log10(max(20.0, min(24000.0, freq_hz)))
        normalized = (val - lo) / (hi - lo)
        return max(0.0, min(1.0, normalized))

    def _find_or_add_reaeq(self, track_idx: int) -> int:
        """在轨道上查找已有 ReaEQ，找不到则添加新的。

        Args:
            track_idx: 轨道索引。

        Returns:
            int: FX 索引（已存在或新添加的），失败返回 -1。
        """
        chain = self._fx.get_chain(track_idx)
        for fx_info in chain:
            if "ReaEQ" in fx_info.get("name", ""):
                return fx_info["index"]
        # 未找到 → 添加新的
        return self._fx.add(track_idx, "ReaEQ")

    # ── 流派预设查询 ──────────────────────────────────────────

    @staticmethod
    def get_compression_preset(genre: str) -> dict:
        """获取指定流派的压缩预设，不执行任何操作。

        Args:
            genre: 流派名称。

        Returns:
            dict: 压缩参数字典；未知流派返回 "default" 预设。
        """
        return dict(_GENRE_COMPRESSION.get(genre, _GENRE_COMPRESSION["default"]))

    @staticmethod
    def supported_genres() -> list[str]:
        """返回已配置压缩预设的流派列表（不含 "default"）。"""
        return [g for g in _GENRE_COMPRESSION if g != "default"]
