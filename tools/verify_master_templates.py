"""批量验证四个大师空间模板 — REAPER 集成测试。

关键原则：不关闭工程（会弹窗），只清轨道重建。
全程零弹窗 = 零人工干预。
"""
import os, sys, time
from hermes_core.bridge import ReaperBridge, _extract_reaper_string
from hermes_core.engine import MixingEngine
from hermes_core.track import TrackManager
from hermes_core.fx import FxManager
from hermes_core.send import SendManager
from hermes_core.config import HermesConfig

# 全程开启 DialogKiller
b = ReaperBridge(dialog_killer=True)
if not b.connect():
    print("REAPER 未运行")
    sys.exit(1)

api = b.api
cfg = HermesConfig.load()
root = cfg.project_root_expanded
base_dir = f"{root}/开发测试/空间效果器"

tests = [
    {"name": "CLA",      "template": "cla",      "checks": {"delays": 3, "reverbs": 3, "cross_sends": 9, "total_tracks": 7}},
    {"name": "Hewitt",   "template": "hewitt",   "checks": {"plates": 3, "total_tracks": 4}},
    {"name": "Serban",   "template": "serban",   "checks": {"buses": 5, "total_tracks": 6}},
    {"name": "Townsend", "template": "townsend", "checks": {"total_tracks": 4}},
]

passed = 0
failed = 0

def clear_all_tracks():
    """删除所有轨道 — 替代 close_project"""
    n = api.CountTracks(0)
    for i in range(n - 1, -1, -1):
        tr = api.GetTrack(0, i)
        if tr:
            api.DeleteTrack(tr)

for test in tests:
    name = test["name"]
    template = test["template"]
    checks = test["checks"]

    print(f"\n{'='*50}")
    print(f"{name} 大师模板")
    print(f"{'='*50}")

    try:
        # 清轨道（替代关闭工程）
        clear_all_tracks()

        eng = MixingEngine.__new__(MixingEngine)
        eng._bridge = b
        eng._tracks = TrackManager(b)
        eng._fx = FxManager(b)
        eng._send = SendManager(b)
        eng._meta = None

        vocal = eng._tracks.create(name="Vocal")
        result = eng.apply_master_template(template, vocal, genre="pop", bpm=120)

        # 验证
        ok = True
        for check_key, expected in checks.items():
            if check_key == "total_tracks":
                actual = api.CountTracks(0)
            elif check_key in result:
                actual = len(result[check_key])
            else:
                continue

            status = "✓" if actual == expected else "✗"
            if actual != expected:
                ok = False
            print(f"  {status} {check_key}: {actual} (预期 {expected})")

        # 额外验证
        if name == "CLA":
            cross = len(result.get("cross_sends", []))
            print(f"  {'✓' if cross == 9 else '✗'} 跨发送 delay→reverb: {cross}")
        elif name == "Townsend":
            ld = result.get("left_delay", {})
            rd = result.get("right_delay", {})
            print(f"  {'✓' if ld.get('pan') == -1.0 else '✗'} L Delay 硬左")
            print(f"  {'✓' if rd.get('pan') == 1.0 else '✗'} R Delay 硬右")
        elif name == "Serban":
            sc_count = sum(1 for bi in result.get("buses", {}).values()
                          if bi.get("sidechain_fx", -1) >= 0)
            print(f"  {'✓' if sc_count == 5 else '✗'} Sidechain Pro-C2: {sc_count}/5")

        # 保存到标准目录
        proj_dir = f"{base_dir}/{template}-template"
        os.makedirs(proj_dir, exist_ok=True)
        rpp = f"{proj_dir}/{template}-template.rpp"
        api.Main_SaveProjectEx(0, rpp, 0)
        # Main_SaveProject 不清 dirty flag，所以保存后不关闭工程
        file_size = os.path.getsize(rpp)
        print(f"  保存: {rpp} ({file_size} bytes)")

        if ok:
            passed += 1
        else:
            failed += 1

    except Exception as e:
        print(f"  ✗ 失败: {e}")
        import traceback; traceback.print_exc()
        failed += 1

# 最后清理 — 清轨道
clear_all_tracks()

# 退出 REAPER（DialogKiller 处理弹窗 Sheet）
eng.safe_quit()

print(f"\n{'='*50}")
print(f"结果: {passed} 通过, {failed} 失败")
print(f"工程目录: {base_dir}/")
for d in sorted(os.listdir(base_dir)):
    rpp_path = os.path.join(base_dir, d, f"{d}.rpp")
    if os.path.exists(rpp_path):
        print(f"  ✓ {d}/ ({os.path.getsize(rpp_path)} bytes)")
print(f"{'='*50}")
