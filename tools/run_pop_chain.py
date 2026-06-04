"""一次性跑通 pop 空间链：连接 → 建链 → 保存 → 退出"""
import os, subprocess
from hermes_core.bridge import ReaperBridge, _extract_reaper_string
from hermes_core.engine import MixingEngine, _compute_spatial_sends
from hermes_core.track import TrackManager
from hermes_core.fx import FxManager
from hermes_core.send import SendManager
from hermes_core.config import HermesConfig
import reapy

b = ReaperBridge(dialog_killer=True)
b.connect()
api = b.api

# 清理
n = api.CountTracks(0)
for i in range(n - 1, -1, -1):
    api.DeleteTrack(api.GetTrack(0, i))

cfg = HermesConfig.load()
proj_dir = f"{cfg.project_root_expanded}/开发测试/空间效果器/pop-chain"
os.makedirs(proj_dir, exist_ok=True)

eng = MixingEngine.__new__(MixingEngine)
eng._bridge = b
eng._tracks = TrackManager(b)
eng._fx = FxManager(b)
eng._send = SendManager(b)
eng._meta = None

vocal = eng._tracks.create(name="Vocal")
sends = _compute_spatial_sends(
    genre="pop", crest_factor_db=12.0,
    presence_deficit_db=2.0, mud_ratio_db=-3.0, section="verse",
)
result = eng.build_spatial_chain(vocal, sends, genre="pop", bpm=120)
print(f"{len(result)} 条总线:")

for key, info in result.items():
    t = api.GetTrack(0, info["aux_index"])
    fx_name = _extract_reaper_string(api.TrackFX_GetFXName(t, info["fx_index"], "", 256))
    print(f"  {key}: {fx_name}")

rpp = f"{proj_dir}/pop-chain.rpp"
api.Main_SaveProjectEx(0, rpp, 0)
print(f"保存: {rpp} ({os.path.getsize(rpp)} bytes)")

# 安全退出（DialogKiller 处理弹窗）
eng.safe_quit()
print("退出完成")
