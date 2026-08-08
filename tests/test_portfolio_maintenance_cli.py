from pathlib import Path

from modules.analytics.outcome_tracker import make_parser


ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "maintenance" / "RECORD_PORTFOLIO_BUY.bat"


def test_portfolio_parser_accepts_db_before_action():
    args = make_parser().parse_args(
        [
            "portfolio",
            "--db",
            "data/database/sde_swing_history.db",
            "record-buy",
            "--symbol",
            "BNBR",
            "--quantity",
            "575",
            "--price",
            "105",
            "--buy-date",
            "2026-08-06",
        ]
    )
    assert args.command == "portfolio"
    assert args.portfolio_action == "record-buy"
    assert args.db.endswith("sde_swing_history.db")


def test_portfolio_maintenance_bat_uses_parent_db_argument_for_buy_and_sell():
    text = BAT.read_text(encoding="utf-8")
    assert "portfolio --db data\\database\\sde_swing_history.db record-buy" in text
    assert "portfolio --db data\\database\\sde_swing_history.db record-sell" in text
    assert "portfolio record-buy --db" not in text
    assert "portfolio record-sell --db" not in text
