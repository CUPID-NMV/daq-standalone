# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import re
p = ROOT + "/config/run-local.toml"; t = open(p).read()
for k, v in [("ChannelList", "[16, 17, 18]"),
             ("DCOffset", "0xB000   # SiPM (positivi). Con i PMT: lista parallela a ChannelList, 0x7000 per i PMT"),
             ("SoftwareTrigger", "true"), ("SelfTrigger", "false"),
             ("ExternalTrigger", "false"), ("SaveData", "true"), ("NEvents", "100000"), ("PostTriggerSize", "10")]:
    t, n = re.subn(r"(?m)^%s\s*=.*$" % k, "%s = %s" % (k, v), t, count=1)
    assert n == 1, k
open(p, "w").write(t)
print("ok")
