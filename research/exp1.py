import yfinance as yf, pandas as pd, numpy as np, logging
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
U=["XLK","XLF","XLE","XLV","XLY","XLP","XLI","XLB","XLU","QQQ","IWM","EFA","EEM","GLD","TLT","IEF","DBC"]
px=yf.download(U+["SPY"],start="2006-06-01",auto_adjust=True,progress=False)["Close"].dropna()
ret=px.pct_change().fillna(0)
def rotate(n,lb,sma=200,rebal=21):
    mom=px[U].pct_change(lb); ok=px[U]>px[U].rolling(sma).mean()
    w=pd.DataFrame(0.0,index=px.index,columns=px.columns); cur=pd.Series(0.0,index=px.columns); st=max(lb,sma)
    for i in range(st,len(px)):
        if (i-st)%rebal==0:
            s=mom.iloc[i].where(ok.iloc[i]).dropna().nlargest(n); cur=pd.Series(0.0,index=px.columns)
            if len(s): cur[s.index]=1/n
        w.iloc[i]=cur.values
    return (w.shift(1)*ret).sum(axis=1).iloc[st+1:]
start=px.index[260]
spy=ret.SPY.loc[start:]
spy_m=(1+spy).groupby(spy.index.to_period("M")).prod()-1
def rep(name,p):
    p=p.loc[start:]; eq=(1+p).cumprod(); yrs=len(p)/252
    m=(1+p).groupby(p.index.to_period("M")).prod()-1; sm=spy_m.reindex(m.index); dn=sm<0
    y=lambda a,b:(1+p.loc[a:b]).prod()-1
    print(f"{name:26} CAGR {eq.iloc[-1]**(1/yrs)-1:+.1%} | maxDD {(1-eq/eq.cummax()).max():.0%} | mo up {(m>0).mean():.0%} | corr SPY {m.corr(sm):.2f} | SPY-down mo avg {m[dn].mean():+.1%} (SPY {sm[dn].mean():+.1%}) | 2008 {y('2008-01-01','2008-12-31'):+.0%} 2022 {y('2022-01-01','2022-12-31'):+.0%}")
rep("SPY buy&hold",spy)
for n,lb in [(3,252),(4,252),(3,126),(4,126)]:
    rep(f"rotation top{n}, {lb}d mom",rotate(n,lb))
