"""Quick checks for core.type_beat. Run directly (`python tests/test_type_beat.py`)
or with pytest."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import type_beat as tb  # noqa: E402


def test_extract_artists_basic():
    assert tb.extract_artists('Lil Baby Type Beat "Freeze"') == ["Lil Baby"]
    assert tb.extract_artists("[FREE] Rod Wave Type Beat 2024") == ["Rod Wave"]
    assert tb.extract_artists("(FREE FOR PROFIT) Shy Glizzy TYPE BEAT") == ["Shy Glizzy"]


def test_extract_artists_collab():
    assert tb.extract_artists("Lil Baby x Rod Wave Type Beat") == ["Lil Baby", "Rod Wave"]
    assert tb.extract_artists("Drake & 21 Savage type beat") == ["Drake", "21 Savage"]


def test_extract_artists_rejects_non_type_beat():
    assert tb.extract_artists("My new song (official audio)") == []
    assert tb.extract_artists("") == []


def test_extract_artists_strips_scene_qualifiers():
    assert tb.extract_artists("Noid4l DMV Type Beat") == ["Noid4l"]
    assert tb.extract_artists('[FREE] Sdot Go NY Drill Type Beat "war"') == ["Sdot Go"]
    assert tb.extract_artists("Rob49 x Rod Wave Jersey Club Type Beat") == ["Rob49", "Rod Wave"]
    # a title that is *only* a qualifier has no artist
    assert tb.extract_artists("DMV Type Beat 2025") == []


def test_title_format_detected_from_history():
    entries = [
        tb.TitleEntry('[FREE] Shy Glizzy Type Beat "Uptown"', 1200),
        tb.TitleEntry('[FREE] Xanman Type Beat "Racks"', 800),
        tb.TitleEntry('[FREE] Q Da Fool Type Beat "Wild"', 400),
    ]
    report = tb.analyze(entries, scene="dmv")
    assert report.title_format.prefix == "[FREE] "
    assert report.title_format.quote_hook is True
    assert report.parsed_count == 3
    rendered = report.title_format.render("Lil Dude", "Movie")
    assert rendered == '[FREE] Lil Dude Type Beat "Movie"'


def test_coverage_and_suggestions_exclude_covered():
    entries = [
        tb.TitleEntry("Shy Glizzy Type Beat", 5000),
        tb.TitleEntry("Shy Glizzy Type Beat pt 2", 100),
        tb.TitleEntry("Wale Type Beat", 300),
    ]
    report = tb.analyze(entries, scene="dmv")

    covered = {c.name.lower(): c for c in report.coverage}
    assert covered["shy glizzy"].beat_count == 2
    assert covered["shy glizzy"].total_views == 5100

    suggested = {s.artist.lower() for s in report.suggestions}
    assert "shy glizzy" not in suggested
    assert any("wale" not in s.artist.lower() for s in report.suggestions)
    # a DMV name we never used should show up
    assert any(s.artist == "Rico Nasty" for s in report.suggestions)


def test_pair_suggestion_for_solo_only_artists():
    entries = [
        tb.TitleEntry("Xanman Type Beat", 900),
        tb.TitleEntry("YungManny Type Beat", 850),
    ]
    report = tb.analyze(entries, scene="dmv")
    pairs = [s for s in report.suggestions if s.kind == "pair"]
    assert pairs, "expected a pair idea for two solo-only artists"
    assert " x " in pairs[0].artist


def test_market_scan_surfaces_pairs_and_trending_artists():
    mine = [tb.TitleEntry("Shy Glizzy Type Beat", 1000)]
    market = [
        '[FREE] Sdot Go x Kyle Richh Type Beat "Sidways"',
        'Sdot Go x Kyle Richh Type Beat 2025',
        'FREE Sdot Go Type Beat',
        'Kyle Richh x Sdot Go type beat "jersey"',
        'DThang Type Beat',
        'DThang x Kyle Richh Type Beat',
    ]
    report = tb.analyze(mine, scene="dmv", market_titles=market)

    assert report.market_scanned == 6
    # Kyle Richh + Sdot Go co-occur the most in others' titles
    top_pair = {report.market_pairs[0][0].lower(), report.market_pairs[0][1].lower()}
    assert top_pair == {"kyle richh", "sdot go"}

    reasons = " ".join(s.reason.lower() for s in report.suggestions)
    assert "other channels" in reasons or "recent uploads" in reasons
    # a name that trends outside the seed list still gets suggested
    assert any(s.artist.lower() == "kyle richh" for s in report.suggestions) or any(
        "kyle richh" in s.artist.lower() for s in report.suggestions
    )


def test_market_pair_skipped_if_you_already_made_it():
    mine = [tb.TitleEntry("Kyle Richh x Sdot Go Type Beat", 500)]
    market = ["Kyle Richh x Sdot Go Type Beat"] * 5
    report = tb.analyze(mine, scene="dmv", market_titles=market)
    for a, b, _ in report.market_pairs:
        assert {a.lower(), b.lower()} != {"kyle richh", "sdot go"}


def test_empty_channel_falls_back_to_seed():
    report = tb.analyze([], scene="dmv")
    assert report.parsed_count == 0
    assert report.suggestions
    assert report.suggestions[0].artist in tb.SCENES["dmv"]
    assert report.suggestions[0].titles
    assert report.suggestions[0].titles[0].startswith("[FREE] ")


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run()
