"""空间插件参数发现脚本。

连接到正在运行的 REAPER，为每个空间插件创建临时轨道、
加载插件、枚举所有参数名/范围/当前值，打印为 PLUGIN_REGISTRY 格式。

用法：
    python tools/discover_spatial_params.py              # 发现所有空间插件
    python tools/discover_spatial_params.py --verbose    # 打印所有参数细节
    python tools/discover_spatial_params.py --plugin "Little Plate"  # 单个插件
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# ── 需要发现的空间插件列表 ──────────────────────────────────────
# 格式: (用于发现的名字, 注册表键名建议)
TARGET_PLUGINS: list[tuple[str, str]] = [
    # 混响
    ("Little Plate", "VST3: Little Plate (Soundtoys)"),
    ("ValhallaPlate", "ValhallaPlate (Valhalla DSP)"),
    ("ValhallaRoom", "ValhallaRoom (Valhalla DSP)"),
    ("ValhallaVintageVerb", "ValhallaVintageVerb (Valhalla DSP)"),
    ("LX480", "VST3: LX480 (Relab Development)"),
    ("FabFilter Pro-R", "VST3: FabFilter Pro-R (FabFilter)"),
    ("EMT 140", "UAD EMT 140 (Universal Audio)"),
    ("Supernova", "VST3: Supernova (Valhalla DSP)"),
    ("REV6000", "VST3: REV6000 (Relab Development)"),
    ("Seventh Heaven", "Seventh Heaven Professional (LiquidSonics)"),
    ("Cinematic Rooms", "Cinematic Rooms Professional (LiquidSonics)"),
    ("Lustrous Plates", "Lustrous Plates (LiquidSonics)"),
    # 延迟
    ("EchoBoy", "VST3: EchoBoy (Soundtoys)"),
    ("ValhallaDelay", "ValhallaDelay (Valhalla DSP)"),
]


def extract_reaper_string(result) -> str:
    """从 reapy/RPR 返回值中提取字符串。"""
    if isinstance(result, str):
        return result
    if isinstance(result, bytes):
        return result.decode("utf-8", errors="replace")
    if isinstance(result, (tuple, list)):
        for value in reversed(result):
            if isinstance(value, str) and value.strip() and not value.startswith("("):
                return value
            if isinstance(value, bytes) and value.strip():
                return value.decode("utf-8", errors="replace")
    return ""


def discover_plugin(api, track, search_name: str, verbose: bool = False) -> dict | None:
    """在 track 上加载 *search_name* 并枚举所有参数。

    返回:
        {
            "fx_name": "VST3: Little Plate (Soundtoys)",  # REAPER 返回的全名
            "search_name": "Little Plate",                  # 用于搜索的名字
            "param_count": 5,
            "params": [
                {"index": 0, "name": "Decay", "value": 0.5, "min": 0.0, "max": 1.0},
                ...
            ],
        }
    """
    idx = api.TrackFX_AddByName(track, search_name, False, 1)
    if idx < 0:
        log.warning("  ✗ 未找到插件: %s", search_name)
        return None

    # 获取 REAPER 返回的实际插件名
    raw_name = api.TrackFX_GetFXName(track, idx, "", 256)
    fx_name = extract_reaper_string(raw_name) or search_name

    log.info("  ✓ 已加载: %s (index=%d)", fx_name, idx)

    n_params = api.TrackFX_GetNumParams(track, idx)
    log.info("    参数数量: %d", n_params)

    params = []
    for pi in range(n_params):
        raw_pname = api.TrackFX_GetParamName(track, idx, pi, "", 256)
        pname = extract_reaper_string(raw_pname)
        if not pname:
            continue

        # 获取参数范围和当前值
        # TrackFX_GetParamEx(track, fx, param, minvalOut, maxvalOut, midvalOut)
        ret = api.TrackFX_GetParamEx(track, idx, pi, 0.0, 0.0, 0.0)
        if isinstance(ret, (tuple, list)) and len(ret) >= 6:
            value = ret[3] if ret[3] is not None else -1.0
            pmin = ret[4] if ret[4] is not None else 0.0
            pmax = ret[5] if ret[5] is not None else 1.0
        else:
            value, pmin, pmax = -1.0, 0.0, 1.0

        params.append({
            "index": pi,
            "name": pname,
            "value": round(value, 4) if value is not None else -1.0,
            "range": [round(pmin, 4), round(pmax, 4)],
        })

        if verbose:
            log.info("    [%3d] %-30s = %.4f  [%.2f, %.2f]",
                     pi, pname, value, pmin, pmax)

    # 清理: 删除临时加载的插件
    api.TrackFX_Delete(track, idx)

    return {
        "fx_name": fx_name,
        "search_name": search_name,
        "param_count": n_params,
        "params": params,
    }


def generate_registry_entry(result: dict) -> str:
    """根据发现结果生成 PLUGIN_REGISTRY 条目。"""
    lines = []
    ftype = "reverb"
    fx_lower = result["fx_name"].lower()
    if any(kw in fx_lower for kw in ("delay", "echoboy")):
        ftype = "delay"

    lines.append(f'"{result["fx_name"]}": {{')
    lines.append(f'    "type": "{ftype}",')
    lines.append('    "params": {')

    for p in result["params"]:
        pmin, pmax = p["range"]
        # 判断参数类型
        if abs(pmin) < 0.001 and abs(pmax - 1.0) < 0.001:
            # 可能是标准化参数 (0-1)，标记为 pass-through
            lines.append(f'        "{p["name"]}": {{"range": (0.0, 1.0), "curve": "linear"}},  # norm')
        elif pmin < -60:
            # 可能是 dB 范围
            lines.append(f'        "{p["name"]}": {{"range": ({pmin}, {pmax}), "curve": "linear"}},  # dB?')
        elif pmax <= 10 and pmin >= 0:
            # 小范围：可能是秒或比例
            lines.append(f'        "{p["name"]}": {{"range": ({pmin}, {pmax}), "curve": "linear"}},')
        else:
            lines.append(f'        "{p["name"]}": {{"range": ({pmin}, {pmax}), "curve": "linear"}},')

    lines.append("    },")
    lines.append("},")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="发现空间插件参数")
    parser.add_argument("--plugin", type=str, help="只发现指定插件")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印每个参数详情")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    # 连接 REAPER
    import reapy
    try:
        reapy.connect()
        api = reapy.reascript_api
        log.info("✓ 已连接到 REAPER v%s", reapy.get_reaper_version())
    except Exception as e:
        log.error("✗ 无法连接 REAPER: %s", e)
        log.error("  请确保 REAPER 正在运行且 reapy 扩展已安装")
        sys.exit(1)

    # 创建临时轨道
    api.PreventUIRefresh(1)
    api.InsertTrackAtIndex(0, True)
    track = api.GetTrack(0, 0)

    plugins = TARGET_PLUGINS
    if args.plugin:
        plugins = [(args.plugin, args.plugin)]
        # 同时保留完整列表中的注册表键名
        for search, registry in TARGET_PLUGINS:
            if args.plugin.lower() in search.lower():
                plugins = [(search, registry)]
                break

    results = []
    all_registry = []

    for search_name, registry_name in plugins:
        log.info("发现: %s", search_name)
        result = discover_plugin(api, track, search_name, verbose=args.verbose)
        if result:
            result["registry_key"] = registry_name
            results.append(result)
            all_registry.append(generate_registry_entry(result))
        log.info("")

    # 清理: 删除临时轨道
    api.DeleteTrack(track)
    api.PreventUIRefresh(-1)

    # 输出
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("\n" + "=" * 70)
        print("PLUGIN_REGISTRY 条目 (复制到 normalize.py):")
        print("=" * 70 + "\n")
        for entry in all_registry:
            print(entry)
            print()

        # 摘要
        print("=" * 70)
        print("摘要:")
        print("=" * 70)
        for r in results:
            print(f"  {r['fx_name']}: {r['param_count']} 个参数")
        print(f"\n成功: {len(results)}/{len(plugins)}")

    # 保存到文件
    out_path = _PROJECT_ROOT / "tools" / "discovered_params.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, ensure_ascii=False, indent=2, fp=f)
    log.info("\n详细结果已保存到: %s", out_path)


if __name__ == "__main__":
    main()
