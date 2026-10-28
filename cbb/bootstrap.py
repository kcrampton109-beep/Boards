"""Download the CBB history the board is built from and derive what the engine needs (cloud setup, or after GitHub's
cache drops it). Kept in the Actions cache (cbb/data/), never in the public repo (third-party data).

  1. Action Network + ESPN daily scoreboards for every season from 2021-22 to last season (research/fetch.py, no key;
     ~35 min from scratch, files already saved are skipped)
  2. games + teams tables and the Action -> ESPN team map (research/build.py)
  3. both rating models over those seasons (research/model.py): end-of-season ratings = this season's preseason prior
  4. hist.json: each model's scoring level / home court at the end of each season, and the mid-major conference list
"""
import datetime as dt, json, pathlib, subprocess, sys
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "research"))
import build, model  # noqa: E402
from engine import MODELS, CONF_NAME, P6, season_of  # noqa: E402

DATA = ROOT / "data"


def main():
    season = season_of(dt.date.today())
    last = season - 1
    subprocess.run([sys.executable, str(ROOT / "research" / "fetch.py"), f"2021,{last}"], check=True)
    build.main()
    hist = {"seasons": [2021, last], "mu_h": {}}
    for target, (lam, hl) in MODELS.items():
        _, finals, log = model.run(lam, hl, target=target, seasons=list(range(2021, last + 1)))
        pd.concat([f.assign(season=s) for s, f in finals.items()]).to_parquet(DATA / f"finals_{target}.parquet")
        hist["mu_h"][target] = {str(s): v for s, v in log["mu_h"].items()}
        print(target, "finals", sorted(finals), flush=True)
    # mid-major = the 10 best non-power conferences by average end-of-season net rating (research/evaluate.py)
    f = pd.read_parquet(DATA / "finals_pts.parquet")
    f = f.assign(conf=f["conf_eid"].astype(str).map(CONF_NAME), net=f["O"] + f["D"])
    rank = f[f["conf"].notna() & ~f["conf_eid"].astype(str).isin(P6 | {"21"})].groupby("conf")["net"].mean()
    hist["mid"] = list(rank.sort_values(ascending=False).index[:10])
    (DATA / "hist.json").write_text(json.dumps(hist, indent=1))
    print("mid-majors:", ", ".join(hist["mid"]))


if __name__ == "__main__":
    main()
