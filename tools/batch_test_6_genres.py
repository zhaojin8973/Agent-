#!/usr/bin/env python3
"""批量测试 6 个流派 — 每个流派独立工程文件，完整处理 + 渲染 + 保存。

用法: PYTHONPATH=src python tools/batch_test_6_genres.py

前提: REAPER 正在运行
"""
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hermes_core import MixingEngine

# ── 配置 ──────────────────────────────────────────────

TEST_DIR = os.path.expanduser("~/hermes-core/Hermes 测试/望归 贴唱")
VOCAL_FILE = os.path.join(TEST_DIR, "望归 Vocal（测试）.wav")
BACKING_FILE = os.path.join(TEST_DIR, "望归 伴奏（测试）.wav")
MIDI_FILE = os.path.join(TEST_DIR, "速度.mid")

GENRES = [
    "folk",
    "ballad",
    "pop",
    "rock",
    "electronic",
    "chinese_folk_bel_canto",
]

# ── 主逻辑 ──────────────────────────────────────────────


def test_genre(genre: str) -> dict:
    """测试单个流派，返回结果摘要。"""
    result = {
        "genre": genre,
        "started": datetime.now().isoformat(),
        "project_name": f"望归_{genre}",
        "stages": [],
        "error": None,
    }

    print(f"\n{'='*60}")
    print(f"  流派: {genre}")
    print(f"  工程: {result['project_name']}")
    print(f"{'='*60}")

    try:
        with MixingEngine(watchdog=True) as eng:
            # Phase 1: 创建工程
            print(f"\n[1/5] 创建工程...")
            eng.create_project(
                result["project_name"],
                category="贴唱混音",
                sample_rate=48000,
            )
            result["stages"].append("create_project ✅")
            result["project_dir"] = getattr(eng, "_project_path", "") or ""

            # Phase 2: 导入分轨 + 增益分级
            print(f"[2/5] 导入分轨 + 增益分级...")
            eng.prepare_stems(
                [VOCAL_FILE, BACKING_FILE],
                genre=genre,
                vocal_indices=[0],
                backing_indices=[1],
            )
            result["stages"].append("prepare_stems ✅")

            # Phase 3: 应用 Profile
            print(f"[3/5] 应用 {genre} Profile...")
            from hermes_core.profiles import MixingProfile
            profile = MixingProfile.for_genre(genre)
            eng.apply_profile(profile, vocal_track=0, genre=genre)
            result["stages"].append(f"apply_profile ✅ ({len(profile.vocal_chain)} FX)")

            # Phase 4: 后处理平衡
            print(f"[4/5] 后处理平衡...")
            eng.post_fx_balance()
            result["stages"].append("post_fx_balance ✅")

            # Phase 5: 母带 + 渲染
            print(f"[5/5] 母带最终化...")
            final = eng.finalize_master()
            result["stages"].append(f"finalize_master ✅ (LUFS={final.get('integrated_lufs', '?')})")

            # 保存工程
            eng.save_project()
            result["stages"].append("save_project ✅")

            print(f"\n✅ {genre} 完成")
            print(f"   工程目录: {result['project_dir']}")

    except Exception as exc:
        result["error"] = str(exc)
        print(f"\n❌ {genre} 失败: {exc}")
        import traceback
        traceback.print_exc()

    result["finished"] = datetime.now().isoformat()
    return result


def main():
    print("=" * 60)
    print("  Hermes 6 流派批量测试")
    print(f"  开始: {datetime.now().isoformat()}")
    print("=" * 60)

    results = []
    for genre in GENRES:
        r = test_genre(genre)
        results.append(r)
        time.sleep(2)  # 给 REAPER 喘口气

    # ── 汇总 ──────────────────────────────────────────
    print("\n\n" + "=" * 60)
    print("  测试汇总")
    print("=" * 60)
    passed = sum(1 for r in results if r["error"] is None)
    failed = sum(1 for r in results if r["error"] is not None)
    print(f"  通过: {passed}/{len(results)}")
    print(f"  失败: {failed}/{len(results)}")
    print()

    for r in results:
        status = "✅" if r["error"] is None else "❌"
        print(f"  {status} {r['genre']:25s} — {r['project_name']}")
        if r["error"]:
            print(f"      错误: {r['error'][:80]}")
        if "project_dir" in r:
            print(f"      目录: {r['project_dir']}")
        for s in r["stages"]:
            print(f"        {s}")

    print(f"\n  结束: {datetime.now().isoformat()}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
