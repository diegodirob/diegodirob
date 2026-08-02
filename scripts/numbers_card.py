#!/usr/bin/env python3
"""Generate the profile "numbers" card (dark/light SVG, one per GitHub theme):
contribution stats since 2025 plus language distribution across all owned
repositories, private included. Reads a GitHub token from METRICS_TOKEN,
GH_TOKEN or GITHUB_TOKEN. Writes assets/numbers-{dark,light}.svg."""

import datetime
import json
import os
import sys
import urllib.request

TOKEN = (
    os.environ.get("METRICS_TOKEN")
    or os.environ.get("GH_TOKEN")
    or os.environ.get("GITHUB_TOKEN")
)
if not TOKEN:
    print("No token available, skipping card generation.")
    sys.exit(0)

MONO = "SFMono-Regular, Consolas, 'Courier New', monospace"
SERIF = "Georgia, 'Times New Roman', serif"
STATS_FROM = datetime.date(2025, 1, 1)

# Simple Icons slug is `name.lower()` for most languages (see fetch_icon_path);
# this maps only the exceptions. Unknown slugs 404 and the row renders iconless.
ICON_SLUGS = {
    "HTML": "html5",
    "Shell": "gnubash",
    "Dockerfile": "docker",
    "Jupyter Notebook": "jupyter",
    "HCL": "terraform",
    "SCSS": "sass",
    "Vue": "vuedotjs",
}

LANG_QUERY = """
query($cursor: String) {
  viewer {
    repositories(ownerAffiliations: OWNER, isFork: false, first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
  }
}
"""

USERNAME = "diegodirob"


_icon_cache = {}


