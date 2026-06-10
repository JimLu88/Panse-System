"""
Diagnostic: run dual OCR on a debug screenshot and print every detected span
with coordinates, confidence, and buyer-extract filtering results.
Output to UTF-8 file to avoid PowerShell 5.1 codepage garbling.
"""

import sys
import os
import io

# Ensure project root is on path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
from PIL import Image

from apps.core.ocr.engine_rapid import RapidOCREngine
from apps.core.ocr.preprocess import preprocess_chat_roi
from apps.core.ocr.buyer_extract import extract_buyer_message_block
from apps.core.ocr.models import OCRSpan

OUT_PATH = os.path.join(ROOT, "scripts", "diag_result.txt")


def main():
    img_path = os.path.join(
        ROOT,
        "dist", "data", "sqlite", "debug",
        "chat_audio_peak_20260523_013914.png",
    )
    if not os.path.isfile(img_path):
        print(f"Image not found: {img_path}")
        return

    buf = io.StringIO()
    def p(s=""):
        buf.write(s + "\n")

    img = Image.open(img_path).convert("RGB")
    rgb = np.array(img)
    h, w = rgb.shape[:2]
    p(f"Image size: {w} x {h}")
    p()

    # --- Raw OCR text_score=0.5 (production default) ---
    p("=" * 80)
    p("1) RapidOCR on RAW image, text_score=0.5 (production default)")
    p("=" * 80)
    engine_prod = RapidOCREngine(text_score=0.5, det_limit_side_len=1280)
    spans_prod = engine_prod.recognize(rgb)
    _print_spans(p, spans_prod, w, h)
    buyer_prod = extract_buyer_message_block(
        spans_prod, roi_height=h, roi_width=w,
        buyer_x_ratio=0.50, bottom_cutoff_ratio=0.58, bubble_gap_px=25.0,
    )
    p(f"  >>> buyer_extract result = '{buyer_prod}'")

    # --- Raw OCR text_score=0.3 ---
    p()
    p("=" * 80)
    p("2) RapidOCR on RAW image, text_score=0.3 (lower threshold)")
    p("=" * 80)
    engine_raw = RapidOCREngine(text_score=0.3, det_limit_side_len=1280)
    spans_raw = engine_raw.recognize(rgb)
    _print_spans(p, spans_raw, w, h)
    buyer_raw = extract_buyer_message_block(
        spans_raw, roi_height=h, roi_width=w,
        buyer_x_ratio=0.50, bottom_cutoff_ratio=0.58, bubble_gap_px=25.0,
    )
    p(f"  >>> buyer_extract result = '{buyer_raw}'")

    # --- Preprocessed ---
    p()
    p("=" * 80)
    p("3) RapidOCR on PREPROCESSED image (2x + contrast + sharpen), text_score=0.5")
    p("=" * 80)
    pre = preprocess_chat_roi(rgb, scale=2.0, contrast_cutoff=1,
                              sharpen_radius=1.0, sharpen_percent=150)
    engine_pre = RapidOCREngine(text_score=0.5, det_limit_side_len=2560)
    spans_pre = engine_pre.recognize(pre)
    spans_pre_scaled = []
    for s in spans_pre:
        x1, y1, x2, y2 = s.bbox
        spans_pre_scaled.append(OCRSpan(
            text=s.text, confidence=s.confidence,
            bbox=(x1 // 2, y1 // 2, x2 // 2, y2 // 2),
        ))
    _print_spans(p, spans_pre_scaled, w, h)
    buyer_pre = extract_buyer_message_block(
        spans_pre_scaled, roi_height=h, roi_width=w,
        buyer_x_ratio=0.50, bottom_cutoff_ratio=0.58, bubble_gap_px=25.0,
    )
    p(f"  >>> buyer_extract result = '{buyer_pre}'")

    # --- Analysis ---
    p()
    p("=" * 80)
    p("ANALYSIS: Why buyer_extract picks wrong text")
    p("=" * 80)
    p()
    # Find toolbar spans (y > 95% of image height)
    toolbar_y = h * 0.95
    for test_name, spans in [("production", spans_prod), ("raw_0.3", spans_raw)]:
        toolbar = [s for s in spans if s.bbox[1] > toolbar_y]
        msg_area = [s for s in spans if s.bbox[3] < toolbar_y
                    and (s.bbox[0] + s.bbox[2]) / 2.0 < w * 0.50
                    and (s.bbox[1] + s.bbox[3]) / 2.0 >= h * 0.58]
        p(f"  [{test_name}] Toolbar area spans (y1 > {toolbar_y:.0f}):")
        for s in toolbar:
            p(f"    text='{s.text}' conf={s.confidence:.3f} bbox={s.bbox}")
        p(f"  [{test_name}] Buyer-zone message spans (bottom 42%, left 50%):")
        for s in msg_area:
            p(f"    text='{s.text}' conf={s.confidence:.3f} bbox={s.bbox}")
        p()

    # Write results
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    print(f"Results written to: {OUT_PATH}")


def _print_spans(p, spans: list[OCRSpan], img_w: int, img_h: int):
    if not spans:
        p("  (no spans detected)")
        return
    p(f"  Total spans: {len(spans)}")
    p(f"  {'#':>3}  {'text':<30}  {'conf':>5}  {'x1':>4} {'y1':>4} {'x2':>4} {'y2':>4}  "
      f"{'cx/w':>5}  {'cy/h':>5}  zone")
    p("  " + "-" * 100)
    for i, s in enumerate(spans):
        x1, y1, x2, y2 = s.bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        cx_ratio = cx / img_w if img_w else 0
        cy_ratio = cy / img_h if img_h else 0
        is_buyer_side = cx_ratio < 0.50
        is_bottom = cy_ratio >= 0.58
        is_toolbar = y1 > img_h * 0.95
        if is_toolbar:
            marker = "TOOLBAR"
        elif is_buyer_side and is_bottom:
            marker = "BUYER"
        elif is_buyer_side:
            marker = "left"
        elif is_bottom:
            marker = "right-bottom"
        else:
            marker = "right"
        p(f"  {i:>3}  {s.text:<30}  {s.confidence:>5.3f}  "
          f"{x1:>4} {y1:>4} {x2:>4} {y2:>4}  "
          f"{cx_ratio:>5.2f}  {cy_ratio:>5.2f}  {marker}")


if __name__ == "__main__":
    main()
