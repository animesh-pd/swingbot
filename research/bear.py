import yfinance as yf, pandas as pd, numpy as np, logging
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
px=yf.download(["SPY","TLT","GLD","IEF"],start="2005-01-01",auto_adjust=True,progress=False)["Close"].dropna()
r=px.pct_change().fillna(0)
sig=(px.SPY>px.SPY.rolling(200).mean()).shift(1).fillna(False)   # trade next day, no look-ahead
cash=0.0
strats={
 "SPY buy&hold": r.SPY,
 "SPY above 200d, else CASH": np.where(sig,r.SPY,cash),
 "SPY above 200d, else SHORT SPY": np.where(sig,r.SPY,-r.SPY),
 "SPY above 200d, else TLT bonds": np.where(sig,r.SPY,r.TLT),
 "SPY above 200d, else GOLD": np.where(sig,r.SPY,r.GLD),
}
start=px.index[260]
def per(s,a,b):
    s=s.loc[a:b]; return (1+s).prod()-1
print(f"{'strategy':34}{'CAGR':>7}{'maxDD':>7}{'2008':>8}{'Q1-20':>8}{'2022':>8}{'mo up':>7}")
for k,v in strats.items():
    s=pd.Series(v,index=px.index).loc[start:]
    eq=(1+s).cumprod(); yrs=len(s)/252
    m=(1+s).groupby(s.index.to_period("M")).prod()-1
    print(f"{k:34}{eq.iloc[-1]**(1/yrs)-1:7.1%}{(1-eq/eq.cummax()).max():7.0%}{per(s,'2008-01-01','2008-12-31'):8.1%}{per(s,'2020-02-19','2020-03-31'):8.1%}{per(s,'2022-01-01','2022-12-31'):8.1%}{(m>0).mean():7.0%}")
