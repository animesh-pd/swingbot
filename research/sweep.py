import copy, itertools, os, sys, time, yaml, pandas as pd, pickle
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # project root
sys.path.insert(0, HERE)
from swingbot.backtest import load_history, run_backtest
cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
tickers = set(cfg["shares"]["watchlist"]) | set(cfg["options"]["watchlist"])
bars, earn = load_history(tickers, 2.0)
rows=[]
t0=time.time()
for stop,hold,score,(mp,pct) in itertools.product([0.07,0.10,0.15],[30,60,120],[3,4],[(3,.33),(5,.20)]):
    c=copy.deepcopy(cfg)
    c["capital"].update(shares_bucket=5000, options_bucket=0); c["options"]["max_positions"]=0
    c["shares"].update(stop_loss_pct=stop,max_hold_days=hold,min_signal_score=score,max_positions=mp,max_position_pct=pct)
    r=run_backtest(c,bars,earn,2.0)
    eq=pd.Series({d:v for d,v in r["curve"]}); eq.index=pd.to_datetime(eq.index)
    mid=eq.index[0]+pd.Timedelta(days=365)
    y1=eq[eq.index<=mid].iloc[-1]/5000-1; y2=eq.iloc[-1]/eq[eq.index<=mid].iloc[-1]-1
    m=eq.groupby(eq.index.to_period("M")).last().pct_change().dropna()
    dd=((eq.cummax()-eq)/eq.cummax()).max()
    rows.append(dict(stop=stop,hold=hold,score=score,mp=mp,pct=pct,total=eq.iloc[-1]/5000-1,y1=y1,y2=y2,dd=dd,up=int((m>0).sum()),n=len(r["trades"])))
    print(rows[-1], round(time.time()-t0), flush=True)
df=pd.DataFrame(rows); df.to_csv("sweep.csv",index=False)
print(df.sort_values("total",ascending=False).to_string())
