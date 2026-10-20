import copy, os, sys, time, yaml, pandas as pd, numpy as np
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # project root
sys.path.insert(0, HERE)
from swingbot.backtest import load_history, run_backtest
cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))   # the UPDATED config
ORIG = cfg["shares"]["watchlist"]
MID = ["ADM","CMI","DE","EMR","ETN","FAST","GWW","ITW","MLM","NUE","OXY","PH","PKG","SYY","TSCO","URI","WAB","CTAS","DOV","AME"]
bars, earn = load_history(set(ORIG)|set(MID)|set(cfg["options"]["watchlist"])|{"SPY"}, 4.0)
spy = bars["SPY"]["Close"]
spy_m = spy.groupby(spy.index.to_period("M")).last().pct_change().dropna()
t0=time.time()
def run(label, wl, filt):
    c = copy.deepcopy(cfg); c["shares"]["watchlist"] = wl; c["market_filter"]["enabled"] = filt
    r = run_backtest(c, bars, earn, 4.0)
    eq = pd.Series({d: v for d, v in r["curve"]}); eq.index = pd.to_datetime(eq.index)
    m = eq.groupby(eq.index.to_period("M")).last().pct_change().dropna()
    sm = spy_m.reindex(m.index).dropna(); mm = m.reindex(sm.index)
    dn = sm < 0
    dd = ((eq.cummax()-eq)/eq.cummax()).max()
    print(f"{label:30} total {eq.iloc[-1]/5000-1:+.1%} | maxDD {dd:.1%} | mo up {(m>0).sum()}/{len(m)} | corr to SPY {mm.corr(sm):.2f} | "
          f"in SPY-down months ({dn.sum()}): avg {mm[dn].mean():+.1%} vs SPY {sm[dn].mean():+.1%} | SPY {r['bench']['SPY buy&hold']:+.1%} | {round(time.time()-t0)}s", flush=True)
for lab, wl in (("original watchlist", ORIG), ("mid-cap/industrial list", MID)):
    for f in (False, True):
        run(f"{lab}, filter {'ON ' if f else 'OFF'}", wl, f)
print("DONE", flush=True)
