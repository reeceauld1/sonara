"""Turn your uploaded type-beat titles into 'who to make a beat for next'
suggestions.

No AI service is involved — it's all pattern matching:
  1. Read every uploaded video title, keep the ones shaped like a type beat.
  2. Pull the artist name(s) out of the part before "type beat".
  3. Tally how many beats (and views) you have per artist.
  4. Suggest artists from a scene seed list you haven't covered (or barely
     have), plus "pair" ideas from artists you've only made solo beats for.
  5. Render ready-to-paste titles in whatever format you already use
     (detected from your own titles: [FREE] tag, separator, quoted hook).

Scene seed lists live in SCENES below — edit them freely, they're just data.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Sequence

TYPE_BEAT_RE = re.compile(r"\btype\s*beat\b", re.IGNORECASE)
_BRACKET_TAG_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
_LEADING_FREE_RE = re.compile(
    r"^\s*(?:free for profit|free|buy \d+ get \d+ free)\b[\s\-–—:]*", re.IGNORECASE
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:\bx\b|×|&|\+|,|/|\bft\.?\b|\bfeat\.?\b|\bwith\b)\s*", re.IGNORECASE
)
_SEP_LEAD_RE = re.compile(r"^[\s\-–—~/|:•·]+")
_QUOTE_CHARS = "\"'“”‘’"
_FILLER = {"the", "type", "prod", "prod by", "free", "beat", "instrumental"}

# Scene / region / genre words that sit next to "type beat" but aren't a
# person — e.g. "Noid4l DMV Type Beat" -> artist is "Noid4l", not "Noid4l DMV".
_QUALIFIERS = {
    "dmv", "atlanta", "atl", "ny", "nyc", "new york", "uk", "detroit",
    "chicago", "memphis", "jersey", "jersey club", "bronx", "brooklyn",
    "philly", "philadelphia", "west coast", "east coast", "flint", "bay area",
    "houston", "dallas", "cali", "california", "florida", "chiraq",
    "drill", "rage", "trap", "sad", "dark", "hard", "soul", "sample",
    "guitar", "piano", "synth", "bell", "ambient", "afro", "afrobeat",
    "afrobeats", "plugg", "pluggnb", "sexy drill", "sexydrill", "old school",
    "boom bap", "lofi", "lo fi", "rnb", "r&b", "melodic", "storytelling",
    "freestyle", "club", "hyperpop", "emo", "gospel", "orchestral",
}

DEFAULT_HOOKS = [
    "Nightfall", "Uptown", "No Cap", "Trenches",
    "Movie", "Reckless", "Overseas", "Pressure",
]

# Scene seed lists — the pool suggestions are drawn from. Just edit these.
SCENES: dict[str, list[str]] = {
    "dmv": [
        "Shy Glizzy", "Wale", "Rico Nasty", "GoldLink", "IDK", "Fat Trel",
        "Q Da Fool", "Xanman", "YungManny", "Lil Dude", "Sparkheem",
        "Baby 9eez", "No Savage", "Big Flock", "3ohBlack", "Lightshow",
        "Fedd The God", "ABG Neal", "Lil Gray", "1900Rugrat",
        "Brent Faiyaz", "Kelow LaTesha",
    ],
    "atlanta": [
        "Young Thug", "Gunna", "Lil Baby", "Future", "21 Savage",
        "Playboi Carti", "Lil Yachty", "Latto", "Offset", "Quavo",
        "Lil Keed", "Young Nudy",
    ],
}
DEFAULT_SCENE = "dmv"


# --------------------------------------------------------------------- data
@dataclass
class TitleEntry:
    title: str
    views: int = 0


@dataclass
class ArtistCoverage:
    name: str
    beat_count: int
    total_views: int
    best_title: str
    best_views: int


@dataclass
class Suggestion:
    artist: str
    reason: str
    titles: list[str]
    kind: str = "artist"  # "artist" | "pair"


@dataclass
class TitleFormat:
    prefix: str = ""          # e.g. "[FREE] "
    separator: str = " "       # between "Type Beat" and the hook
    quote_hook: bool = True
    sample_hooks: list[str] = field(default_factory=list)

    def render(self, artist: str, hook: str) -> str:
        core = f"{self.prefix}{artist} Type Beat"
        if not hook:
            return core.strip()
        body = f'"{hook}"' if self.quote_hook else hook
        sep = self.separator if self.separator.strip() else " "
        if sep != " ":
            sep = f" {sep.strip()} "
        return f"{core}{sep}{body}".strip()


@dataclass
class MarketScan:
    """What other channels in a scene are posting right now."""
    scanned: int                                        # titles read
    pairs: list[tuple] = field(default_factory=list)    # (name_a, name_b, count), most first
    artists: list[tuple] = field(default_factory=list)  # (name, count), most first


@dataclass
class SuggestionReport:
    coverage: list[ArtistCoverage]
    suggestions: list[Suggestion]
    title_format: TitleFormat
    parsed_count: int
    total_titles: int
    scene: str
    market_scanned: int = 0                                  # other channels' titles read
    market_pairs: list[tuple] = field(default_factory=list)  # (name_a, name_b, count)
    market_artists: list[tuple] = field(default_factory=list)  # (name, count)


@dataclass
class _Parsed:
    artists: list[str]
    prefix: str
    sep: str
    hook: str
    quoted: bool


# ---------------------------------------------------------------- parsing
def _normalize(name: str) -> str:
    n = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    return re.sub(r"\s+", " ", n)


def extract_artists(title: str) -> list[str]:
    """The artist name(s) named before "type beat", or [] if the title isn't
    a type beat. "[FREE] Lil Baby x Rod Wave Type Beat 2024" -> ["Lil Baby",
    "Rod Wave"]."""
    match = TYPE_BEAT_RE.search(title or "")
    if not match:
        return []
    head = title[: match.start()]
    head = _BRACKET_TAG_RE.sub(" ", head)
    head = _LEADING_FREE_RE.sub(" ", head)
    head = _YEAR_RE.sub(" ", head)
    for q in _QUOTE_CHARS:
        head = head.replace(q, " ")
    head = head.strip(" -–—|:~/").strip()
    head = _strip_trailing_qualifiers(head)
    if not head:
        return []

    artists: list[str] = []
    for part in _ARTIST_SPLIT_RE.split(head):
        name = _strip_trailing_qualifiers(" ".join(part.split()).strip(" -–—|:~/."))
        if len(name) < 2 or len(name) > 40:
            continue
        if _normalize(name) in _FILLER or _normalize(name) in _QUALIFIERS:
            continue
        artists.append(name)
    return artists


def _strip_trailing_qualifiers(text: str) -> str:
    """Drop scene/region/genre words hanging off the end of a name, e.g.
    "Noid4l DMV" -> "Noid4l", "Rob49 Jersey Club" -> "Rob49"."""
    words = text.split()
    while words:
        one = _normalize(words[-1])
        two = _normalize(" ".join(words[-2:])) if len(words) >= 2 else ""
        if two and two in _QUALIFIERS:
            words = words[:-2]
        elif one in _QUALIFIERS:
            words = words[:-1]
        else:
            break
    return " ".join(words)


def _parse(title: str) -> Optional[_Parsed]:
    match = TYPE_BEAT_RE.search(title or "")
    if not match:
        return None

    stripped = title.strip()
    tag = _BRACKET_TAG_RE.match(stripped)
    prefix = tag.group(0).upper() if tag else ""

    rest = _YEAR_RE.sub(" ", title[match.end():])
    lead = _SEP_LEAD_RE.match(rest)
    sep = ""
    if lead:
        punct = lead.group(0).strip()
        sep = punct[:1]
        rest = rest[lead.end():]
    rest = rest.strip()

    hook, quoted = "", False
    if rest and rest[0] in _QUOTE_CHARS:
        quoted = True
        body = rest[1:]
        cut = next((i for i, ch in enumerate(body) if ch in _QUOTE_CHARS), len(body))
        hook = body[:cut]
    elif rest:
        cand = re.split(r"[|\[\(]", rest)[0].strip(" -–—~/")
        if 2 <= len(cand) <= 30 and not TYPE_BEAT_RE.search(cand):
            hook = cand
    hook = hook.strip(_QUOTE_CHARS + " ")

    return _Parsed(extract_artists(title), prefix, sep, hook, quoted)


def _build_format(parsed: list[_Parsed]) -> TitleFormat:
    if not parsed:
        return TitleFormat(prefix="[FREE] ", separator=" ", quote_hook=True)

    n = len(parsed)
    threshold = max(2, n * 0.34)

    prefixes = Counter(p.prefix for p in parsed if p.prefix)
    prefix = ""
    if prefixes:
        top, cnt = prefixes.most_common(1)[0]
        if cnt >= threshold:
            prefix = f"{top} "

    seps = Counter(p.sep for p in parsed if p.sep)
    separator = " "
    if seps:
        top, cnt = seps.most_common(1)[0]
        if cnt >= threshold:
            separator = top

    quoted = sum(1 for p in parsed if p.quoted)
    quote_hook = quoted >= max(1, n * 0.4)

    hooks = Counter(p.hook for p in parsed if 2 <= len(p.hook) <= 30)
    sample_hooks = [h for h, _ in hooks.most_common(8)]

    return TitleFormat(prefix, separator, quote_hook, sample_hooks)


# --------------------------------------------------------------- analysis
def _market_stats(titles: Sequence[str]):
    """Artist frequency + name-pair co-occurrence across a bag of external
    titles. Returns (artist_counter, pair_counter, {key: display_name})."""
    artist_counts: Counter = Counter()
    pair_counts: Counter = Counter()
    display: dict[str, str] = {}
    for title in titles:
        keys: list[str] = []
        for raw in extract_artists(title):
            key = _normalize(raw)
            if not key:
                continue
            keys.append(key)
            display.setdefault(key, raw)
            artist_counts[key] += 1
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                pair_counts[tuple(sorted((keys[i], keys[j])))] += 1
    return artist_counts, pair_counts, display


def scan_market(titles: Sequence[str], exclude_pairs: "frozenset|set" = frozenset()) -> MarketScan:
    """Turn a bag of other channels' titles into ranked name pairs + artists.
    `exclude_pairs` is a set of sorted (key_a, key_b) tuples to leave out
    (e.g. combos you've already made)."""
    titles = list(titles)
    counts, pair_counts, display = _market_stats(titles)

    def name_of(key: str) -> str:
        return display.get(key, key.title())

    pairs: list[tuple] = []
    for (k1, k2), n in pair_counts.most_common():
        if n < 2:
            break
        if (k1, k2) in exclude_pairs:
            continue
        pairs.append((name_of(k1), name_of(k2), n))

    artists = [(name_of(k), n) for k, n in counts.most_common()]
    return MarketScan(scanned=len(titles), pairs=pairs, artists=artists)


def analyze(
    entries: Sequence[TitleEntry],
    scene: str = DEFAULT_SCENE,
    limit: int = 12,
    market_titles: Sequence[str] = (),
) -> SuggestionReport:
    entries = list(entries)
    market_titles = list(market_titles)
    parsed_all = [(_parse(e.title), e) for e in entries]
    fmt = _build_format([p for p, _ in parsed_all if p is not None])

    agg: dict[str, dict] = {}
    cooc: Counter = Counter()
    for parsed, entry in parsed_all:
        if not parsed or not parsed.artists:
            continue
        keys: list[str] = []
        for raw in parsed.artists:
            key = _normalize(raw)
            if not key:
                continue
            keys.append(key)
            slot = agg.setdefault(
                key,
                {
                    "display": raw,
                    "beats": 0,
                    "views": 0,
                    "best_title": entry.title,
                    "best_views": -1,
                },
            )
            slot["beats"] += 1
            slot["views"] += max(0, entry.views)
            if entry.views >= slot["best_views"]:
                slot["best_views"] = entry.views
                slot["best_title"] = entry.title
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                cooc[tuple(sorted((keys[i], keys[j])))] += 1

    coverage = [
        ArtistCoverage(
            v["display"], v["beats"], v["views"], v["best_title"], max(0, v["best_views"])
        )
        for v in agg.values()
    ]
    coverage.sort(key=lambda c: (c.beat_count, c.total_views), reverse=True)

    # Suggested titles use a fresh hook pool (not the user's own past hook
    # names) so they don't read as re-uploads; only the *format* is copied.
    hooks = DEFAULT_HOOKS

    def titles_for(name: str, offset: int) -> list[str]:
        spin = offset % len(hooks)
        rotated = hooks[spin:] + hooks[:spin]
        out: list[str] = []
        for hook in rotated:
            rendered = fmt.render(name, hook)
            if rendered not in out:
                out.append(rendered)
            if len(out) == 3:
                break
        return out

    scene_key = (scene or DEFAULT_SCENE).lower().strip()
    seed = SCENES.get(scene_key, [])
    seed_keys = {_normalize(n): n for n in seed}
    scene_label = scene_key.upper() if len(scene_key) <= 3 else scene_key.title()
    covered = set(agg)
    fresh_channel = not covered

    mkt_artists, mkt_pairs, mkt_display = _market_stats(market_titles)

    def name_of(key: str) -> str:
        slot = agg.get(key)
        if slot:
            return slot["display"]
        return seed_keys.get(key) or mkt_display.get(key) or key.title()

    # Name pairs other channels keep putting together that you haven't made.
    market_pairs_out: list[tuple] = []
    for (k1, k2), n in mkt_pairs.most_common():
        if n < 2:
            break
        if cooc.get((k1, k2), 0):
            continue
        market_pairs_out.append((name_of(k1), name_of(k2), n))
        if len(market_pairs_out) >= 12:
            break

    suggestions: list[Suggestion] = []
    used: set[str] = set()

    def add(suggestion: Suggestion, key: str) -> None:
        if key in used or key in covered:
            return
        used.add(key)
        suggestions.append(suggestion)

    # 1. pairs other producers post together that you haven't
    for name_a, name_b, n in market_pairs_out[:3]:
        combo = f"{name_a} x {name_b}"
        suggestions.append(
            Suggestion(
                combo,
                f"Other channels put {name_a} + {name_b} together in {n} recent uploads",
                titles_for(combo, len(suggestions)),
                kind="pair",
            )
        )
        used.update((_normalize(name_a), _normalize(name_b)))
        if len(suggestions) >= limit:
            break

    # 2. one pair idea from your own solo-only artists
    solo = [c for c in coverage if c.beat_count >= 1][:6]
    for i in range(len(solo)):
        done = False
        for j in range(i + 1, len(solo)):
            a, b = solo[i], solo[j]
            if cooc.get(tuple(sorted((_normalize(a.name), _normalize(b.name)))), 0):
                continue
            combo = f"{a.name} x {b.name}"
            suggestions.append(
                Suggestion(
                    combo,
                    f"You've made {a.name} and {b.name} beats separately, never together",
                    titles_for(combo, len(suggestions)),
                    kind="pair",
                )
            )
            done = True
            break
        if done:
            break

    # 3. artists trending in other channels' recent uploads, not in your catalog
    for key, n in mkt_artists.most_common():
        if len(suggestions) >= limit:
            break
        if n < 3 or key in covered or key in used:
            continue
        in_seed = key in seed_keys
        if in_seed:
            reason = f"{scene_label} artist in {n} recent uploads by others, not in yours"
        else:
            reason = f"In {n} recent uploads by others, not on the {scene_label} seed list"
        add(Suggestion(name_of(key), reason, titles_for(name_of(key), len(suggestions))), key)

    # 4. remaining seed artists with no beat yet, in seed order
    for name in seed:
        if len(suggestions) >= limit:
            break
        reason = (
            f"Popular {scene_label} artist"
            if fresh_channel
            else f"{scene_label} artist not in your catalog yet"
        )
        add(Suggestion(name, reason, titles_for(name, len(suggestions))), _normalize(name))

    # 5. seed artists you've only made one beat for
    for name in seed:
        if len(suggestions) >= limit:
            break
        key = _normalize(name)
        if key in used:
            continue
        slot = agg.get(key)
        if slot and slot["beats"] <= 1:
            used.add(key)
            suggestions.append(
                Suggestion(
                    name,
                    f"Only {slot['beats']} beat so far, room to double down",
                    titles_for(name, len(suggestions)),
                )
            )

    return SuggestionReport(
        coverage=coverage,
        suggestions=suggestions[:limit],
        title_format=fmt,
        parsed_count=sum(1 for p, _ in parsed_all if p is not None),
        total_titles=len(entries),
        scene=scene_key,
        market_scanned=len(market_titles),
        market_pairs=market_pairs_out,
        market_artists=[(mkt_display.get(k, k.title()), n) for k, n in mkt_artists.most_common(15)],
    )
