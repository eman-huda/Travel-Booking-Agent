"""Visual tokens and CSS for the dashboard."""

INK = "#172B3A"
SLATE = "#5F7280"
MIST = "#DDE4E8"
PAPER = "#FFFFFF"
HORIZON = "#2C6E8F"
CLEAR = "#2E7D5B"
AMBER = "#B7800A"
SIGNAL = "#B4423A"

STATUS_COLOURS = {
    "success": CLEAR, "completed": CLEAR, "recovered": CLEAR, "CONFIRMED": CLEAR,
    "failure": SIGNAL, "graceful_failure": SIGNAL, "error": SIGNAL, "failed": SIGNAL, "UNAVAILABLE": SIGNAL,
    "retrying": AMBER, "completed_with_warnings": AMBER, "partial": AMBER, "needs_clarification": AMBER,
    "skipped": SLATE, "info": SLATE, "N/A": SLATE, "started": SLATE,
}

STATUS_TEXT = {
    "completed": "Completed", "recovered": "Recovered from failure", "completed_with_warnings": "Completed with warnings",
    "graceful_failure": "Stopped safely", "needs_clarification": "Needs more detail", "error": "Internal error",
    "success": "Success", "failure": "Failed", "retrying": "Retrying", "skipped": "Skipped", "info": "Info",
}

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
html, body, [class*="css"], .stMarkdown, .stTextInput, .stSelectbox, button, input, textarea {{
  font-family: 'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
}}
code, pre, .stJson {{ font-family: 'IBM Plex Mono', ui-monospace, Menlo, monospace !important; }}
.block-container {{ padding-top: 3.6rem; max-width: 1240px; }}
h1, h2, h3 {{ color: {INK}; letter-spacing: -0.01em; }}

.tb-header {{ display:flex; justify-content:space-between; align-items:flex-end; gap:1rem; flex-wrap:wrap;
  border-bottom: 1px solid {MIST}; padding-bottom: .9rem; margin-bottom: 1.2rem; }}
.tb-title {{ font-size: 1.65rem; font-weight: 600; color: {INK}; margin: 0; line-height: 1.2; }}
.tb-sub {{ color: {SLATE}; font-size: .95rem; margin-top: .25rem; max-width: 62ch; }}
.tb-modes {{ display:flex; gap:.4rem; flex-wrap:wrap; }}

.badge {{ display:inline-block; padding: .18rem .6rem; border-radius: 999px; font-size: .78rem; font-weight: 500;
  border: 1px solid currentColor; background: {PAPER}; white-space: nowrap; }}
.badge.solid {{ color: #fff !important; border-color: transparent; }}

.pass {{ background:{PAPER}; border:1px solid {MIST}; border-radius: 14px; padding: 1.3rem 1.5rem; margin-bottom: 1rem; }}
.pass-route {{ display:flex; align-items:center; gap: 1rem; flex-wrap: wrap; }}
.pass-city {{ font-size: 2.1rem; font-weight: 600; color: {INK}; line-height: 1.1; }}
.pass-arrow {{ flex: 1; min-width: 60px; border-top: 2px dashed {MIST}; position: relative; height: 0; }}
.pass-arrow::after {{ content: "✈"; position:absolute; right:-2px; top:-14px; color:{HORIZON}; font-size: 1.1rem; }}
.pass-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: .9rem; margin-top: 1.1rem;
  border-top: 1px dashed {MIST}; padding-top: 1rem; }}
.pass-k {{ color:{SLATE}; font-size:.8rem; }}
.pass-v {{ color:{INK}; font-size:1.05rem; font-weight:500; }}

.card {{ background:{PAPER}; border:1px solid {MIST}; border-radius: 10px; padding: .9rem 1.1rem; margin-bottom:.7rem; }}
.card.selected {{ border: 2px solid {HORIZON}; box-shadow: 0 0 0 3px rgba(44,110,143,.08); }}
.card.muted {{ opacity: .55; }}
.card-top {{ display:flex; justify-content:space-between; gap: .8rem; align-items: baseline; flex-wrap: wrap; }}
.card-title {{ font-weight: 600; color: {INK}; font-size: 1.02rem; }}
.card-meta {{ color:{SLATE}; font-size: .86rem; margin-top: .2rem; }}
.price {{ font-weight:600; color:{INK}; font-size: 1.1rem; }}
.chips {{ margin-top:.45rem; display:flex; gap:.3rem; flex-wrap: wrap; }}
.chip {{ font-size:.75rem; padding:.1rem .5rem; border-radius: 6px; background:#EEF3F6; color:{INK}; }}
.legs {{ display:grid; grid-template-columns: 1fr 1fr; gap: .6rem; margin-top:.5rem; font-size:.9rem; color:{INK}; }}
.legs b {{ font-weight: 600; }}

.note {{ border-left: 3px solid {SLATE}; padding: .45rem .8rem; margin: .35rem 0; background:{PAPER}; border-radius: 0 8px 8px 0; font-size:.9rem; }}
.note.warning {{ border-color: {AMBER}; }}
.note.error {{ border-color: {SIGNAL}; }}
.note.info {{ border-color: {HORIZON}; }}

.answer {{ background:{PAPER}; border:1px solid {MIST}; border-radius: 10px; padding: 1rem 1.2rem; line-height:1.6;
  white-space: pre-wrap; color:{INK}; max-width: 80ch; }}

.day {{ display:grid; grid-template-columns: 150px 1fr; gap: 1rem; padding: 1rem 0; border-top: 1px solid {MIST}; }}
.day-label {{ color:{INK}; font-weight:600; }}
.day-date {{ color:{SLATE}; font-size:.85rem; }}
.day-weather {{ color:{SLATE}; font-size:.8rem; margin-top:.3rem; }}
.slot {{ display:grid; grid-template-columns: 56px 22px 1fr; gap:.5rem; padding:.22rem 0; align-items: baseline; }}
.slot-time {{ font-variant-numeric: tabular-nums; color:{SLATE}; font-size:.88rem; }}
.slot-what {{ color:{INK}; }}
.slot-note {{ color:{SLATE}; font-size:.8rem; }}

.board {{ background:{INK}; border-radius: 12px; padding: .8rem 1rem; color:#E8EEF2; }}
.board-row {{ display:grid; grid-template-columns: 34px 1fr auto; gap:.7rem; align-items:center; padding:.42rem .2rem;
  border-bottom: 1px solid rgba(255,255,255,.08); }}
.board-row:last-child {{ border-bottom: none; }}
.stamp {{ width: 26px; height: 26px; border-radius: 50%; display:flex; align-items:center; justify-content:center;
  font-weight:600; font-size:.85rem; color:#fff; }}
.board-name {{ font-weight:500; }}
.board-msg {{ color:#AFC0CB; font-size:.82rem; }}
.board-time {{ font-family:'IBM Plex Mono', monospace; color:#AFC0CB; font-size:.8rem; }}
.board-sub {{ padding-left: 2.6rem; color:#F2C46B; font-size:.82rem; padding-bottom:.35rem; }}

.kv {{ display:grid; grid-template-columns: 190px 1fr; gap: .35rem .8rem; font-size: .92rem; }}
.kv .k {{ color:{SLATE}; }}
.kv .v {{ color:{INK}; }}
.sandbox {{ border: 1.5px dashed {AMBER}; color: {AMBER}; border-radius: 8px; padding: .35rem .7rem; font-weight:600;
  font-size: .8rem; display:inline-block; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
"""
