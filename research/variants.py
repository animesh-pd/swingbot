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

def shares_only(c):
    c["options"]["max_positions"] = 0
def single_only(c):
    c["options"]["structure"] = "single_only"
def spread_only(c):
    c["options"]["structure"] = "spread_only"

variant("SHARES ONLY (options max_positions=0)", shares_only)
variant("single_only options", single_only)
variant("spread_only options", spread_only)
