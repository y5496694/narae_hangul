import sys
import os
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {
    'hp': 'http://www.hancom.co.kr/hwpml/2011/paragraph',
    'hh': 'http://www.hancom.co.kr/hwpml/2011/head',
    'hc': 'http://www.hancom.co.kr/hwpml/2011/core',
    'hs': 'http://www.hancom.co.kr/hwpml/2011/section'
}

def extract_template_from_hwpx(hwpx_path, template_name, category="06_사용자_추출_템플릿"):
    hwpx_path = Path(hwpx_path).resolve()
    if not hwpx_path.exists():
        print(f"[오류] 파일을 찾을 수 없습니다: {hwpx_path}")
        return None

    output_dir = Path(__file__).parent.parent / "4_형식템플릿" / category
    output_dir.mkdir(parents=True, exist_ok=True)
    
    clean_name = template_name.replace(" ", "_")
    output_json = output_dir / f"{clean_name}.json"

    print(f"[*] HWPX 파일 분석 중: {hwpx_path.name}")
    
    with zipfile.ZipFile(hwpx_path, 'r') as zf:
        sec_xml = zf.read("Contents/section0.xml")
        root = ET.fromstring(sec_xml)

    blocks = []
    
    # Analyze paragraphs and tables
    for elem in root.iter():
        tag = elem.tag.split('}')[-1]
        
        # Paragraph
        if tag == 'p':
            texts = [t.text for t in elem.iter() if t.tag.endswith('t') and t.text]
            full_text = "".join(texts).strip()
            if full_text:
                blocks.append({
                    "type": "paragraph",
                    "text": full_text,
                    "template_placeholder": f"[{full_text[:15]}...]",
                    "paragraph_style": {"align": "LEFT"}
                })
        # Table
        elif tag == 'tbl':
            rows_count = len(elem.findall('.//hp:tr', NS))
            cols_count = len(elem.findall('.//hp:tc', NS)) // max(1, rows_count)
            blocks.append({
                "type": "table_reference",
                "description": f"표 스타일 구조 ({rows_count}행 x {cols_count}열)",
                "rows": rows_count,
                "cols": cols_count
            })

    template_data = {
        "schema": 1,
        "name": template_name,
        "source_file": hwpx_path.name,
        "extracted_at": "auto",
        "description": f"{hwpx_path.stem} 문서에서 추출된 사용자 정의 서식 템플릿",
        "page": {
            "width_mm": 210,
            "height_mm": 297,
            "margins_mm": [20, 20, 15, 15]
        },
        "extracted_blocks_sample": blocks[:20]
    }

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(template_data, f, ensure_ascii=False, indent=2)

    print(f"[성공] 템플릿이 나래팩에 등록되었습니다: {output_json.relative_to(output_dir.parent.parent)}")
    return output_json

if __name__ == "__main__":
    if len(sys.argv) > 2:
        extract_template_from_hwpx(sys.argv[1], sys.argv[2])
    else:
        print("사용법: python extract_template.py <HWPX경로> <템플릿이름> [카테고리명]")
