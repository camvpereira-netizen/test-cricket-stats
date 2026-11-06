"""The site's Test cricket look: cream whites, a red ball, green outfield and a scoreboard.

Colours live in `.streamlit/config.toml` (Streamlit's own theme) and here (the extra touches
Streamlit can't theme: page banners, scoreboard metrics, headings). Text colours are chosen
for at least WCAG AA contrast (4.5:1) on the backgrounds they sit on; see tests/test_theme.py.
Fonts come from Google Fonts (free, Open Font License).
"""
from __future__ import annotations

import html

import streamlit as st

# Palette
WHITES = "#FBF8F0"       # page: cricket whites / cream
INK = "#1E2A22"          # body text
BALL = "#A6111C"         # the red ball: primary colour, links, buttons
OUTFIELD = "#1F6B3A"     # outfield green
OUTFIELD_DARK = "#174F2B"
BOARD = "#0E3420"        # scoreboard
BOARD_DIGITS = "#FFD34D"  # scoreboard numbers
BOARD_LABEL = "#E9F1E4"  # scoreboard labels
PITCH = "#D9C79A"        # the pitch strip in banners

FONTS = ("https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700"
         "&family=Share+Tech+Mono&display=swap")

# A red ball with its seam, drawn inline so no image file is needed
BALL_SVG = """<svg class="tc-ball" viewBox="0 0 100 100" aria-hidden="true">
<defs><radialGradient id="tcShine" cx="35%" cy="30%" r="75%">
<stop offset="0" stop-color="#E0473F"/><stop offset=".55" stop-color="#A6111C"/>
<stop offset="1" stop-color="#5E0A10"/></radialGradient></defs>
<circle cx="50" cy="50" r="46" fill="url(#tcShine)"/>
<path d="M30 9 C52 32 52 68 30 91" fill="none" stroke="#F6E7C8" stroke-width="2.2"/>
<path d="M38 6 C62 30 62 70 38 94" fill="none" stroke="#F6E7C8" stroke-width="2.2"/>
<path d="M30 9 C52 32 52 68 30 91" fill="none" stroke="#F6E7C8" stroke-width="5"
 stroke-dasharray="1.2 4.5" transform="translate(-3 0)"/>
<path d="M38 6 C62 30 62 70 38 94" fill="none" stroke="#F6E7C8" stroke-width="5"
 stroke-dasharray="1.2 4.5" transform="translate(3 0)"/>
</svg>"""

