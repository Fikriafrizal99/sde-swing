from __future__ import annotations

import pytest

from modules.ai_interpretation.watchlist.validator import validate_numbers, validate_response


def test_validator_allows_billion_abbreviation_for_exact_nominal():
    context = {"facts": {"broker_net_flow": 42_600_000_000}}
    validate_numbers("Net flow masih positif Rp42,6 miliar.", context)


def test_validator_allows_fraction_to_percent_when_percent_is_explicit():
    context = {"facts": {"buyer_concentration": 0.7502}}
    validate_numbers("Konsentrasi buyer berada di 75,02%.", context)


def test_validator_allows_indonesian_price_grouping():
    context = {"facts": {"last_price": 3120, "entry_low": 3050, "entry_high": 3100}}
    validate_numbers("Harga 3.120 masih di atas entry 3.050–3.100.", context)


def test_validator_rejects_new_unrelated_level():
    context = {"facts": {"last_price": 3120, "entry_low": 3050, "entry_high": 3100}}
    with pytest.raises(ValueError, match="unsupported number"):
        validate_numbers("Saya melihat support baru di 2.975.", context)


def test_scaled_nominal_does_not_authorize_plain_price_level():
    context = {"facts": {"broker_net_flow": 42_600_000_000}}
    with pytest.raises(ValueError, match="unsupported number"):
        validate_numbers("Menurut saya support berada di 42,6.", context)


def test_response_validator_preserves_natural_paragraph_breaks():
    context = {"facts": {"last_price": 3120, "entry_low": 3050}}
    analysis, conclusion = validate_response(
        {
            "analysis": (
                "Menurut saya harga 3.120 masih menarik untuk diamati karena struktur yang diberikan SDE tetap terjaga.\n\n"
                "Namun saya lebih nyaman menunggu respons mendekati 3.050 daripada mengejar harga, karena plan resmi tetap menjadi batas eksekusi."
            ),
            "conclusion": "Saya memilih menunggu konfirmasi sesuai plan SDE.",
        },
        context,
    )

    assert "\n\n" in analysis
    assert conclusion == "Saya memilih menunggu konfirmasi sesuai plan SDE."
