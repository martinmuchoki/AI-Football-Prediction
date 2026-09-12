import csv
from pathlib import Path
from app.services.high_confidence_selector import select_prediction, run_selector_csv

def test_high_confidence():
    d = select_prediction(0.70, 0.20, 0.10)
    assert d.label == "HIGH_CONFIDENCE"
    assert d.prediction == "H"
    assert d.publish is True

def test_pass():
    d = select_prediction(0.50, 0.30, 0.20)
    assert d.label == "PASS"
    assert d.publish is False

def test_csv(tmp_path: Path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    with src.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["fixture_id","bookmaker_open_p_home","bookmaker_open_p_draw","bookmaker_open_p_away"])
        w.writeheader()
        w.writerow({"fixture_id":"1","bookmaker_open_p_home":"0.70","bookmaker_open_p_draw":"0.20","bookmaker_open_p_away":"0.10"})
        w.writerow({"fixture_id":"2","bookmaker_open_p_home":"0.50","bookmaker_open_p_draw":"0.30","bookmaker_open_p_away":"0.20"})
    r = run_selector_csv(input_path=str(src), output_path=str(out))
    assert r["high_confidence"] == 1
    assert r["pass"] == 1
    assert r["final_holdout_touched"] is False