CSS = f"""
<style>
@import url('{FONTS}');

/* headings in a condensed, scoreboard-poster face */
h1, h2, h3, [data-testid="stHeading"] * {{
  font-family: 'Oswald', 'Arial Narrow', sans-serif !important;
  letter-spacing: .02em;
}}
h2, h3 {{ color: {OUTFIELD_DARK}; }}

/* a strip of mown outfield along the top of the page */
[data-testid="stHeader"] {{
  border-bottom: 4px solid {OUTFIELD};
  background-image: repeating-linear-gradient(90deg, rgba(31,107,58,.06) 0 40px, transparent 40px 80px);
}}

/* page banners: striped outfield, a pitch, and the red ball */
.tc-banner {{
  position: relative; overflow: hidden; border-radius: 14px; margin: .25rem 0 1.25rem;
  padding: 1.4rem 1.6rem 1.3rem 1.6rem; color: #FFFFFF;
  background:
    linear-gradient(180deg, rgba(0,0,0,0) 60%, rgba(0,0,0,.18) 100%),
    repeating-linear-gradient(90deg, {OUTFIELD} 0 56px, {OUTFIELD_DARK} 56px 112px);
  box-shadow: 0 2px 0 rgba(0,0,0,.08);
}}
.tc-banner::after {{  /* the pitch, with creases */
  content: ""; position: absolute; top: -10%; bottom: -10%; right: 120px; width: 46px;
  background: {PITCH}; opacity: .9; transform: skewX(-8deg);
  box-shadow: inset 0 14px 0 -11px #FFFFFF, inset 0 -14px 0 -11px #FFFFFF;
}}
.tc-banner h1 {{
  color: #FFFFFF !important; margin: 0 !important; padding: 0 !important;
  font-size: 2.3rem !important; line-height: 1.15 !important; position: relative; z-index: 1;
  text-shadow: 0 2px 0 rgba(0,0,0,.25); max-width: calc(100% - 150px);
}}
.tc-banner p {{
  margin: .35rem 0 0; font-size: 1.05rem; position: relative; z-index: 1;
  max-width: calc(100% - 150px); color: #F3F8EF;
}}
.tc-banner .tc-ball {{
  position: absolute; right: 22px; top: 50%; width: 92px; height: 92px; z-index: 2;
  transform: translateY(-50%) rotate(-18deg); filter: drop-shadow(0 4px 4px rgba(0,0,0,.35));
}}
.tc-banner.tc-big {{ padding: 2.2rem 1.8rem; }}
.tc-banner.tc-big h1 {{ font-size: 3rem !important; }}
.tc-banner.tc-big .tc-ball {{ width: 120px; height: 120px; animation: tc-spin 14s linear infinite; }}
@keyframes tc-spin {{ from {{ transform: translateY(-50%) rotate(0deg); }}
                      to {{ transform: translateY(-50%) rotate(360deg); }} }}
@media (prefers-reduced-motion: reduce) {{ .tc-banner .tc-ball {{ animation: none !important; }} }}
@media (max-width: 640px) {{
  .tc-banner::after {{ right: 70px; width: 30px; }}
  .tc-banner .tc-ball, .tc-banner.tc-big .tc-ball {{ width: 60px; height: 60px; right: 14px; }}
  .tc-banner h1, .tc-banner.tc-big h1 {{ font-size: 1.8rem !important; max-width: calc(100% - 90px); }}
  .tc-banner p {{ max-width: calc(100% - 90px); }}
}}

/* metrics as an old-fashioned scoreboard: dark green board, yellow digits */
[data-testid="stMetric"] {{
  background: {BOARD}; border-radius: 10px; padding: .7rem 1rem .8rem;
  border: 3px solid #2B5A3C; box-shadow: inset 0 0 0 1px rgba(255,255,255,.06);
}}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {{
  color: {BOARD_LABEL} !important; text-transform: uppercase; letter-spacing: .08em;
  font-size: .8rem !important; font-weight: 600;
}}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * {{
  color: {BOARD_DIGITS} !important; font-family: 'Share Tech Mono', ui-monospace, monospace !important;
  font-size: 2.1rem !important; line-height: 1.15;
}}

@media (max-width: 640px) {{
  [data-testid="stMetric"] {{ padding: .4rem .8rem .5rem; }}
  [data-testid="stMetricValue"], [data-testid="stMetricValue"] * {{ font-size: 1.6rem !important; }}
}}
[data-testid="stMainBlockContainer"] {{ padding-top: 4rem; }}

/* the player card above a player's tables */
.tc-card {{
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #FFFFFF; border: 1px solid #D8E2D2; border-left: 8px solid {BALL};
  border-radius: 12px; padding: .9rem 1.2rem; margin: .5rem 0 1rem;
}}
.tc-card .tc-name {{ font-family: 'Oswald', sans-serif; font-size: 1.7rem; font-weight: 600; color: {INK}; }}
.tc-card .tc-full {{ color: #4A5A4F; }}
.tc-chip {{
  display: inline-block; background: #E6F0E1; color: {OUTFIELD_DARK}; border-radius: 999px;
  padding: .15rem .65rem; margin: .1rem .3rem .1rem 0; font-size: .9rem; font-weight: 600;
}}
</style>
"""


def apply() -> None:
    """Add the theme's CSS to the page. Call once per run, after st.set_page_config."""
    st.markdown(CSS, unsafe_allow_html=True)


def banner(title: str, subtitle: str = "", big: bool = False) -> None:
    """A page heading on a striped outfield with the red ball. Text is escaped."""
    sub = f"<p>{html.escape(subtitle)}</p>" if subtitle else ""
    st.markdown(f'<div class="tc-banner{" tc-big" if big else ""}"><h1>{html.escape(title)}</h1>'
                f"{sub}{BALL_SVG}</div>", unsafe_allow_html=True)


def player_card(name: str, full_name: str | None, chips: list[str]) -> None:
    """The player's name, full name and a row of chips (teams, span), escaped."""
    full = f'<span class="tc-full">{html.escape(full_name)}</span>' if full_name else ""
    chip_html = "".join(f'<span class="tc-chip">{html.escape(c)}</span>' for c in chips)
    st.markdown(f'<div class="tc-card"><div><div class="tc-name">{html.escape(name)}</div>{full}'
                f"<div>{chip_html}</div></div></div>", unsafe_allow_html=True)
