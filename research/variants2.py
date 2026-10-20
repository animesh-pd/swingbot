import copy, os, sys, yaml
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # project root
sys.path.insert(0, HERE)
from swingbot.backtest import format_report, load_history, run_backtest

cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
tickers = set(cfg["shares"]["watchlist"]) | set(cfg["options"]["watchlist"])
bars, earn = load_history(tickers, 2.0)

def variant(name, fn):
    c = copy.deepcopy(cfg)
    fn(c)
    r = run_backtest(c, bars, earn, 2.0)
    lines = format_report(r).splitlines()
    print(f"\n===== {name} =====")
    for l in lines:
        if l.startswith(("Final equity", "Max drawdown", "Months up", "Shares:", "Options:")):
            print(l)


def full5000(c):
    c["capital"]["shares_bucket"]=5000; c["capital"]["options_bucket"]=0
    c["options"]["max_positions"]=0
def full5000_3x33(c):
    full5000(c); c["shares"]["max_position_pct"]=0.33
def half(c):
    c["options"]["max_positions"]=0
variant("Shares only, $2,500 bucket (previous)", half)
variant("Shares only, full $5,000 (40%/position)", full5000)
variant("Shares only, full $5,000 (33%/position)", full5000_3x33)
