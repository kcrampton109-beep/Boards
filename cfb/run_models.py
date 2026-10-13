"""Run the three rating models with tuned settings; lag 7 = model blind to the most recent week (leakage check)."""
import sys
import pandas as pd
import model

PARAMS = {"pts": dict(lam=2.0, half_life=6), "ppa": dict(lam=4.0), "sr": dict(lam=4.0)}


def run_all(lag_days=0, seasons=range(2014, 2027)):
    res = [model.run(k, seasons=seasons, lag_days=lag_days, **p) for k, p in PARAMS.items()]
    p = pd.concat(res)
    wide = p.pivot_table(index="game_id", columns="kind",
                         values=["margin_hat", "total_hat", "prior_margin", "n_home", "n_away"])
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    return wide.reset_index(), p


if __name__ == "__main__":
    lag = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    wide, long = run_all(lag)
    suffix = "" if lag == 0 else f"_lag{lag}"
    wide.to_parquet(model.DATA / f"preds{suffix}.parquet")
    long.to_parquet(model.DATA / f"preds_long{suffix}.parquet")
    print("saved", suffix, wide.shape)
