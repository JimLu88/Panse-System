"""Verify: DualOCREngine now returns spans in original coords, buyer_extract gets correct text."""

import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
from PIL import Image

from apps.core.ocr.dual_engine import DualOCREngine
from apps.core.ocr.buyer_extract import extract_buyer_message_block
from apps.core.ai.input_quality_gate import check_buyer_input


def main():
    img_path = os.path.join(
        ROOT, "dist", "data", "sqlite", "debug",
        "chat_audio_peak_20260523_013914.png",
    )
    img = Image.open(img_path).convert("RGB")
    rgb = np.array(img)
    h, w = rgb.shape[:2]

    engine = DualOCREngine()
    result = engine.recognize(rgb)

    out = []
    out.append(f"Image: {w}x{h}")
    out.append(f"Engine: {result.engine}, avg_conf: {result.avg_confidence:.3f}")
    out.append(f"Total spans: {len(result.spans)}")
    out.append("")

    # Print all spans with zone classification
    out.append(f"{'#':>3}  {'text':<30}  {'conf':>5}  {'x1':>4} {'y1':>4} {'x2':>4} {'y2':>4}  "
               f"{'cx/w':>5}  {'cy/h':>5}  zone")
    out.append("-" * 100)
    for i, s in enumerate(result.spans):
        x1, y1, x2, y2 = s.bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        cx_ratio = cx / w if w else 0
        cy_ratio = cy / h if h else 0
        is_buyer = cx_ratio < 0.50
        is_bottom = cy_ratio >= 0.58
        is_toolbar = y1 > h * 0.95
        if is_toolbar:
            zone = "TOOLBAR"
        elif is_buyer and is_bottom:
            zone = "BUYER"
        elif is_buyer:
            zone = "left"
        else:
            zone = "right"
        out.append(f"{i:>3}  {s.text:<30}  {s.confidence:>5.3f}  "
                   f"{x1:>4} {y1:>4} {x2:>4} {y2:>4}  "
                   f"{cx_ratio:>5.2f}  {cy_ratio:>5.2f}  {zone}")

    out.append("")

    # buyer_extract with toolbar exclusion
    buyer_text = extract_buyer_message_block(
        result.spans, roi_height=h, roi_width=w,
    )
    gate = check_buyer_input(buyer_text)

    out.append(f"buyer_extract result: '{buyer_text}'")
    out.append(f"gate: action={gate.action}, rule={gate.rule_name}")
    out.append("")

    if "多久发货" in buyer_text and gate.action == "pass":
        out.append("SUCCESS: OCR correctly returns '多久发货' and gate passes it!")
    elif "多久发货" in buyer_text:
        out.append(f"PARTIAL: '多久发货' found but gate={gate.action} ({gate.rule_name})")
    else:
        out.append(f"FAIL: buyer_text='{buyer_text}', expected '多久发货'")

    text = "\n".join(out)
    result_path = os.path.join(ROOT, "scripts", "diag_verify_result.txt")
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(result_path)


if __name__ == "__main__":
    main()