def graphql(query, variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    return payload["data"]


def collect_languages():
    totals = {}
    cursor = None
    while True:
        data = graphql(LANG_QUERY, {"cursor": cursor})
        repos = data["viewer"]["repositories"]
        for node in repos["nodes"]:
            for edge in node["languages"]["edges"]:
                name = edge["node"]["name"]
                totals[name] = totals.get(name, 0) + edge["size"]
        if not repos["pageInfo"]["hasNextPage"]:
            return totals
        cursor = repos["pageInfo"]["endCursor"]


def collect_days():
    """Daily contribution counts from STATS_FROM to today (UTC).

    Read from the public contribution-calendar fragment instead of GraphQL:
    with the "private contributions" profile toggle on it carries the full
    counts, while fine-grained tokens undercount the GraphQL calendar.
    """
    import re

    days = {}
    today = datetime.datetime.now(datetime.timezone.utc).date()
    for year in range(STATS_FROM.year, today.year + 1):
        url = (
            f"https://github.com/users/{USERNAME}/contributions"
            f"?from={year}-01-01&to={year}-12-31"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "numbers-card"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode()
        cells = dict(
            re.findall(r'data-date="(\d{4}-\d{2}-\d{2})"[^>]*\bid="([^"]+)"', html)
        )
        tips = dict(
            re.findall(
                r'<tool-tip[^>]*\bfor="([^"]+)"[^>]*>\s*(\d+|No) contribution', html
            )
        )
        for date_str, cell_id in cells.items():
            date = datetime.date.fromisoformat(date_str)
            if STATS_FROM <= date <= today and cell_id in tips:
                count = tips[cell_id]
                days[date] = 0 if count == "No" else int(count)
    expected = (today - STATS_FROM).days + 1
    if len(days) < expected * 0.9:
        raise RuntimeError(
            f"calendar parse looks broken: {len(days)} days found, {expected} expected"
        )
    return days, today


def fmt_date(date, with_year=False):
    label = f"{date.strftime('%b').upper()} {date.day}"
    return f"{label}, {date.year}" if with_year else label


def compute_stats(days, today):
    total = sum(days.values())
    active = sorted(d for d, c in days.items() if c > 0)
    if not active:
        return total, None, (0, None, None), (0, None, None)
    first = active[0]

    runs = []
    run_start = prev = active[0]
    for date in active[1:]:
        if (date - prev).days > 1:
            runs.append((run_start, prev))
            run_start = date
        prev = date
    runs.append((run_start, prev))

    longest = max(runs, key=lambda r: (r[1] - r[0]).days)
    longest_len = (longest[1] - longest[0]).days + 1

    top = sorted(runs, key=lambda r: (r[1] - r[0]).days, reverse=True)[:3]
    print("longest runs:", [(f"{a} to {b}", (b - a).days + 1) for a, b in top])

    current_len, current_range = 0, (None, None)
    last_start, last_end = runs[-1]
    if (today - last_end).days <= 1:  # unbroken through yesterday or today
        current_len = (last_end - last_start).days + 1
        current_range = (last_start, last_end)

    return total, first, (current_len, *current_range), (longest_len, *longest)


def monthly_series(days):
    """Contribution totals per month, chronological."""
    sums = {}
    for date, count in days.items():
        sums[(date.year, date.month)] = sums.get((date.year, date.month), 0) + count
    return [sums[key] for key in sorted(sums)]


def fetch_icon_path(name):
    """Return the Simple Icons path data for a language, or None."""
    slug = ICON_SLUGS.get(name, name.lower())
    if slug in _icon_cache:
        return _icon_cache[slug]
    try:
        req = urllib.request.Request(
            f"https://cdn.simpleicons.org/{slug}",
            headers={"User-Agent": "numbers-card"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            svg = resp.read().decode()
        start = svg.index(' d="') + 4
        path = svg[start:svg.index('"', start)]
    except Exception as exc:  # icon is decoration: never fail the card over it
        print(f"icon {slug}: skipped ({exc})")
        path = None
    _icon_cache[slug] = path
    return path


def xml_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def spark_path(series, x0, y0, w, h):
    """Sparkline geometry: polyline string, closed area polygon, point coords."""
    mx = max(series) or 1
    step = w / (len(series) - 1)
    coords = [
        (x0 + i * step, y0 + h - (v / mx) * h)
        for i, v in enumerate(series)
    ]
    line = " ".join(f"{px:.1f},{py:.1f}" for px, py in coords)
    area = f"{line} {x0 + w:.1f},{y0 + h} {x0},{y0 + h}"
    return line, area, coords


def render(stats_blocks, lang_rows, spark, dots_n, fg, glow, flame, gold, out_path):
    width = 1200
    lang_col_rows = (len(lang_rows) + 1) // 2
    lang_top = 222
    height = lang_top + lang_col_rows * 38 + 6
    ease = "calcMode='spline' keySplines='0.22 1 0.36 1' keyTimes='0;1' fill='freeze'"

    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' "
        f"role='img' aria-label='Contribution and language statistics, private repositories included'>",
        "<defs><filter id='soft' x='-80%' y='-80%' width='260%' height='260%'>"
        "<feGaussianBlur stdDeviation='40'/></filter>"
        f"<linearGradient id='sparkglow' x1='0' y1='0' x2='0' y2='1'>"
        f"<stop offset='0' stop-color='{flame}' stop-opacity='0.28'/>"
        f"<stop offset='1' stop-color='{flame}' stop-opacity='0'/>"
        f"</linearGradient>"
        f"<linearGradient id='sheen' x1='0' y1='0' x2='1' y2='0'>"
        f"<stop offset='0' stop-color='{gold}' stop-opacity='0'/>"
        f"<stop offset='0.5' stop-color='{gold}' stop-opacity='1'/>"
        f"<stop offset='1' stop-color='{gold}' stop-opacity='0'/>"
        f"</linearGradient>"
        "</defs>",
        f"<circle cx='1080' cy='30' r='90' fill='{glow}' opacity='0.05' filter='url(#soft)'>"
        "<animate attributeName='opacity' values='0.03;0.07;0.03' dur='11s' repeatCount='indefinite'/>"
        "</circle>",
    ]

    for i, (kicker, big, sub) in enumerate(stats_blocks):
        x = 80 + i * 380
        cx = x + 150
        t0 = 0.12 * i
        if i == 1:  # current streak: ring with komorebi flame, like the old card
            parts += [
                f"<text x='{cx}' y='30' text-anchor='middle' font-family=\"{MONO}\" font-size='11' "
                f"letter-spacing='3' fill='{fg}' opacity='0.45'>{xml_escape(kicker)}</text>",
                f"<g opacity='0'>"
                f"<animate attributeName='opacity' values='0;1' dur='0.6s' begin='{t0:.2f}s' {ease}/>"
                f"<circle cx='{cx}' cy='92' r='40' fill='none' stroke='{fg}' stroke-width='2.5' "
                f"stroke-opacity='0.9' stroke-linecap='round' stroke-dasharray='226 25' "
                f"transform='rotate(-72 {cx} 92)'/>"
                f"<circle cx='{cx}' cy='92' r='50' fill='none' stroke='{fg}' stroke-width='1' "
                f"stroke-opacity='0.12' stroke-dasharray='2 7'>"
                f"<animateTransform attributeName='transform' type='rotate' "
                f"values='0 {cx} 92;360 {cx} 92' dur='60s' repeatCount='indefinite'/>"
                f"</circle>"
                f"<g transform='translate({cx},50)'><path d='M0 -7 C3 -3.5 6 -1.5 6 2.5 "
                f"A6 6 0 1 1 -6 2.5 C-6 -1.5 -3 -3.5 0 -7 Z' fill='{flame}' opacity='0.9'>"
                f"<animate attributeName='opacity' values='0.5;1;0.5' dur='3s' repeatCount='indefinite'/>"
                f"</path></g>"
                f"<text x='{cx}' y='105' text-anchor='middle' font-family=\"{SERIF}\" font-size='38' "
                f"fill='{fg}' opacity='0.95'>{xml_escape(big)}</text>"
                f"<text x='{cx}' y='158' text-anchor='middle' font-family=\"{MONO}\" font-size='11' "
                f"letter-spacing='2' fill='{fg}' opacity='0.35'>{xml_escape(sub)}</text>"
                f"</g>",
            ]
            continue
        extra = ""
        if i == 0 and len(spark) > 1:  # contributions: monthly sparkline
            line, area, coords = spark_path(spark, x, 116, 300, 28)
            peak_x, peak_y = coords[spark.index(max(spark))]
            last_x, last_y = coords[-1]
            extra = (
                f"<clipPath id='wipe{i}'><rect x='{x}' y='108' width='0' height='42'>"
                f"<animate attributeName='width' values='0;300' dur='1.1s' begin='0.3s' {ease}/>"
                f"</rect></clipPath>"
                f"<line x1='{x}' y1='144' x2='{x + 300}' y2='144' stroke='{fg}' "
                f"stroke-opacity='0.12' stroke-dasharray='1 5'/>"
                f"<g clip-path='url(#wipe{i})'>"
                f"<polygon points='{area}' fill='url(#sparkglow)'/>"
                f"<polyline points='{line}' fill='none' stroke='{fg}' stroke-width='1.5' "
                f"stroke-opacity='0.6' stroke-linejoin='round' stroke-linecap='round'/>"
                f"<circle cx='{peak_x:.1f}' cy='{peak_y:.1f}' r='2' fill='{fg}' opacity='0.5'/>"
                f"</g>"
                f"<circle cx='{last_x:.1f}' cy='{last_y:.1f}' r='2.8' fill='{flame}' opacity='0'>"
                f"<animate attributeName='opacity' values='0;0.9' dur='0.4s' begin='1.4s' {ease}/>"
                f"<animate attributeName='r' values='2.8;4.4;2.8' dur='2.5s' begin='1.8s' repeatCount='indefinite'/>"
                f"<animate attributeName='opacity' values='0.9;0.55;0.9' dur='2.5s' begin='1.8s' repeatCount='indefinite'/>"
                f"</circle>"
            )
        if i == 2 and dots_n:  # longest streak: one dot per day
            shown = min(dots_n, 30)
            dots = []
            for d in range(shown):
                dx = x + d * 10.5
                color = flame if d == shown - 1 else fg
                op = "0.9" if d == shown - 1 else "0.7"
                wave_hi = float(op)
                wave_lo = round(wave_hi * 0.5, 2)
                dots.append(
                    f"<circle cx='{dx:.1f}' cy='130' r='3' fill='{color}' opacity='0'>"
                    f"<animate attributeName='opacity' values='0;{op}' dur='0.3s' "
                    f"begin='{0.5 + 0.035 * d:.2f}s' {ease}/>"
                    f"<animate attributeName='opacity' values='{wave_hi};{wave_lo};{wave_hi}' dur='4s' "
                    f"begin='{2.5 + 0.12 * d:.2f}s' repeatCount='indefinite'/></circle>"
                )
            extra = "".join(dots)
        parts += [
            f"<text x='{x}' y='42' font-family=\"{MONO}\" font-size='11' letter-spacing='3' "
            f"fill='{fg}' opacity='0.45'>{xml_escape(kicker)}</text>",
            f"<g opacity='0'>"
            f"<animate attributeName='opacity' values='0;1' dur='0.6s' begin='{t0:.2f}s' {ease}/>"
            f"<animateTransform attributeName='transform' type='translate' values='0 8;0 0' "
            f"dur='0.6s' begin='{t0:.2f}s' {ease}/>"
            f"<text x='{x}' y='100' font-family=\"{SERIF}\" font-size='54' "
            f"fill='{fg}' opacity='0.95'>{xml_escape(big)}</text>"
            f"{extra}"
            f"<text x='{x}' y='158' font-family=\"{MONO}\" font-size='11' letter-spacing='2' "
            f"fill='{fg}' opacity='0.35'>{xml_escape(sub)}</text>"
            f"</g>",
        ]

    parts += [
        f"<line x1='80' y1='176' x2='1120' y2='176' stroke='{fg}' stroke-opacity='0.15'/>",
        f"<text x='80' y='202' font-family=\"{MONO}\" font-size='11' letter-spacing='3' "
        f"fill='{fg}' opacity='0.45'>LANGUAGES · ALL REPOS, PRIVATE INCLUDED</text>",
    ]

    for i, (name, icon, share) in enumerate(lang_rows):
        col, row = divmod(i, lang_col_rows)
        x = 80 + col * 560
        y = lang_top + row * 38
        # bar length is the absolute share of the track, not relative to the top language
        bar_w = max(4, round(190 * share / 100.0))
        t_bar = 0.35 + 0.1 * i
        t_num = t_bar + 0.3
        if icon:
            parts.append(
                f"<g transform='translate({x},{y + 1}) scale(0.625)'>"
                f"<path d='{icon}' fill='{fg}' opacity='0.8'/></g>"
            )
        else:
            parts.append(
                f"<circle cx='{x + 7}' cy='{y + 9}' r='2.5' fill='{fg}' opacity='0.5'/>"
            )
        parts += [
            f"<text x='{x + 24}' y='{y + 14}' font-family=\"{MONO}\" font-size='13' "
            f"fill='{fg}' opacity='0.85'>{xml_escape(name)}</text>",
            f"<rect x='{x + 170}' y='{y + 5}' width='190' height='6' rx='3' fill='{fg}' opacity='0.1'/>",
            f"<rect x='{x + 170}' y='{y + 5}' width='0' height='6' rx='3' fill='{fg}' opacity='0.85'>"
            f"<animate attributeName='width' values='0;{bar_w}' dur='0.9s' begin='{t_bar:.2f}s' {ease}/>"
            f"</rect>",
            # komorebi sheen: a soft light band sweeps each bar, staggered per row
            f"<clipPath id='bc{i}'><rect x='{x + 170}' y='{y + 5}' width='{bar_w}' height='6' rx='3'/></clipPath>",
            f"<g clip-path='url(#bc{i})'><rect x='{x + 114}' y='{y + 5}' width='56' height='6' "
            f"fill='url(#sheen)' opacity='0.9'>"
            f"<animate attributeName='x' values='{x + 114};{x + 170 + bar_w};{x + 170 + bar_w}' "
            f"keyTimes='0;0.3;1' dur='5s' begin='{1.5 + 0.7 * i:.2f}s' repeatCount='indefinite'/>"
            f"</rect></g>",
            f"<text x='{x + 440}' y='{y + 15}' text-anchor='end' font-family=\"{SERIF}\" "
            f"font-style='italic' font-size='16' fill='{fg}' opacity='0'>{share:.1f}%"
            f"<animate attributeName='opacity' values='0;0.7' dur='0.5s' begin='{t_num:.2f}s' {ease}/>"
            f"</text>",
        ]
        if i == 0:  # top language: the warm "now" accent, same as the sparkline tip
            parts.append(
                f"<circle cx='{x + 170 + bar_w}' cy='{y + 8}' r='2.8' fill='{gold}' opacity='0'>"
                f"<animate attributeName='opacity' values='0;0.95' dur='0.4s' begin='1.5s' {ease}/>"
                f"<animate attributeName='r' values='2.8;3.6;2.8' dur='3s' begin='2s' repeatCount='indefinite'/>"
                f"</circle>"
            )

    parts.append("</svg>")
    with open(out_path, "w") as fh:
        fh.write("\n".join(parts) + "\n")
    print(f"wrote {out_path}")


def main():
    totals = collect_languages()
    grand = sum(totals.values())
    days, today = collect_days()
    total, first, current, longest = compute_stats(days, today)
    if not grand or not total:
        print("No data found, skipping.")
        return

    cur_len, cur_a, cur_b = current
    lon_len, lon_a, lon_b = longest
    stats_blocks = [
        (
            "CONTRIBUTIONS · SINCE 2025",
            f"{total:,}",
            f"{fmt_date(first, True)} · TODAY",
        ),
        (
            "CURRENT STREAK · DAYS",
            str(cur_len),
            f"{fmt_date(cur_a)} · {fmt_date(cur_b)}" if cur_a else "TAKING A BREATH",
        ),
        (
            "LONGEST STREAK · DAYS",
            str(lon_len),
            f"{fmt_date(lon_a)} · {fmt_date(lon_b)}" if lon_a else "",
        ),
    ]

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    rows = [(name, fetch_icon_path(name), size * 100.0 / grand) for name, size in ranked[:6]]
    rest = sum(size for _, size in ranked[6:])
    if rest:
        rows.append(("Other", None, rest * 100.0 / grand))

    spark = monthly_series(days)

    os.makedirs("assets", exist_ok=True)
    render(stats_blocks, rows, spark, longest[0], "#ffffff", "#f5edd8", "#f5edd8", "#c9a24b", "assets/numbers-dark.svg")
    render(stats_blocks, rows, spark, longest[0], "#0a0a0a", "#4a473c", "#b08d2e", "#b08d2e", "assets/numbers-light.svg")


if __name__ == "__main__":
    main()
