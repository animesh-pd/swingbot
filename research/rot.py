import yfinance as yf, pandas as pd, numpy as np, logging
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
U=["XLK","XLF","XLE","XLV","XLY","XLP","XLI","XLB","XLU","QQQ"]
px=yf.download(U+["SPY"],start="2006-01-01",auto_adjust=True,progress=False)["Close"].dropna()
def stats(r,name):
    eq=(1+r).cumprod(); yrs=len(r)/252
    m=(1+r).groupby(r.index.to_period("M")).prod()-1
    dd=(1-eq/eq.cummax()).max()
    y=(1+r).groupby(r.index.year).prod()-1
    print(f"{name:34} CAGR {eq.iloc[-1]**(1/yrs)-1:+.1%} | maxDD {dd:.0%} | months up {(m>0).mean():.0%} | worst mo {m.min():+.1%} | yrs beating SPY: ", end="")
    return y
def rotate(n,lb,sma,rebal=21,start=None):
    ret=px.pct_change().fillna(0)
    mom=px[U].pct_change(lb)
    ok=px[U]>px[U].rolling(sma).mean()
    w=pd.DataFrame(0.0,index=px.index,columns=px.columns)
    cur=pd.Series(0.0,index=px.columns)
    for i in range(max(lb,sma),len(px)):
        if (i-max(lb,sma))%rebal==0:
            s=mom.iloc[i].where(ok.iloc[i]).dropna().nlargest(n)
            cur=pd.Series(0.0,index=px.columns)
            if len(s): cur[s.index]=1/n   # unfilled slots stay in cash
        w.iloc[i]=cur.values
    port=(w.shift(1)*ret).sum(axis=1).iloc[max(lb,sma)+1:]
    return port
spy=px["SPY"].pct_change().iloc[260:]
ys=stats(spy,"SPY buy&hold"); print("-")
base=ys
for n,lb,sma in [(3,126,200),(3,63,100),(2,126,200),(4,126,200),(3,252,200)]:
    p=rotate(n,lb,sma); p=p[p.index>=spy.index[0]]
    y=stats(p,f"rotate top{n} mom{lb} sma{sma}")
    sp=(1+spy.loc[p.index]).groupby(spy.loc[p.index].index.year).prod()-1
    print(f"{(y>sp).sum()}/{len(y)}")
