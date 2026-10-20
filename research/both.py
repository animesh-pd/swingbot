import copy, os, sys, time, yaml, pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # project root
sys.path.insert(0, HERE)
from swingbot.backtest import load_history, run_backtest
cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
ORIG = cfg["shares"]["watchlist"]
NEW = ["AAPL","MSFT","GOOGL","META","PG","KO","HD","UNH","BAC","WMT","PEP","MRK","CSCO","ORCL","DIS","TXN","HON","LOW","ABT","MCD"]
allt = set(ORIG)|set(NEW)|set(cfg["options"]["watchlist"])|{"SPY"}
YEARS = 4.0
print("downloading...", flush=True)
bars, earn = load_history(allt, YEARS)
t0 = time.time()
def run(label, wl, n, pct, years):
    c = copy.deepcopy(cfg)
    c["capital"].update(shares_bucket=5000, options_bucket=0)
    c["options"]["max_positions"] = 0
    c["shares"].update(watchlist=wl, max_positions=n, max_position_pct=pct)   # stop .10, hold 60, score 3 as in config
    r = run_backtest(c, bars, earn, years)
    eq = pd.Series({d: v for d, v in r["curve"]}); eq.index = pd.to_datetime(eq.index)
    m = eq.groupby(eq.index.to_period("M")).last().pct_change().dropna()
    yr = eq.groupby(eq.index.year).last().pct_change()
    dd = ((eq.cummax()-eq)/eq.cummax()).max()
    tr = r["trades"]
    print(f"{label:34} {years:.0f}y | total {eq.iloc[-1]/5000-1:+.1%} | maxDD {dd:.1%} | months up {(m>0).sum()}/{len(m)} | worst mo {m.min():+.1%} | trades {len(tr)} | SPY {r['bench']['SPY buy&hold']:+.1%} | watchlist B&H {r['bench']['Watchlist equal-weight buy&hold']:+.1%} | {round(time.time()-t0)}s", flush=True)
print("\n== A: position count, original watchlist, 2y ==", flush=True)
for n, pct in [(3,.33),(5,.20),(6,.16),(8,.125)]:
    run(f"orig wl, {n} x {pct:.0%}", ORIG, n, pct, 2.0)
print("\n== B: out-of-sample watchlist, 4y (includes 2022 bear market) ==", flush=True)
for n, pct in [(3,.33),(5,.20),(8,.125)]:
    run(f"NEW wl, {n} x {pct:.0%}", NEW, n, pct, 4.0)
for n, pct in [(3,.33),(5,.20),(8,.125)]:
    run(f"orig wl, {n} x {pct:.0%}", ORIG, n, pct, 4.0)
print("DONE", flush=True)
